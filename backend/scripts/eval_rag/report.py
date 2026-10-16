"""Results writer for the retrieval eval: a markdown table + the raw JSON (#627).

``write_results`` writes ``<out_dir>/<YYYY-MM-DD>-retrieval.md`` and
``<YYYY-MM-DD>-retrieval.json``. The default ``out_dir`` is the repo's
``docs/portfolio/rag/results`` (``REPO_ROOT`` is three levels above this
package, which inside the backend container is ``/``: mount the repo's
``docs`` at ``/docs`` and the same default works there).
"""

from __future__ import annotations

import json
from pathlib import Path

from .runner import METRIC_KEYS

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "portfolio" / "rag" / "results"


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _cost(value) -> str:
    return "—" if value is None else f"${value:.6f}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render_markdown(meta: dict, summary: dict) -> str:
    """The human-readable results page."""
    out = [f"# Retrieval eval — {meta['date']}", ""]
    out.append(
        f"Golden set `{meta['golden_path']}` ({meta['questions']} questions: "
        f"{meta['answerable']} answerable, {meta['unanswerable']} unanswerable; "
        f"sha256 `{meta['golden_sha256'][:12]}`). Visibility `public`, k={meta['k']} "
        f"articles, {meta['candidates']} candidates per stage, rerank "
        f"`{meta['rerank_model']}` over the top {meta['rerank_top_n']} fused chunks."
    )
    curation = meta.get("curation") or {}
    if curation:
        out.append("")
        out.append(
            f"Curation (`{curation.get('drafts_file')}`): drafted {curation.get('drafted')}, "
            f"kept {curation.get('kept')}, edited {curation.get('edited')}, "
            f"dropped {curation.get('dropped')}; by type {curation.get('by_type')}; "
            f"by language {curation.get('by_language')}."
        )
        reader_cur = curation.get("reader")
        if reader_cur:
            out.append("")
            out.append(
                f"Reader questions (#693, `{reader_cur.get('drafts_file')}`, drafted and "
                f"verified by Opus): drafted {reader_cur.get('drafted')}, kept "
                f"{reader_cur.get('kept')}, fixed {reader_cur.get('fixed')}, dropped "
                f"{reader_cur.get('dropped')}; added {reader_cur.get('questions_added')} "
                f"(by type {reader_cur.get('by_type')}; by language "
                f"{reader_cur.get('by_language')})."
            )
    if meta.get("database"):
        out += ["", f"Database: {meta['database']}; index: the live `knowledge_chunks`."]
    out.append("")
    out.append(f"Reproduce: `{meta['command']}`")
    out.append("")
    if meta.get("tables"):
        out += ["## Corpus", ""]
        out += _table(
            ["model", "table", "rows", "embedded", "embedded this run", "hash mismatches"],
            [
                [
                    t["model"],
                    f"`{t['table']}`",
                    _fmt(t["counts"].get("total")),
                    _fmt(t["counts"].get("embedded")),
                    _fmt(t["sync"].get("to_embed")),
                    _fmt(t["sync"].get("hash_mismatches")),
                ]
                for t in meta["tables"]
            ],
        )
        out.append("")
    out += ["## Results (answerable questions; article-level)", ""]
    out += _table(
        [
            "model",
            "mode",
            *METRIC_KEYS,
            "p50 ms",
            "p95 ms",
            "embed tok/q",
            "rerank tok/q",
            "cost/q",
        ],
        [
            [
                r["model"],
                r["mode"],
                *(_fmt(r[key]) for key in METRIC_KEYS),
                _fmt(r["latency_p50_ms"], 1),
                _fmt(r["latency_p95_ms"], 1),
                _fmt(r["embed_tokens_per_query"], 1),
                _fmt(r["rerank_tokens_per_query"], 1),
                _cost(r["cost_usd_per_query"]),
            ]
            for r in summary["rows"]
        ],
    )
    degraded = [r for r in summary["rows"] if r["degraded_queries"]]
    if degraded:
        out.append("")
        out.append(
            "**Degraded:** "
            + ", ".join(f"{r['model']}/{r['mode']}: {r['degraded_queries']}" for r in degraded)
            + " queries ran degraded (see `degraded` in the JSON); those rows are not comparable."
        )
    out += ["", "## By question type", ""]
    out += _table(
        ["model", "mode", "type", "n", "recall@5", "mrr", "ndcg@10"],
        [
            [
                r["model"],
                r["mode"],
                r["type"],
                str(r["questions"]),
                _fmt(r["recall@5"]),
                _fmt(r["mrr"]),
                _fmt(r["ndcg@10"]),
            ]
            for r in summary["by_type"]
        ],
    )
    out += [
        "",
        "## Abstain floor (top-1 confidence, answerable vs unanswerable)",
        "",
        "A question is answered when its signal is `>= floor`. `keep_95` keeps 95% of "
        "answerable questions; `balanced` maximises (kept + abstained) / 2; "
        "`abstain_all` is just above the highest unanswerable score.",
        "",
    ]
    rows = []
    for a in summary["abstain"]:
        ans, una, floors = a["answerable"], a["unanswerable"], a["floors"]

        def floor_cell(name: str, floors=floors) -> str:
            f = floors.get(name)
            if not f:
                return "—"
            return (
                f"{f['floor']:.4f} (kept {f['answerable_kept']:.0%}, "
                f"abstained {f['unanswerable_abstained']:.0%})"
            )

        rows.append(
            [
                a["model"],
                a["mode"],
                a["signal"],
                f"{_fmt(ans['median'], 4)} [{_fmt(ans['min'], 4)}–{_fmt(ans['max'], 4)}]",
                f"{_fmt(una['median'], 4)} [{_fmt(una['min'], 4)}–{_fmt(una['max'], 4)}]",
                floor_cell("keep_95"),
                floor_cell("balanced"),
                floor_cell("abstain_all"),
            ]
        )
    out += _table(
        [
            "model",
            "mode",
            "signal",
            "answerable median [min–max]",
            "unanswerable median [min–max]",
            "keep_95",
            "balanced",
            "abstain_all",
        ],
        rows,
    )
    out += ["", "Per-question scores for every unanswerable question are in the JSON.", ""]
    return "\n".join(out)


def write_results(
    meta: dict, summary: dict, records: list[dict], *, out_dir: Path = DEFAULT_OUT_DIR
) -> tuple[Path, Path]:
    """Write the markdown page and the raw JSON; returns both paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta['date']}-retrieval"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(render_markdown(meta, summary), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "records": records},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
