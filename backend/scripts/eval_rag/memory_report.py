"""Results writer for the pipeline-memory eval (#632): markdown + raw JSON.

``write_results`` writes ``<out_dir>/<YYYY-MM-DD>-memory.md`` and ``.json``
(default ``out_dir``: the repo's ``docs/portfolio/rag/results``; in a
container pass ``--out-dir``).
"""

from __future__ import annotations

import json
from pathlib import Path

from .report import DEFAULT_OUT_DIR, _fmt, _table

__all__ = ["DEFAULT_OUT_DIR", "render_markdown", "write_results"]


def _pct(value) -> str:
    return "—" if value is None else f"{value:.0%}"


def _usd(value) -> str:
    return "—" if value is None else f"${value:.4f}"


def _clip(text: str | None, n: int = 110) -> str:
    text = " ".join((text or "").split()).replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


def _summary(meta: dict, b3: dict | None, b4: dict | None, rec: dict) -> list[str]:
    out = ["## Summary", ""]
    if b4:
        row = b4["selection"]["row"] or {}
        cfg = b4["configured_row"]
        out.append(
            f"- **B4 contradictions** ({b4['perturbed']} perturbed, {b4['paraphrase']} "
            f"paraphrases): at the configured floor {b4['configured_floor']:g}, precision "
            f"{_pct(cfg['precision'])}, recall {_pct(cfg['recall'])}, source retrieved "
            f"{_pct(cfg['source_hit_rate'])}. Chosen floor **{b4['chosen_floor']:g}**: precision "
            f"{_pct(row.get('precision'))}, recall {_pct(row.get('recall'))}."
        )
    if b3:
        row = b3["selection"]["row"] or {}
        cur = b3["current_default"]
        out.append(
            f"- **B3 duplicates** ({b3['near_duplicates']} near-duplicates, {b3['novel']} novel): "
            f"at the provisional {cur['threshold']:g}, precision {_pct(cur['precision'])}, recall "
            f"{_pct(cur['recall'])}. Chosen threshold **{b3['chosen_threshold']:g}**: precision "
            f"{_pct(row.get('precision'))}, recall {_pct(row.get('recall'))}, F1 "
            f"{_fmt(row.get('f1'))}."
        )
    out.append(f"- Total cost: {_usd(meta['cost_usd_total'])} (Sonnet judge + Voyage).")
    for key in ("b4", "b3"):
        if key in rec:
            out.append(f"- Recommendation {key.upper()}: {rec[key]['text']}")
    return out + [""]


def _sweep_rows(rows: list[dict], extra: list[str]) -> list[list[str]]:
    out = []
    for r in rows:
        cells = [
            f"{r['threshold']:g}",
            str(r["tp"]),
            str(r["fp"]),
            str(r["fn"]),
            str(r["tn"]),
            _fmt(r["precision"]),
            _fmt(r["recall"]),
            _fmt(r["f1"]),
        ]
        for key in extra:
            value = r.get(key)
            cells.append(_usd(value) if key.endswith("_usd") else _fmt(value, 2))
        out.append(cells)
    return out


def _b4(b4: dict) -> list[str]:
    out = ["## B4: cross-article contradictions", ""]
    out.append(
        f"{b4['items']} items ({b4['perturbed']} perturbed = positives, {b4['paraphrase']} "
        f"faithful paraphrases). Retrieval as #631 (`_gather_pairs`, vector, internal, "
        f"ARTICLE_CLAIM + VERIFIED_CLAIM, top_k {b4['top_k']}) with **no article excluded** "
        f"(the seeded claim must meet its own source), at floor >= {b4['retrieval_floor']:g}; "
        f"{b4['pairs']} pairs judged by the production judge in {b4['judge_calls']} calls "
        f"({b4['failed_batches']} failed). An item is flagged when any pair at the floor is "
        "CONTRADICTS."
    )
    out.append("")
    sel = b4["selection"]
    out.append(
        f"Selection rule: {sel['rule']}"
        + ("" if sel["met_precision"] else " — **no floor met the precision bar**; max F1 shown")
        + f". Chosen: **{b4['chosen_floor']:g}** (configured today: {b4['configured_floor']:g})."
    )
    out.append("")
    out += _table(
        [
            "floor",
            "TP",
            "FP",
            "FN",
            "TN",
            "precision",
            "recall",
            "F1",
            "source hit",
            "pairs/item",
            "judge $/item",
        ],
        _sweep_rows(b4["sweep"], ["source_hit_rate", "pairs_per_item", "judge_cost_per_item_usd"]),
    )
    out.append("")
    out += ["By language at the chosen floor:", ""]
    out += _table(
        ["language", "n", "TP", "FP", "FN", "TN", "precision", "recall"],
        [
            [
                lang,
                str(v["n"]),
                str(v["tp"]),
                str(v["fp"]),
                str(v["fn"]),
                str(v["tn"]),
                _fmt(v["precision"]),
                _fmt(v["recall"]),
            ]
            for lang, v in b4["by_language"].items()
        ],
    )
    out.append("")
    if b4["by_perturbation"]:
        out.append(
            "Recall by perturbation kind (chosen floor): "
            + ", ".join(
                f"{k} {_pct(v['recall'])} (n={v['n']})" for k, v in b4["by_perturbation"].items()
            )
            + "."
        )
        out.append("")
    sims = b4["source_similarity"]
    out.append(
        f"Similarity of each item to its own source chunk (read directly): perturbed median "
        f"{_fmt(sims['perturbed']['median'])} (min {_fmt(sims['perturbed']['min'])}), paraphrase "
        f"median {_fmt(sims['paraphrase']['median'])} (min {_fmt(sims['paraphrase']['min'])}); "
        f"{b4['source_missing']} source chunk(s) missing from the index. Judge on the source "
        f"pair alone: {_pct(b4['judge_source_pair_accuracy'])} correct over "
        f"{b4['judge_source_pairs']} pairs."
    )
    out.append("")
    out.append(
        f"Judge tokens: {b4['judge_input_tokens']} in / {b4['judge_output_tokens']} out, "
        f"{_usd(b4['judge_cost_usd'])}."
    )
    out.append("")
    misses = [
        r
        for r in b4["records"]
        if r["expected_label"] == "CONTRADICTS" and not r["flagged_at_chosen"]
    ]
    false_pos = [
        r for r in b4["records"] if r["expected_label"] != "CONTRADICTS" and r["flagged_at_chosen"]
    ]
    for title, rows in (("Missed contradictions", misses), ("False positives", false_pos)):
        if not rows:
            continue
        out += [f"{title} (chosen floor):", ""]
        out += _table(
            ["id", "lang", "seeded text", "original", "source sim"],
            [
                [
                    r["id"],
                    r["language"],
                    _clip(r["text"]),
                    _clip(r["original_text"]),
                    _fmt(r["source_similarity"]),
                ]
                for r in rows
            ],
        )
        out.append("")
    return out


def _b3(b3: dict) -> list[str]:
    out = ["## B3: duplicate-topic flag", ""]
    out.append(
        f"{b3['items']} topics ({b3['near_duplicates']} near-duplicates = positives, "
        f"{b3['novel']} novel). Embedded as #630 embeds a suggestion (title + main topic, "
        "document input) and compared with every article centroid (`nearest_articles`, k=1, "
        "internal). Suggestion-to-suggestion matching is not measured."
    )
    out.append("")
    sel = b3["selection"]
    out.append(
        f"Selection rule: {sel['rule']}"
        + (
            ""
            if sel["met_precision"]
            else " — **no threshold met the precision bar**; max F1 shown"
        )
        + f". Chosen: **{b3['chosen_threshold']:g}** (provisional default: "
        f"{b3['current_default']['threshold']:g})."
    )
    out.append("")
    out += _table(
        ["threshold", "TP", "FP", "FN", "TN", "precision", "recall", "F1"],
        _sweep_rows(b3["sweep"], []),
    )
    out.append("")
    out += ["By language at the chosen threshold:", ""]
    out += _table(
        ["language", "n", "TP", "FP", "FN", "TN", "precision", "recall"],
        [
            [
                lang,
                str(v["n"]),
                str(v["tp"]),
                str(v["fp"]),
                str(v["fn"]),
                str(v["tn"]),
                _fmt(v["precision"]),
                _fmt(v["recall"]),
            ]
            for lang, v in b3["by_language"].items()
        ],
    )
    out.append("")
    sims = b3["similarity"]
    out.append(
        f"Best-centroid similarity: near-duplicates median {_fmt(sims['near_duplicate']['median'])} "
        f"(min {_fmt(sims['near_duplicate']['min'])}); novel median {_fmt(sims['novel']['median'])} "
        f"(max {_fmt(sims['novel']['max'])}). Near-duplicates whose best match is their source "
        f"article: {_pct(b3['source_match_rate'])}. Novel topics still `review: pending`: "
        f"{b3['novel_unreviewed']}; flagged `suspect` at seed time: {b3['novel_suspect']}."
    )
    out.append("")
    novel = sorted(
        (r for r in b3["records"] if r["type"] == "novel" and r["similarity"] is not None),
        key=lambda r: -r["similarity"],
    )[:5]
    if novel:
        out += ["Closest novel topics (check these by hand):", ""]
        out += _table(
            ["id", "lang", "topic", "nearest article", "similarity"],
            [
                [
                    r["id"],
                    r["language"],
                    _clip(r["text"], 80),
                    _clip(r["best_article_title"], 60),
                    _fmt(r["similarity"]),
                ]
                for r in novel
            ],
        )
        out.append("")
    return out


def render_markdown(meta: dict, b3: dict | None, b4: dict | None, rec: dict) -> str:
    out = [f"# Pipeline-memory eval — {meta['date']}", ""]
    out.append(
        f"Seed `{meta['seed_path']}` (sha256 `{meta['seed_sha256'][:12]}`; "
        f"{meta['contradiction_items']} contradiction and {meta['duplicate_items']} duplicate "
        f"items after review drops). Database read-only"
        + (" (production, `--read-only-prod`)" if meta.get("read_only_prod") else "")
        + f". Prices: Sonnet ${meta['sonnet_prices'][0]:g} / ${meta['sonnet_prices'][1]:g} per 1M "
        "(placeholder, see #653)."
    )
    out += ["", f"Reproduce: `{meta['command']}`", ""]
    out += _summary(meta, b3, b4, rec)
    if b4:
        out += _b4(b4)
    if b3:
        out += _b3(b3)
    out += ["## Recommendation", ""]
    for key, label in (("b4", "B4 (consistency check)"), ("b3", "B3 (suggestion dedup)")):
        if key in rec:
            out.append(f"- **{label}:** {rec[key]['text']}")
    out.append("")
    out.append("Switching either flag on is the operator's decision; both stay default off.")
    out.append("")
    return "\n".join(out)


def write_results(
    meta: dict, b3: dict | None, b4: dict | None, rec: dict, *, out_dir: Path
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{meta['date']}-memory.md"
    js = out_dir / f"{meta['date']}-memory.json"
    md.write_text(render_markdown(meta, b3, b4, rec), encoding="utf-8")
    payload = {"meta": meta, "b3": b3, "b4": b4, "recommendation": rec}
    js.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return md, js
