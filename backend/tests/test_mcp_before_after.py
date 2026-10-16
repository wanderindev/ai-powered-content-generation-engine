"""Tests for the #643 before/after harness (``scripts.mcp_before_after``)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.mcp.auth import hash_token
from app.models.fact_check import FactCheckClaim
from app.models.mcp_token import McpToken
from app.models.mcp_tool_call import McpToolCall
from scripts import mcp_before_after as harness

SKILL = """---
name: fact-check-process
---

# Title

## Trigger

`/x`

## Procedure (MCP — default)

### 1. Scan

#### a. Submit

Call `mcp__pic__submit_fact_check_verdicts` and then `mcp__pic__check_claim`,
then `mcp__pic__submit_fact_check_verdicts` again.

---

## Fallback: Direct SQL (pre-MCP path)

#### SQL c. Validate

```bash
psql "$DATABASE_URL" -t -A -F '|' \\
  -c "SELECT id, verdict FROM fact_check_claims WHERE id IN (<ids>);"
```

#### SQL f. Write

```bash
cat <<'SQLEOF' | psql "$DATABASE_URL"
UPDATE fact_check_claims
SET verdict = '<VERDICT>',  -- a comment; with a semicolon
    checked_at = NOW()
WHERE id = <ID> AND verdict = 'NEEDS_DEEP_RESEARCH';
SQLEOF
```

```bash
cat <<'SQLEOF' | psql "$DATABASE_URL"
INSERT INTO verified_claims (claim_key)
SELECT c.claim_text FROM fact_check_claims c WHERE c.id = <ID>
ON CONFLICT (claim_key) DO UPDATE SET claim_key = EXCLUDED.claim_key;
SQLEOF
```

```
## Verdicts
VERDICT: 1 | VERIFIED | - | x
```

Not SQL: `ls ai_generated_content/*.md`.
"""

PRE_MCP_SKILL = """# Old

## Procedure

### 1. Collect

```bash
psql "$DATABASE_URL" -c "SELECT id FROM fact_check_claims;"
```

### 2. Save
"""


def _ts(minutes: int) -> datetime:
    return datetime(2026, 10, 13, 14, 0, tzinfo=UTC) + timedelta(minutes=minutes)


TIMINGS = "\n".join(
    [
        "# stamps",
        "before_generate_start=2026-10-13T14:00:00Z",
        "before_generate_end=2026-10-13T14:02:00Z",
        "before_process_start=2026-10-13T15:00:00Z",
        "before_process_end=2026-10-13T15:05:00Z",
        "after_generate_start=2026-10-13T16:00:00Z",
        "after_generate_end=2026-10-13T16:00:30Z",
        "after_process_start=2026-10-13T17:00:00Z",
        "after_process_end=2026-10-13T17:01:00.500Z",
    ]
)


def _claim(cid, verdict, minutes):
    return {
        "id": cid,
        "subject_type": "ARTICLE",
        "subject_id": 1,
        "verdict": verdict,
        "checked_at": _ts(minutes).isoformat(),
    }


def _call(minutes, tool, outcome="OK", error_class=None, ms=100):
    return {
        "id": minutes,
        "created_at": _ts(minutes).isoformat(),
        "token_name": "op",
        "tool": tool,
        "required_scope": "pipeline",
        "outcome": outcome,
        "error_class": error_class,
        "duration_ms": ms,
    }


MEASUREMENTS = {
    "since": "2026-10-13T13:00:00+00:00",
    "until": "2026-10-13T18:00:00+00:00",
    "claims": [
        _claim(1, "VERIFIED", 61),  # before batch, in its process window
        _claim(2, "INACCURATE", 200),  # before batch, OUTSIDE its window
        _claim(3, "VERIFIED", 180),  # after batch
        _claim(4, "DISPUTED", 180),
        _claim(99, "VERIFIED", 61),  # in no batch
    ],
    "tool_calls": [
        _call(30, "search_knowledge"),  # before the after window
        _call(120, "get_deep_research_batch", ms=80),
        _call(180, "submit_fact_check_verdicts", "ERROR", "ToolError", 40),
        _call(180, "submit_fact_check_verdicts", ms=900),
    ],
}


# --- parsing ----------------------------------------------------------------------


def test_parse_timings_reads_key_values_and_rejects_naive_times():
    timings = harness.parse_timings(TIMINGS)
    assert timings["after_process_end"] == datetime(2026, 10, 13, 17, 1, 0, 500000, tzinfo=UTC)
    assert harness.phase_window(timings, "before_generate") == (_ts(0), _ts(2))
    assert harness.phase_window({}, "before_generate") is None
    with pytest.raises(ValueError):
        harness.parse_timings("before_generate_start=2026-10-13T14:00:00")
    with pytest.raises(ValueError):
        harness.parse_timings("no equals sign")


def test_phase_window_rejects_end_before_start():
    timings = harness.parse_timings("x_start=2026-10-13T14:00:00Z\nx_end=2026-10-13T13:00:00Z")
    with pytest.raises(ValueError):
        harness.phase_window(timings, "x")


def test_prompt_claim_ids_reads_only_claim_lines():
    prompt = "REF: fact_check_1\nCLAIM 41 (DATE): a\nsee CLAIM 7 (x)\nCLAIM 42 (OTHER): b\n"
    assert harness.prompt_claim_ids(prompt) == [41, 42]


def test_sections_ignore_headings_inside_code_fences():
    old, new = harness.old_and_new_sections(SKILL)
    assert old.startswith("## Fallback")
    assert "## Verdicts" in old  # fenced, so it did not end the section
    assert new.startswith("## Procedure (MCP")
    assert "Fallback" not in new
    assert harness.step_headings(new) == ["1. Scan", "a. Submit"]
    assert harness.step_headings(old) == ["SQL c. Validate", "SQL f. Write"]


def test_a_pre_mcp_file_without_markers_counts_as_all_old():
    old, new = harness.old_and_new_sections(PRE_MCP_SKILL)
    assert old == PRE_MCP_SKILL
    assert new == ""


def test_sql_statements_come_from_psql_blocks_only():
    old, _ = harness.old_and_new_sections(SKILL)
    statements = harness.sql_statements(old)
    assert statements == [
        {"kind": "SELECT", "tables": ["fact_check_claims"], "per_claim": False},
        {"kind": "UPDATE", "tables": ["fact_check_claims"], "per_claim": True},
        {
            "kind": "INSERT",
            "tables": ["fact_check_claims", "verified_claims"],
            "per_claim": True,
        },
    ]


def test_mcp_tools_named_are_unique_in_order():
    _, new = harness.old_and_new_sections(SKILL)
    assert harness.mcp_tools_named(new) == ["submit_fact_check_verdicts", "check_claim"]


# --- analysis and rendering -------------------------------------------------------


def _analyse(old_skills=None):
    return harness.analyse(
        MEASUREMENTS,
        harness.parse_timings(TIMINGS),
        before_ids=[1, 2],
        after_ids=[3, 4, 5],
        skills={"fact-check-process": SKILL},
        old_skills=old_skills,
    )


def test_analyse_attributes_claims_and_calls_to_their_windows():
    result = _analyse()
    assert result["before"] == {
        "batch_size": 2,
        "written": 1,
        "by_verdict": {"VERIFIED": 1},
        "not_written": [2],
    }
    assert result["after"] == {
        "batch_size": 3,
        "written": 2,
        "by_verdict": {"DISPUTED": 1, "VERIFIED": 1},
        "not_written": [5],
    }
    assert [c["tool"] for c in result["tool_calls"]] == [
        "get_deep_research_batch",
        "submit_fact_check_verdicts",
        "submit_fact_check_verdicts",
    ]
    assert result["tool_call_failures"] == 1
    assert result["windows"]["before_generate"] == 120.0
    assert result["wall_before"] == 420.0
    assert result["wall_after"] == 90.5
    # 1 once-per-run + 2 per-claim statements x 1 claim written.
    assert (result["sql_once"], result["sql_per_claim"]) == (1, 2)
    assert result["sql_executed_before"] == 3


def test_old_skills_replace_the_fallback_sections_as_the_before_side():
    result = _analyse(old_skills={"fact-check-prompt": PRE_MCP_SKILL})
    assert result["old_steps"] == [
        "fact-check-prompt: 1. Collect",
        "fact-check-prompt: 2. Save",
    ]
    assert [s["skill"] for s in result["sql_statements"]] == ["fact-check-prompt"]
    assert result["new_steps"] == ["fact-check-process: 1. Scan", "fact-check-process: a. Submit"]


def test_missing_stamps_render_as_not_available():
    result = harness.analyse(MEASUREMENTS, {}, [1], [3], {"s": SKILL})
    assert result["wall_before"] is None
    assert result["tool_calls"] == []
    page = harness.render_markdown(result, {"date": "2026-10-13"})
    assert "| Wall time, total (s) | n/a | n/a |" in page
    assert "(none in the after window)" in page


def test_render_markdown_reports_the_measured_numbers():
    page = harness.render_markdown(_analyse(), {"date": "2026-10-13", "before_batch": "A"})
    assert page.startswith("# Fact-check cycle before/after")
    assert "| Prod SQL statements embedded in the prompts | 3 | 0 |" in page
    assert "| Audited tool calls | 0 | 3 |" in page
    assert "| Claims written | 1 / 2 | 2 / 3 |" in page
    assert "| Failed tool calls (outcome not OK) | n/a | 1 |" in page
    assert "| DISPUTED | 0 | 1 |" in page
    assert "submit_fact_check_verdicts | ERROR | ToolError | 40 |" in page


def test_render_cli_refuses_overlapping_batches(tmp_path):
    (tmp_path / "m.json").write_text(json.dumps(MEASUREMENTS))
    (tmp_path / "t.env").write_text(TIMINGS)
    (tmp_path / "skill.md").write_text(SKILL)
    (tmp_path / "fact_check_A_prompt.md").write_text("CLAIM 1 (DATE): a\nCLAIM 2 (DATE): b\n")
    (tmp_path / "fact_check_B_prompt.md").write_text("CLAIM 2 (DATE): b\nCLAIM 3 (DATE): c\n")
    base = [
        "render",
        "--measurements",
        str(tmp_path / "m.json"),
        "--timings",
        str(tmp_path / "t.env"),
        "--skill",
        str(tmp_path / "skill.md"),
        "--before-prompt",
        str(tmp_path / "fact_check_A_prompt.md"),
    ]
    assert harness.main([*base, "--after-prompt", str(tmp_path / "fact_check_B_prompt.md")]) == 2

    (tmp_path / "fact_check_B_prompt.md").write_text("CLAIM 3 (DATE): c\n")
    out = tmp_path / "result.md"
    rc = harness.main(
        [*base, "--after-prompt", str(tmp_path / "fact_check_B_prompt.md"), "--out", str(out)]
    )
    assert rc == 0
    page = out.read_text()
    assert "Before batch: `A` (2 claims)" in page
    assert "After batch: `B` (1 claims)" in page


def test_the_real_skills_have_both_paths():
    """The checked-in skills keep an MCP procedure and a SQL fallback (#643)."""
    root = next(
        (p for p in Path(__file__).resolve().parents if (p / ".claude" / "skills").is_dir()),
        None,
    )
    if root is None:
        pytest.skip("skill files not available (test container mounts backend/ only)")
    for name, tool in [
        ("fact-check-prompt", "get_deep_research_batch"),
        ("fact-check-process", "submit_fact_check_verdicts"),
        ("research-process", "submit_research"),
    ]:
        text = (root / ".claude" / "skills" / name / "SKILL.md").read_text()
        old, new = harness.old_and_new_sections(text)
        assert tool in harness.mcp_tools_named(new), name
        assert harness.sql_statements(old), name


# --- measure (database, read-only) ------------------------------------------------


def test_measure_reads_the_window(test_db):
    now = datetime.now(UTC)
    token = McpToken(name="op", token_hash=hash_token("pic_mcp_x"), scopes=["pipeline"])
    other = McpToken(name="other", token_hash=hash_token("pic_mcp_y"), scopes=["read"])
    test_db.add_all([token, other])
    test_db.flush()

    def claim(verdict, via, at):
        return FactCheckClaim(
            subject_type="ARTICLE",
            subject_id=7,
            claim_text="x",
            verdict=verdict,
            checked_via=via,
            checked_at=at,
        )

    inside = claim("VERIFIED", "DEEP_RESEARCH", now - timedelta(minutes=5))
    test_db.add_all(
        [
            inside,
            claim("VERIFIED", "WEB_SEARCH", now - timedelta(minutes=5)),
            claim("VERIFIED", "DEEP_RESEARCH", now - timedelta(days=2)),
        ]
    )
    for tok, tool, outcome in [
        (token, "get_deep_research_batch", "OK"),
        (other, "search_knowledge", "OK"),
    ]:
        test_db.add(
            McpToolCall(
                token_id=tok.id,
                token_name=tok.name,
                tool=tool,
                required_scope="pipeline",
                args_redacted={},
                outcome=outcome,
                duration_ms=12,
            )
        )
    test_db.commit()

    data = harness.measure(test_db, now - timedelta(hours=1), now + timedelta(hours=1), "op")
    assert [c["id"] for c in data["claims"]] == [inside.id]
    assert data["claims"][0]["verdict"] == "VERIFIED"
    assert [c["tool"] for c in data["tool_calls"]] == ["get_deep_research_batch"]
    assert data["tool_calls"][0]["duration_ms"] == 12
    json.dumps(data)  # the CLI prints it as JSON

    everyone = harness.measure(test_db, now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(everyone["tool_calls"]) == 2
