"""Before/after report for one deep-research fact-check cycle: SQL skills vs MCP (#643).

Two read-only halves, so the numbers in
``docs/portfolio/mcp/results/<date>-before-after.md`` come from a script run,
never from estimates (runbook: ``docs/portfolio/mcp/runbook.md``):

``measure`` (needs the database; run where the prod DB is reachable, i.e. in
``pic-backend`` on the droplet) dumps, as JSON on stdout, everything the cycle
left in the database between ``--since`` and ``--until``:

* the ``fact_check_claims`` rows resolved by deep research in the window
  (``checked_via = 'DEEP_RESEARCH'``, ``checked_at`` in the window), and
* the ``mcp_tool_calls`` audit rows in the window (optionally one token's).

It opens one ``SET TRANSACTION READ ONLY`` session and writes nothing.

``render`` (stdlib only, no database; run from ``backend/`` on the host, where
the skill files exist) turns that dump plus the operator's timing stamps and the
two batches' prompt files into the markdown report:

* **steps** — the step headings of each path, read from the skill files: the
  ``## Procedure (MCP`` sections of ``--skill`` are the new way; the old way is
  the ``--old-skill`` files (the pre-#643 skills, ``git show 2390a32:<path>``,
  which have neither marker and count whole) or, without them, the
  ``## Fallback`` sections of ``--skill``.
* **SQL embedded in the old prompts** — the statements inside ``psql`` code
  blocks of the old sections, by kind and table; a statement keyed on one
  ``<ID>`` placeholder is run once per claim, any other once per run, so the
  executed count is derived as ``once + per_claim * claims written``.
* **audited tool calls** for the new way — name, outcome, error class and
  latency, straight from ``mcp_tool_calls``.
* **wall time** of each phase (generate the prompt; process the verdicts) from
  the timing stamps — the deep-research run itself is the operator's and is
  excluded from both sides.
* **failures** — batch claims not written in their process window, and tool
  calls whose outcome is not ``OK``.

Timings file: one ``key=<ISO-8601 UTC>`` per line (``#`` comments allowed) with
the keys in ``PHASES`` (``before_generate_start`` ... ``after_process_end``).

Usage:
    # 1. On the droplet (read-only), from the ~/pic host shell:
    docker exec pic-backend python -m scripts.mcp_before_after measure \\
        --since 2026-10-13T14:00:00Z --until 2026-10-13T20:00:00Z \\
        --token-name operator-laptop-2 > measurements.json

    # 2. Locally, from backend/ (no database, no third-party imports):
    python3 -m scripts.mcp_before_after render \\
        --measurements measurements.json --timings timings.env \\
        --before-prompt ../ai_generated_content/fact_check_prompts/processed/fact_check_A_prompt.md \\
        --after-prompt ../ai_generated_content/fact_check_prompts/processed/fact_check_B_prompt.md \\
        --skill ../.claude/skills/fact-check-prompt/SKILL.md \\
        --skill ../.claude/skills/fact-check-process/SKILL.md \\
        --old-skill /tmp/pre-mcp/fact-check-prompt.md \\
        --old-skill /tmp/pre-mcp/fact-check-process.md \\
        --out ../docs/portfolio/mcp/results/2026-10-13-before-after.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# --- shared --------------------------------------------------------------------------

PHASES = (
    "before_generate",
    "before_process",
    "after_generate",
    "after_process",
)

OLD_SECTION = "## Fallback"
NEW_SECTION = "## Procedure (MCP"

_SQL_KINDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")
# Keywords the FROM/INTO/UPDATE table regex can land on (``DO UPDATE SET``).
_SQL_RESERVED = {"SET", "SELECT", "VALUES", "ONLY", "LATERAL"}
_CLAIM_LINE = re.compile(r"^CLAIM (\d+) \(", re.MULTILINE)
_FENCE = re.compile(r"^\s*```")


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; it must carry a timezone (``Z`` or offset)."""
    ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return ts


# --- render: pure parsing ------------------------------------------------------------


def parse_timings(text: str) -> dict[str, datetime]:
    """Parse ``key=<ISO timestamp>`` lines; later lines override earlier ones."""
    out: dict[str, datetime] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"not a key=value line: {line!r}")
        out[key.strip()] = parse_ts(value)
    return out


def phase_window(timings: dict[str, datetime], phase: str) -> tuple[datetime, datetime] | None:
    """``(start, end)`` of a phase, or None when either stamp is missing."""
    start = timings.get(f"{phase}_start")
    end = timings.get(f"{phase}_end")
    if start is None or end is None:
        return None
    if end < start:
        raise ValueError(f"{phase}_end is before {phase}_start")
    return start, end


def prompt_claim_ids(prompt_text: str) -> list[int]:
    """The claim ids a deep-research prompt lists (its ``CLAIM <id> (`` lines)."""
    return [int(m) for m in _CLAIM_LINE.findall(prompt_text)]


def _unfenced_lines(text: str) -> list[tuple[str, bool]]:
    """Each line with a flag saying whether it sits inside a fenced code block."""
    out: list[tuple[str, bool]] = []
    inside = False
    for line in text.splitlines():
        if _FENCE.match(line):
            out.append((line, True))
            inside = not inside
            continue
        out.append((line, inside))
    return out


def extract_section(text: str, heading_prefix: str) -> str | None:
    """The ``## `` section whose heading starts with ``heading_prefix``.

    Headings inside fenced code blocks (the prompt template's ``## Verdicts``)
    are ignored. Returns None when no such section exists.
    """
    lines = _unfenced_lines(text)
    start = None
    for i, (line, fenced) in enumerate(lines):
        if not fenced and line.startswith(heading_prefix):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line, fenced = lines[j]
        if not fenced and line.startswith("## "):
            end = j
            break
    return "\n".join(line for line, _ in lines[start:end])


def old_and_new_sections(text: str) -> tuple[str, str]:
    """``(old, new)`` parts of a skill file; a file without markers is all-old."""
    old = extract_section(text, OLD_SECTION)
    new = extract_section(text, NEW_SECTION)
    if old is None and new is None:
        return text, ""
    return old or "", new or ""


def step_headings(section: str) -> list[str]:
    """The ``###`` / ``####`` headings of a section, outside code fences."""
    return [
        line.lstrip("#").strip()
        for line, fenced in _unfenced_lines(section)
        if not fenced and re.match(r"^#{3,4} ", line)
    ]


def _code_blocks(section: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in section.splitlines():
        if _FENCE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def _sql_texts(block: str) -> list[str]:
    """The SQL a ``psql`` shell block runs: ``-c "..."`` and heredoc bodies."""
    texts = re.findall(r"-c\s+\"(.*?)\"", block, flags=re.DOTALL)
    texts += re.findall(r"<<-?'?(\w+)'?[^\n]*\n(.*?)\n\1\b", block, flags=re.DOTALL)
    return [t if isinstance(t, str) else t[1] for t in texts]


def sql_statements(section: str) -> list[dict[str, Any]]:
    """Every SQL statement in the section's ``psql`` code blocks.

    Each is ``{"kind", "tables", "per_claim"}``: ``per_claim`` is True when the
    statement is keyed on a single ``<ID>`` placeholder, i.e. the skill runs it
    once per claim.
    """
    out: list[dict[str, Any]] = []
    for block in _code_blocks(section):
        if "psql" not in block:
            continue
        for sql in _sql_texts(block):
            # Drop SQL line comments before splitting, so a ';' in one is harmless.
            body = re.sub(r"--[^\n]*", "", sql)
            for stmt in body.split(";"):
                words = stmt.split()
                if not words or words[0].upper() not in _SQL_KINDS:
                    continue
                tables = sorted(
                    {
                        t
                        for t in re.findall(
                            r"\b(?:FROM|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", stmt, re.I
                        )
                        if t.upper() not in _SQL_RESERVED
                    }
                )
                out.append(
                    {
                        "kind": words[0].upper(),
                        "tables": tables,
                        "per_claim": bool(re.search(r"=\s*<ID>", stmt)),
                    }
                )
    return out


def mcp_tools_named(section: str) -> list[str]:
    """MCP tools a section tells the agent to call (``mcp__pic__<tool>``)."""
    seen: list[str] = []
    for name in re.findall(r"mcp__pic__(\w+)", section):
        if name not in seen:
            seen.append(name)
    return seen


# --- render: analysis -------------------------------------------------------------


def _in(ts: str, window: tuple[datetime, datetime] | None) -> bool:
    if window is None:
        return False
    when = parse_ts(ts)
    return window[0] <= when <= window[1]


def _seconds(window: tuple[datetime, datetime] | None) -> float | None:
    return None if window is None else round((window[1] - window[0]).total_seconds(), 1)


def batch_outcome(
    claim_ids: list[int],
    claims: list[dict[str, Any]],
    window: tuple[datetime, datetime] | None,
) -> dict[str, Any]:
    """What happened to one batch's claims inside its process window."""
    wanted = set(claim_ids)
    written = [c for c in claims if c["id"] in wanted and _in(c["checked_at"], window)]
    written_ids = {c["id"] for c in written}
    by_verdict: dict[str, int] = {}
    for c in written:
        by_verdict[c["verdict"]] = by_verdict.get(c["verdict"], 0) + 1
    return {
        "batch_size": len(claim_ids),
        "written": len(written),
        "by_verdict": dict(sorted(by_verdict.items())),
        "not_written": sorted(wanted - written_ids),
    }


def analyse(
    measurements: dict[str, Any],
    timings: dict[str, datetime],
    before_ids: list[int],
    after_ids: list[int],
    skills: dict[str, str],
    old_skills: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Everything the report shows, as data (the markdown is a view of this).

    ``skills`` are the current skill files: their ``## Procedure (MCP`` sections
    are the new way, and their ``## Fallback`` sections the old way unless
    ``old_skills`` (whole pre-MCP files, e.g. from ``git show``) is given.
    """
    windows = {phase: phase_window(timings, phase) for phase in PHASES}
    claims = measurements.get("claims", [])

    old_steps: list[str] = []
    new_steps: list[str] = []
    statements: list[dict[str, Any]] = []
    tools_named: list[str] = []
    for name, text in skills.items():
        old, new = old_and_new_sections(text)
        new_steps += [f"{name}: {h}" for h in step_headings(new)]
        tools_named += [t for t in mcp_tools_named(new) if t not in tools_named]
        if old_skills is None:
            old_steps += [f"{name}: {h}" for h in step_headings(old)]
            statements += [dict(s, skill=name) for s in sql_statements(old)]
    for name, text in (old_skills or {}).items():
        old, _new = old_and_new_sections(text)
        old_steps += [f"{name}: {h}" for h in step_headings(old)]
        statements += [dict(s, skill=name) for s in sql_statements(old)]

    before = batch_outcome(before_ids, claims, windows["before_process"])
    after = batch_outcome(after_ids, claims, windows["after_process"])

    once = sum(1 for s in statements if not s["per_claim"])
    per_claim = sum(1 for s in statements if s["per_claim"])

    after_span = None
    if windows["after_generate"] and windows["after_process"]:
        after_span = (windows["after_generate"][0], windows["after_process"][1])
    calls = [c for c in measurements.get("tool_calls", []) if _in(c["created_at"], after_span)]
    calls.sort(key=lambda c: c["created_at"])
    call_failures = [c for c in calls if c["outcome"] != "OK"]

    def total(*phases: str) -> float | None:
        parts = [_seconds(windows[p]) for p in phases]
        return None if any(p is None for p in parts) else round(sum(parts), 1)

    return {
        "windows": {p: _seconds(w) for p, w in windows.items()},
        "wall_before": total("before_generate", "before_process"),
        "wall_after": total("after_generate", "after_process"),
        "old_steps": old_steps,
        "new_steps": new_steps,
        "sql_statements": statements,
        "sql_once": once,
        "sql_per_claim": per_claim,
        "sql_executed_before": once + per_claim * before["written"],
        "tools_named": tools_named,
        "before": before,
        "after": after,
        "tool_calls": calls,
        "tool_call_failures": len(call_failures),
    }


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def render_markdown(result: dict[str, Any], meta: dict[str, str]) -> str:
    """The results page. ``meta`` carries labels only (date, batch ids, files)."""
    b, a = result["before"], result["after"]
    w = result["windows"]
    lines = [
        f"# Fact-check cycle before/after: SQL skills vs MCP tools ({meta.get('date', '')})",
        "",
        "Generated by `python -m scripts.mcp_before_after render` (#643) from a read-only",
        "`measure` dump of the production database and the operator's timing stamps.",
        "Deep-research time (the operator's Claude UI run) is excluded from both columns.",
        "",
        f"- Before batch: `{meta.get('before_batch', '?')}` ({b['batch_size']} claims), "
        "SQL fallback of the skills",
        f"- After batch: `{meta.get('after_batch', '?')}` ({a['batch_size']} claims), MCP tools",
        f"- Measurement window: {meta.get('since', '?')} to {meta.get('until', '?')}",
        "",
        "## Summary",
        "",
        "| | Before (SQL skills) | After (MCP tools) |",
        "|---|---|---|",
        f"| Steps in the skill procedure | {len(result['old_steps'])} | "
        f"{len(result['new_steps'])} |",
        f"| Prod SQL statements embedded in the prompts | {len(result['sql_statements'])} | 0 |",
        f"| SQL statements executed for this batch (derived) | "
        f"{result['sql_executed_before']} | 0 |",
        f"| MCP tools named in the prompts | 0 | {len(result['tools_named'])} |",
        f"| Audited tool calls | 0 | {len(result['tool_calls'])} |",
        f"| Wall time, generate prompt (s) | {_fmt(w['before_generate'])} | "
        f"{_fmt(w['after_generate'])} |",
        f"| Wall time, process verdicts (s) | {_fmt(w['before_process'])} | "
        f"{_fmt(w['after_process'])} |",
        f"| Wall time, total (s) | {_fmt(result['wall_before'])} | {_fmt(result['wall_after'])} |",
        f"| Claims written | {b['written']} / {b['batch_size']} | "
        f"{a['written']} / {a['batch_size']} |",
        f"| Claims not written | {len(b['not_written'])} | {len(a['not_written'])} |",
        f"| Failed tool calls (outcome not OK) | n/a | {result['tool_call_failures']} |",
        "",
        "Executed SQL is derived from the skill text: statements keyed on one `<ID>`",
        f"run once per claim ({result['sql_per_claim']}), the rest once per run "
        f"({result['sql_once']}), times the {b['written']} claims written.",
        "",
        "## Verdicts written",
        "",
        "| Verdict | Before | After |",
        "|---|---|---|",
    ]
    for verdict in sorted(set(b["by_verdict"]) | set(a["by_verdict"])):
        lines.append(
            f"| {verdict} | {b['by_verdict'].get(verdict, 0)} | {a['by_verdict'].get(verdict, 0)} |"
        )
    lines += [
        "",
        f"Not written, before: {b['not_written'] or 'none'}; after: {a['not_written'] or 'none'}.",
        "",
        "## Before: SQL statements embedded in the skill prompts",
        "",
        "| Skill | Kind | Tables | Runs |",
        "|---|---|---|---|",
    ]
    for s in result["sql_statements"]:
        runs = "once per claim" if s["per_claim"] else "once per run"
        lines.append(f"| {s['skill']} | {s['kind']} | {', '.join(s['tables'])} | {runs} |")
    lines += [
        "",
        "## After: audited MCP tool calls (`mcp_tool_calls`)",
        "",
        "| At (UTC) | Token | Tool | Outcome | Error class | Latency (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for c in result["tool_calls"]:
        lines.append(
            f"| {c['created_at']} | {c.get('token_name') or ''} | {c['tool']} | "
            f"{c['outcome']} | {c.get('error_class') or ''} | {c['duration_ms']} |"
        )
    if not result["tool_calls"]:
        lines.append("| (none in the after window) | | | | | |")
    lines += ["", "## Steps", "", "Before (SQL fallback sections):", ""]
    lines += [f"1. {s}" for s in result["old_steps"]] or ["(none)"]
    lines += ["", "After (MCP procedure sections):", ""]
    lines += [f"1. {s}" for s in result["new_steps"]] or ["(none)"]
    lines.append("")
    return "\n".join(lines)


# --- measure (database, read-only) --------------------------------------------------


def measure(
    db: Any,
    since: datetime,
    until: datetime,
    token_name: str | None = None,
) -> dict[str, Any]:
    """Read the claims resolved by deep research and the MCP calls in a window."""
    from app.models.fact_check import FactCheckClaim
    from app.models.mcp_tool_call import McpToolCall

    claims = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.checked_via == "DEEP_RESEARCH",
            FactCheckClaim.checked_at >= since,
            FactCheckClaim.checked_at <= until,
        )
        .order_by(FactCheckClaim.checked_at, FactCheckClaim.id)
        .all()
    )
    calls_q = db.query(McpToolCall).filter(
        McpToolCall.created_at >= since, McpToolCall.created_at <= until
    )
    if token_name:
        calls_q = calls_q.filter(McpToolCall.token_name == token_name)
    calls = calls_q.order_by(McpToolCall.created_at, McpToolCall.id).all()
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "token_name": token_name,
        "claims": [
            {
                "id": c.id,
                "subject_type": c.subject_type,
                "subject_id": c.subject_id,
                "verdict": c.verdict,
                "checked_at": c.checked_at.isoformat(),
            }
            for c in claims
        ],
        "tool_calls": [
            {
                "id": c.id,
                "created_at": c.created_at.isoformat(),
                "token_name": c.token_name,
                "tool": c.tool,
                "required_scope": c.required_scope,
                "outcome": c.outcome,
                "error_class": c.error_class,
                "duration_ms": c.duration_ms,
            }
            for c in calls
        ],
    }


def _run_measure(args: argparse.Namespace) -> int:
    from app.core.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        data = measure(db, parse_ts(args.since), parse_ts(args.until), args.token_name)
    finally:
        db.rollback()
        db.close()
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _skill_name(path: str) -> str:
    """``fact-check-process`` for ``.../fact-check-process/SKILL.md``, else the stem."""
    p = Path(path)
    return p.parent.name if p.name == "SKILL.md" and p.parent.name else p.stem


def _batch_label(path: Path) -> str:
    m = re.match(r"fact_check_(.+)_prompt\.md$", path.name)
    return m.group(1) if m else path.name


def _run_render(args: argparse.Namespace) -> int:
    measurements = json.loads(Path(args.measurements).read_text())
    timings = parse_timings(Path(args.timings).read_text())
    before_prompt = Path(args.before_prompt)
    after_prompt = Path(args.after_prompt)
    before_ids = prompt_claim_ids(before_prompt.read_text())
    after_ids = prompt_claim_ids(after_prompt.read_text())
    overlap = set(before_ids) & set(after_ids)
    if overlap:
        print(
            f"error: the two batches share claims {sorted(overlap)}; they must be disjoint",
            file=sys.stderr,
        )
        return 2
    skills = {_skill_name(p): Path(p).read_text() for p in args.skill}
    old_skills = (
        {_skill_name(p): Path(p).read_text() for p in args.old_skill} if args.old_skill else None
    )
    result = analyse(measurements, timings, before_ids, after_ids, skills, old_skills)
    meta = {
        "date": args.date or measurements.get("since", "")[:10],
        "before_batch": _batch_label(before_prompt),
        "after_batch": _batch_label(after_prompt),
        "since": measurements.get("since", "?"),
        "until": measurements.get("until", "?"),
    }
    page = render_markdown(result, meta)
    if args.out:
        Path(args.out).write_text(page)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(page)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="read-only DB dump of the cycle window (JSON)")
    m.add_argument("--since", required=True, help="ISO-8601 with timezone")
    m.add_argument("--until", required=True, help="ISO-8601 with timezone")
    m.add_argument("--token-name", help="only this MCP token's calls")

    r = sub.add_parser("render", help="markdown report from a measure dump (no DB)")
    r.add_argument("--measurements", required=True)
    r.add_argument("--timings", required=True)
    r.add_argument("--before-prompt", required=True)
    r.add_argument("--after-prompt", required=True)
    r.add_argument("--skill", action="append", required=True, help="current SKILL.md, repeatable")
    r.add_argument(
        "--old-skill",
        action="append",
        help="pre-MCP skill file (e.g. from git show), repeatable; default: --skill fallbacks",
    )
    r.add_argument("--date", help="label for the title (default: the window's date)")
    r.add_argument("--out", help="write here instead of stdout")

    args = parser.parse_args(argv)
    if args.command == "measure":
        return _run_measure(args)
    return _run_render(args)


if __name__ == "__main__":
    sys.exit(main())
