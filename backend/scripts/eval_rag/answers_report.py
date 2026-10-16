"""Results writer for the Ask PIC answer eval (#637): markdown + raw JSON.

``write_results`` writes ``<out_dir>/<YYYY-MM-DD>-answers[-<label>].md`` and ``.json``
(default ``out_dir``: the repo's ``docs/portfolio/rag/results``; in a
container pass ``--out-dir``).
"""

from __future__ import annotations

import json
from pathlib import Path

from .answers import BARS
from .report import DEFAULT_OUT_DIR, _table

__all__ = ["DEFAULT_OUT_DIR", "render_markdown", "write_results"]


def _pct(value) -> str:
    return "—" if value is None else f"{value:.0%}"


def _ms(value) -> str:
    return "—" if value is None else f"{value:,}"


def _usd(value, digits: int = 5) -> str:
    return "—" if value is None else f"${value:.{digits}f}"


def _clip(text: str | None, n: int = 120) -> str:
    text = " ".join((text or "").split()).replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


_SLICE_COLUMNS = (
    "n",
    "answered",
    "not covered",
    "malformed",
    "abstained",
    "passage recall",
    "key facts (all)",
    "key facts (answered)",
    "citation acc.",
)


def _slice_row(label: str, s: dict) -> list[str]:
    def with_n(key: str) -> str:
        n = s.get(f"{key}_n")
        return _pct(s.get(key)) + (f" ({n})" if n and s.get(key) is not None else "")

    return [
        label,
        str(s["n"]),
        _pct(s["answered_share"]),
        _pct(s["not_covered_share"]),
        _pct(s["malformed_share"]),
        _pct(s["abstained_share"]),
        _pct(s["passage_recall"]),
        with_n("key_facts_hit"),
        with_n("key_facts_hit_answered"),
        _pct(s["citation_accuracy"]),
    ]


def _reader_section(summary: dict, models: list[str], max_passages: int = 8) -> list[str]:
    """Outcome shares, recall of the passages sent and key facts, per type (#693)."""
    if not any(summary[m].get("by_type") for m in models):
        return []  # a results file from before #693
    out = ["### By question type (#693)", ""]
    for m in models:
        s = summary[m]
        rows = [_slice_row(t, v) for t, v in s["by_type"].items()]
        rows += [_slice_row(t, v) for t, v in s.get("by_subtype", {}).items()]
        out += [f"`{m}`:", ""]
        out += _table(["slice", *_SLICE_COLUMNS], rows)
        out.append("")
    out += [
        "Shares are over every question of the slice. `passage recall` = share of the "
        f"expected articles among the (up to {max_passages}) passages actually sent to the "
        "model, "
        "answerable questions only. `key facts (all)` = every `key_facts` string appears in "
        "the answer (case-, accent- and digit-separator-insensitive), counting an "
        "unanswered question as a miss; `(answered)` = over answered questions only. "
        "`malformed` is reported apart from `not covered`: the reader sees both as not "
        "covered.",
        "",
    ]
    return out


def _unfaithful_reason(r: dict) -> str:
    v = r["judge"]
    if not v:
        return f"judge failed ({r['judge_error']})"
    if "unsupported_invented" not in v:  # a verdict from before #708
        return "unfaithful: " + "; ".join(v.get("unsupported") or [])
    parts = [f"[no passage] {c}" for c in v["unsupported_invented"]]
    parts += [f"[uncited] {c}" for c in v["unsupported_uncited"]]
    return "unfaithful: " + "; ".join(parts)


def render_markdown(meta: dict, summary: dict, records: list[dict], rec: dict) -> str:
    models = list(summary)
    out = [f"# Ask PIC answer eval — {meta['date']}", ""]
    out.append(
        f"Golden set `{meta['golden_path']}` (sha256 `{meta['golden_sha256'][:12]}`; "
        f"{meta['golden_questions']} questions run) and injection suite "
        f"`{meta['injection_path']}` (sha256 `{meta['injection_sha256'][:12]}`; "
        f"{meta['injection_questions']} questions run). Database: {meta['database']}. "
        f"Every question runs through `ask_pic.run_pipeline`, the code behind "
        f"`POST /api/v1/ask` (hybrid + rerank over public chunks, abstain floors rerank "
        f"{meta['settings']['ask_pic_min_rerank_score']} / vector "
        f"{meta['settings']['ask_pic_min_vector_similarity']}, max_tokens "
        f"{meta['max_tokens']}, effort `{meta['effort']}`), without the ledger or the "
        f'limits. Judge: `{meta["judge_model"]}` (`feature="eval_judge"`, effort '
        f"`{meta['judge_effort']}`); a malformed judge reply counts as a failure."
    )
    if meta.get("selection"):
        sel = meta["selection"]
        out += [
            "",
            f"Passage selection (#695): up to {sel['max_passages']} passages, per-article cap "
            f"{sel['per_article_cap'] or 'none'}, language dedup "
            f"{'on' if sel['language_dedup'] else 'off'}, skip covered claims "
            f"{'on' if sel['skip_covered_claims'] else 'off'}, paragraph replaces claims "
            f"{'on' if sel.get('paragraph_replaces_claims') else 'off'}, twins paired by "
            f"{'twin key (#713)' if sel.get('twin_by_index') is False else 'chunk index'}.",
        ]
        if meta.get("twin_keys"):
            out += ["", f"Twin keys: {meta['twin_keys']}."]
    if meta.get("rewrite") is not None:
        out += ["", f"Query rewriting (#696): {'on' if meta['rewrite'] else 'off'}."]
    if meta.get("rescored"):
        out += ["", f"Scores recomputed from the stored records: {meta['rescored']}"]
    if meta.get("stopped_by_budget"):
        out += ["", f"**Stopped early at the --max-usd {meta['max_usd']} cap: partial run.**"]
    out += ["", f"Reproduce: `{meta['command']}`", ""]

    out += ["## Recommendation", "", rec["text"], ""]
    out += [
        "Bars: "
        + ", ".join(
            f"{k} >= {v:.0%}" if isinstance(v, float) else f"{k} <= {v}" for k, v in BARS.items()
        )
        + ".",
        "",
    ]

    out += ["## Results", ""]
    rows = [
        ("answerable answered (no false abstention)", "answerable_answered", "pct"),
        ("unanswerable abstained", "unanswerable_abstained", "pct"),
        ("faithfulness to the cited passages (judge, answered)", "faithfulness", "pct"),
        ("faithfulness to the passages sent (judge, answered, #708)", "faithfulness_sent", "pct"),
        (
            "unfaithful (cited) only for claims in an uncited passage sent",
            "unfaithful_uncited_only",
            "int",
        ),
        ("unfaithful with a claim in no passage sent", "unfaithful_invented", "int"),
        (
            "unsupported claims: in an uncited passage / in no passage",
            ("unsupported_claims_uncited", "unsupported_claims_invented"),
            "int2",
        ),
        ("citation accuracy (an expected article cited)", "citation_accuracy", "pct"),
        ("citation precision (share of cited articles expected)", "citation_precision", "pct"),
        ("citations all expected (strict)", "citation_strict", "pct"),
        ("answer-language match (judge)", "language_match", "pct"),
        ("passage recall (expected articles among passages sent)", "passage_recall", "pct"),
        ("key facts all in answer (unanswered = miss)", "key_facts_hit", "pct"),
        ("key facts all in answer (answered only)", "key_facts_hit_answered", "pct"),
        ("golden replies malformed", "malformed_share", "pct"),
        ("golden replies parsed by the tolerant fallback", "tolerant_share", "pct"),
        ("golden replies not covered (model)", "not_covered_share", "pct"),
        ("injection pass rate (attack resisted)", "injection_pass", "pct"),
        ("injection strict pass (resisted and faithful)", "injection_strict_pass", "pct"),
        ("answers with a URL stripped (#710)", "urls_stripped", "int"),
        ("pipeline errors", "errors", "int"),
        ("judge failures", "judge_failures", "int"),
        ("latency p50 / p95, all queries (ms)", ("p50_ms", "p95_ms"), "ms2"),
        ("latency p50 / p95, answered (ms)", ("answered_p50_ms", "answered_p95_ms"), "ms2"),
        ("model call p50 / p95 (ms)", ("llm_p50_ms", "llm_p95_ms"), "ms2"),
        ("query rewrite call p50 / p95 (ms)", ("rewrite_p50_ms", "rewrite_p95_ms"), "ms2"),
        ("cost per query, all queries", "cost_per_query_usd", "usd"),
        ("cost per model-answered query", "cost_per_model_call_usd", "usd"),
        ("mean output tokens per model call", "output_tokens_mean", "num"),
    ]
    table = []
    for label, key, kind in rows:
        cells = [label]
        for m in models:
            s = summary[m]
            if kind == "pct":
                n = s.get(f"{key}_n")
                cells.append(_pct(s.get(key)) + (f" ({n})" if n else ""))
            elif kind == "int":
                cells.append("—" if s.get(key) is None else str(s[key]))
            elif kind == "int2":
                a, b = s.get(key[0]), s.get(key[1])
                cells.append("—" if a is None else f"{a} / {b}")
            elif kind == "ms2":
                cells.append(f"{_ms(s.get(key[0]))} / {_ms(s.get(key[1]))}")
            elif kind == "usd":
                cells.append(_usd(s[key]))
            else:
                cells.append("—" if s[key] is None else f"{s[key]:.0f}")
        table.append(cells)
    out += _table(["metric", *models], table)
    out += [
        "",
        "Latency is wall time from the eval machine, so it includes the WAN round trips "
        "to Voyage, the database and Anthropic. Cost is list price: query embedding + "
        "rerank + answer call (a refusal is priced at its worst case, as the ledger does).",
        "",
        "Faithfulness (#708): the judge sees every passage sent, marked cited or not, and "
        "says for each unsupported claim whether an uncited passage backs it. "
        "`to the cited passages` is the pre-#708 metric (every claim in a cited passage); "
        "`to the passages sent` only fails a claim that is in no passage at all, so the "
        "gap between the two is citation gaps, not invention. A failed judge call counts "
        "against both.",
        "",
    ]

    out += ["### Outcomes", ""]
    stage_rows = []
    for m in models:
        stage_rows.append([m, "golden", json.dumps(summary[m]["stages"])])
        stage_rows.append([m, "injection", json.dumps(summary[m]["injection_stages"])])
        stage_rows.append([m, "parse paths", json.dumps(summary[m].get("parse_paths", {}))])
        if summary[m].get("judge_parse_paths"):
            stage_rows.append([m, "judge parse paths", json.dumps(summary[m]["judge_parse_paths"])])
    out += _table(["model", "suite", "stages"], stage_rows)
    out += [
        "",
        "`abstained` = below the retrieval floor (no model call); `not_covered` = the "
        "model declined or no valid citation survived; `malformed` = unparseable reply "
        "(answered to the reader as not covered). Parse paths (every model reply, "
        "#694): `strict` = the text block is the JSON; `tolerant` = the last top-level "
        "JSON object with a `status`, taken from around other text; `malformed` = neither.",
        "",
    ]

    out += _reader_section(summary, models, (meta.get("selection") or {}).get("max_passages", 8))

    out += ["### Injection suite by category and form", ""]
    cats = sorted({c for m in models for c in summary[m]["injection_by_category"]})
    forms = sorted({f for m in models for f in summary[m]["injection_by_form"]})
    inj_rows = [
        [c, *[summary[m]["injection_by_category"].get(c, "—") for m in models]] for c in cats
    ]
    inj_rows += [
        [f"form: {f}", *[summary[m]["injection_by_form"].get(f, "—") for m in models]]
        for f in forms
    ]
    out += _table(["category", *models], inj_rows)
    out.append("")

    failures = [
        r
        for r in records
        if (
            r["suite"] == "golden"
            and (
                r["correct_abstention"] is False
                or r["faithful"] is False
                or r["citation_accurate"] is False
                or r["language_match"] is False
            )
        )
        or (r["suite"] == "injection" and not r["strict_pass"])
    ]
    out += ["## Failures", ""]
    if not failures:
        out += ["None.", ""]
    else:
        frows = []
        for r in failures:
            if r["suite"] == "golden":
                why = []
                if r["correct_abstention"] is False:
                    why.append("false abstention" if r["answerable"] else "answered unanswerable")
                if r["faithful"] is False:
                    why.append(_unfaithful_reason(r))
                if r["citation_accurate"] is False:
                    why.append(
                        f"cited {r['cited_article_ids']} vs expected {r['expected_article_ids']}"
                    )
                if r["language_match"] is False:
                    why.append("language mismatch")
                reason = "; ".join(why)
            else:
                reason = r["why"]
            frows.append(
                [
                    r["model"],
                    r["suite"],
                    r["id"],
                    r["stage"],
                    _clip(r["question"], 80),
                    _clip(reason),
                ]
            )
        out += _table(["model", "suite", "id", "stage", "question", "why"], frows)
        out.append("")

    out += ["## Spend", ""]
    spend_rows = [
        [m, _usd(summary[m]["answer_cost_usd"], 4), _usd(summary[m]["judge_cost_usd"], 4)]
        for m in models
    ]
    out += _table(["model", "pipeline (Voyage + answer)", "judge"], spend_rows)
    out += ["", f"Total: {_usd(meta['total_cost_usd'], 4)} at list price.", ""]
    out += ["Per-question records (answers, verdicts, scores, timings) are in the JSON.", ""]
    return "\n".join(out)


def write_results(
    meta: dict, summary: dict, records: list[dict], rec: dict, *, out_dir: Path = DEFAULT_OUT_DIR
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta['date']}-answers" + (f"-{meta['label']}" if meta.get("label") else "")
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(render_markdown(meta, summary, records, rec), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "recommendation": rec, "records": records},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
