"""Passage-selection eval for Ask PIC (#695, epic #698): no model call.

Each golden question is retrieved **once** with ``ask_pic.search_passages``
(the endpoint's own hybrid + rerank call); every selection variant is then
applied to that same ranked list with ``ask_pic._passages``, so the variants
differ only in the selection rule. Costs Voyage (query embedding + rerank)
only, a few cents for the whole set.

Per variant, over the answerable questions:

- ``passage_recall``: share of the expected articles among the passages sent
  (as the answers eval scores it);
- ``key_facts_in_passages``: every ``key_facts`` string occurs in the text of
  the passages sent (the answer cannot hold a fact the model never saw; plain
  substring matching, so a fact "25" also matches inside "1525");
- passages sent, distinct articles, prompt tokens (mean / max), and the slots
  the #695 rules target: other-language twins both sent, claim chunks sent
  next to a paragraph of their article, the largest share of one article.

The top-1 rerank score is selection-independent, so the run also reports the
abstain floor (``ask_pic_min_rerank_score``) against this set.

``--rewrite`` (#696) retrieves through the query-rewrite path instead: one
``ask_pic.rewrite_queries`` call (Haiku, effort low) per question, then the
multi-query search the endpoint runs with the setting on. The rewritten
queries, the call's status / time / cost and the retrieval time are kept per
question, so two runs (with and without) compare retrieval alone.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from app.core.config import get_settings
from app.models.enums import KnowledgeSourceType
from app.prompts.ask_pic import SYSTEM_PROMPT
from app.services import ask_pic, embeddings
from app.services.ask_pic_controls import estimate_input_tokens

from .golden import GoldenSet, key_facts_found
from .metrics import mean, percentile
from .report import DEFAULT_OUT_DIR, _table


def _S(n: int, cap: int | None = None, rules: str = "") -> ask_pic.PassageSelection:
    """``rules``: ``d`` language dedup, ``c`` skip covered claims, ``r`` a
    paragraph replaces its article's claims, ``k`` twins paired by the #713
    twin key instead of ``chunk_index``."""
    return ask_pic.PassageSelection(
        max_passages=n,
        per_article_cap=cap,
        language_dedup="d" in rules,
        skip_covered_claims="c" in rules,
        paragraph_replaces_claims="r" in rules,
        twin_by_index="k" not in rules,
    )


# Named variants: the pre-#695 baseline, each rule alone, and combinations.
VARIANTS: dict[str, ask_pic.PassageSelection] = {
    "baseline": _S(8),
    "dedup": _S(8, rules="d"),
    "cap2": _S(8, 2),
    "claims": _S(8, rules="c"),
    "claims-replace": _S(8, rules="r"),
    "max12": _S(12),
    "max15": _S(15),
    "rules8": _S(8, 2, "dc"),
    "rules12": _S(12, 2, "dc"),
    "rules15": _S(15, 2, "dc"),
    "rules12-cap3": _S(12, 3, "dc"),
    "rules8-replace": _S(8, 2, "dr"),
    "rules12-replace": _S(12, 2, "dr"),
    "rules15-replace": _S(15, 2, "dr"),
    "dedup-cap2-12": _S(12, 2, "d"),
    # Leave-one-out from the shipped combination (rules15-replace).
    "rules15-replace-no-dedup": _S(15, 2, "r"),
    "rules15-replace-no-cap": _S(15, None, "dr"),
    "rules15-replace-no-claims": _S(15, 2, "d"),
    "rules15-replace-cap3": _S(15, 3, "dr"),
    # The shipped combination with twins paired by the #713 twin key.
    "rules15-replace-twin-key": _S(15, 2, "drk"),
}
DEFAULT_VARIANTS = tuple(VARIANTS)
FLOORS = (0.70, 0.72, 0.74, 0.75, 0.76)
_CLAIM = KnowledgeSourceType.ARTICLE_CLAIM.value


def passage_label(p) -> str:
    kind = "c" if p.source_type == _CLAIM else f"#{p.chunk_index}"
    return f"{p.article_id}{p.language}{kind}"


def _wasted(passages: list) -> dict:
    """Slots the #695 rules target, counted on what was sent."""
    paragraphs = Counter()
    twins = Counter()
    for p in passages:
        if p.source_type != _CLAIM:
            paragraphs[p.article_id] += 1
            if p.twin_key:
                twins[(p.article_id, p.twin_key)] += 1
    per_article = Counter(p.article_id for p in passages)
    return {
        "twin_pairs": sum(1 for n in twins.values() if n > 1),
        "covered_claims": sum(
            1 for p in passages if p.source_type == _CLAIM and paragraphs[p.article_id]
        ),
        "max_per_article": max(per_article.values(), default=0),
    }


def evaluate_question(q, result, language: str, variants: dict) -> dict:
    """Every variant's selection over one retrieval result."""
    out = {}
    for name, selection in variants.items():
        passages = ask_pic._passages(result, language, selection)
        sent = [p.article_id for p in passages]
        text = "\n".join(p.content for p in passages)
        expected = set(q.expected_article_ids)
        rec = {
            # Compact: "<article><lang>#<chunk_index>", or "<article><lang>c" for a claim.
            "passages": [passage_label(p) for p in passages],
            "n_passages": len(passages),
            "n_articles": len(set(sent)),
            "prompt_tokens": estimate_input_tokens(
                SYSTEM_PROMPT, ask_pic._user_prompt(result, passages, q.question, language)
            ),
            **_wasted(passages),
        }
        if q.answerable and expected:
            rec["passage_recall"] = len(expected & set(sent)) / len(expected)
        if q.answerable and q.key_facts:
            found = key_facts_found(text, q.key_facts)
            rec["key_facts_found"] = found
            rec["key_facts_in_passages"] = len(found) == len(q.key_facts)
        out[name] = rec
    return out


def chunk_ranks(q, result) -> dict:
    """Where the evidence sits in the full reranked chunk list (#697), 1-based.

    ``fact_chunk_rank``: the first chunk whose own text holds every key fact
    (None when no single chunk in the pool does). ``expected_chunk_rank``: the
    first chunk of an expected article. ``fact_chunk_rerank`` is that chunk's
    rerank score.
    """
    out: dict = {}
    if not q.answerable:
        return out
    expected = set(q.expected_article_ids)
    for rank, chunk in enumerate(result.chunks, start=1):
        if "expected_chunk_rank" not in out and chunk.article_id in expected:
            out["expected_chunk_rank"] = rank
        if (
            q.key_facts
            and "fact_chunk_rank" not in out
            and len(key_facts_found(chunk.content, q.key_facts)) == len(q.key_facts)
        ):
            out["fact_chunk_rank"] = rank
            out["fact_chunk_rerank"] = chunk.rerank_score
    if q.key_facts:
        out.setdefault("fact_chunk_rank", None)
    return out


def run(
    gold: GoldenSet,
    search_session,
    *,
    variants: dict,
    progress: Callable[[str], None] = lambda _line: None,
    rewrite: bool = False,
) -> tuple[list[dict], float]:
    """(per-question records, USD spent: Voyage, plus the rewrite calls with ``rewrite``)."""
    records = []
    spent = 0.0
    model = get_settings().embedding_model
    for i, q in enumerate(gold.questions, 1):
        language = ask_pic.detect_language(q.question, q.language)
        rw = ask_pic.rewrite_queries(q.question, language, log_label="eval") if rewrite else None
        t = time.perf_counter()
        with embeddings.track_usage() as scope, search_session() as db:
            result = ask_pic.search_passages(db, q.question, rw.queries if rw else None)
        retrieval_ms = int((time.perf_counter() - t) * 1000)
        embed_tokens, rerank_usd = ask_pic._voyage_cost(scope)
        cost = rerank_usd + (embeddings.estimate_cost(model, embed_tokens) if embed_tokens else 0)
        cost += rw.cost_usd if rw else 0.0
        spent += cost
        top_rerank, top_similarity = ask_pic._top_scores(result)
        records.append(
            {
                "id": q.id,
                "question": q.question,
                "language": language,
                "type": q.type,
                "subtype": q.subtype,
                "source": q.source,
                "answerable": q.answerable,
                "expected_article_ids": list(q.expected_article_ids),
                "key_facts": q.key_facts,
                "top_rerank": top_rerank,
                "top_similarity": top_similarity,
                "degraded": list(result.degraded),
                "cost_usd": cost,
                "retrieval_ms": retrieval_ms,
                "rewrite_status": rw.status if rw else None,
                "rewrite_queries": list(rw.queries) if rw else [],
                "rewrite_ms": rw.ms if rw else None,
                "rewrite_cost_usd": rw.cost_usd if rw else 0.0,
                "variants": evaluate_question(q, result, language, variants),
                **chunk_ranks(q, result),
            }
        )
        progress(f"[{i}/{len(gold.questions)}] {q.id} top1={top_rerank} (spent ${spent:.4f})")
    return records, spent


def _rate(values) -> float | None:
    vals = [bool(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def summarize_variant(records: list[dict], name: str) -> dict:
    rows = [r["variants"][name] for r in records]
    answerable = [r["variants"][name] for r in records if r["answerable"]]
    with_facts = [v for v in answerable if "key_facts_in_passages" in v]
    return {
        "passage_recall": mean(v.get("passage_recall") for v in answerable),
        "passage_hit": _rate(
            (v["passage_recall"] > 0) if "passage_recall" in v else None for v in answerable
        ),
        "key_facts_in_passages": _rate(v["key_facts_in_passages"] for v in with_facts),
        "key_facts_in_passages_n": f"{sum(v['key_facts_in_passages'] for v in with_facts)}"
        f"/{len(with_facts)}",
        "mean_passages": mean(v["n_passages"] for v in rows),
        "mean_articles": mean(v["n_articles"] for v in rows),
        "mean_prompt_tokens": mean(v["prompt_tokens"] for v in rows),
        "max_prompt_tokens": max((v["prompt_tokens"] for v in rows), default=0),
        "twin_pairs": sum(v["twin_pairs"] for v in rows),
        "covered_claims": sum(v["covered_claims"] for v in rows),
        "max_per_article_mean": mean(v["max_per_article"] for v in rows),
    }


def _slices(records: list[dict]) -> dict[str, list[dict]]:
    slices = {"all answerable": [r for r in records if r["answerable"]]}
    slices["reader"] = [r for r in records if r["type"] == "reader"]
    slices["reader/distractor"] = [r for r in records if r["subtype"] == "distractor"]
    slices["operator (op01-op06)"] = [r for r in records if r["source"] == "operator"]
    return slices


def floor_report(records: list[dict], floors=FLOORS) -> dict:
    ans = [r["top_rerank"] for r in records if r["answerable"] and r["top_rerank"] is not None]
    una = [r["top_rerank"] for r in records if not r["answerable"] and r["top_rerank"] is not None]
    return {
        "answerable_min": min(ans, default=None),
        "unanswerable_max": max(una, default=None),
        "answerable_n": len(ans),
        "unanswerable_n": len(una),
        "by_floor": {
            f"{f:.2f}": {
                "answerable_abstained": sum(s < f for s in ans),
                "unanswerable_passed": sum(s >= f for s in una),
            }
            for f in floors
        },
        "answerable_lowest": sorted(
            (r["top_rerank"], r["id"])
            for r in records
            if r["answerable"] and r["top_rerank"] is not None
        )[:5],
        "unanswerable_highest": sorted(
            (
                (r["top_rerank"], r["id"])
                for r in records
                if not r["answerable"] and r["top_rerank"] is not None
            ),
            reverse=True,
        )[:5],
    }


def summarize(records: list[dict], variants: dict) -> dict:
    out = {}
    for label, rows in _slices(records).items():
        out[label] = {name: summarize_variant(rows, name) for name in variants}
    return out


def rank_summary(records: list[dict]) -> dict:
    """Chunk-level rank metrics per slice (#697); variant-independent."""

    def rr(rank):
        return 1.0 / rank if rank else 0.0

    out = {}
    for label, rows in _slices(records).items():
        facts = [r for r in rows if "fact_chunk_rank" in r]
        ranks = [r["fact_chunk_rank"] for r in facts]
        expected = [r.get("expected_chunk_rank") for r in rows if r["answerable"]]
        out[label] = {
            "n_facts": len(facts),
            "fact_chunk_found": sum(1 for x in ranks if x),
            "fact_chunk_top1": sum(1 for x in ranks if x and x <= 1),
            "fact_chunk_top5": sum(1 for x in ranks if x and x <= 5),
            "fact_chunk_top15": sum(1 for x in ranks if x and x <= 15),
            "fact_chunk_mrr": mean(rr(x) for x in ranks) if ranks else None,
            "fact_chunk_median_rank": percentile([x for x in ranks if x], 50),
            "expected_chunk_mrr": mean(rr(x) for x in expected) if expected else None,
        }
    return out


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


def _num(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v:,.{digits}f}"


def render_markdown(meta: dict, summary: dict, floors: dict, records: list[dict]) -> str:
    variants = meta["variants"]
    out = [f"# Ask PIC passage selection eval — {meta['date']}", ""]
    out.append(
        f"Golden set `{meta['golden_path']}` (sha256 `{meta['golden_sha256'][:12]}`; "
        f"{meta['questions']} questions). Database: {meta['database']}. Each question is "
        "retrieved once with `ask_pic.search_passages` (the endpoint's hybrid + rerank call); "
        "every variant is applied to that same ranked list with `ask_pic._passages`. "
        + (
            "Query rewriting (#696) on: one rewrite call per question, then the multi-query "
            "search (RRF over the queries, rerank against the original question). "
            if meta.get("rewrite")
            else "No model call. "
        )
        + f"Spend ${meta['total_cost_usd']:.4f}."
    )
    lat = meta.get("latency_ms") or {}
    if lat:
        out.append("")
        out.append(
            f"Latency p50 / p95 (ms): retrieval {lat.get('retrieval_p50')} / "
            f"{lat.get('retrieval_p95')}"
            + (
                f"; rewrite call {lat.get('rewrite_p50')} / {lat.get('rewrite_p95')}; "
                f"rewrite + retrieval {lat.get('total_p50')} / {lat.get('total_p95')}"
                if meta.get("rewrite")
                else ""
            )
            + "."
        )
    if meta.get("twin_keys"):
        out += ["", f"Twin keys (#713): {meta['twin_keys']}."]
    out += ["", f"Reproduce: `{meta['command']}`", ""]
    out += ["## Variants", ""]
    out += _table(
        [
            "variant",
            "max passages",
            "per-article cap",
            "language dedup",
            "skip covered claims",
            "paragraph replaces claims",
            "twins paired by",
        ],
        [
            [
                name,
                str(v["max_passages"]),
                str(v["per_article_cap"] or "none"),
                "on" if v["language_dedup"] else "off",
                "on" if v["skip_covered_claims"] else "off",
                "on" if v["paragraph_replaces_claims"] else "off",
                "chunk index" if v.get("twin_by_index") else "twin key",
            ]
            for name, v in variants.items()
        ],
    )
    out.append("")
    for label, by_variant in summary.items():
        n = len(_slices(records)[label])
        out += [f"## {label} (n={n})", ""]
        out += _table(
            [
                "variant",
                "passage recall",
                "any expected sent",
                "key facts in passages",
                "passages",
                "articles",
                "prompt tokens (mean / max)",
                "twin pairs",
                "covered claims",
                "max per article (mean)",
            ],
            [
                [
                    name,
                    _pct(s["passage_recall"]),
                    _pct(s["passage_hit"]),
                    f"{_pct(s['key_facts_in_passages'])} ({s['key_facts_in_passages_n']})",
                    _num(s["mean_passages"]),
                    _num(s["mean_articles"]),
                    f"{_num(s['mean_prompt_tokens'], 0)} / {s['max_prompt_tokens']:,}",
                    str(s["twin_pairs"]),
                    str(s["covered_claims"]),
                    _num(s["max_per_article_mean"]),
                ]
                for name, s in by_variant.items()
            ],
        )
        out.append("")
    out += [
        "`passage recall` = share of the expected articles among the passages sent "
        "(answerable only). `key facts in passages` = every `key_facts` string occurs in the "
        "text of the passages sent (substring match, either language). `twin pairs` = EN/ES "
        "twins (same article and twin key, #713) both sent; `covered claims` = claim chunks sent "
        "next to a paragraph of their own article; both summed over the slice.",
        "",
    ]
    out += ["## Chunk ranks (selection-independent)", ""]
    ranks = rank_summary(records)
    out += _table(
        [
            "slice",
            "with key facts",
            "fact chunk in pool",
            "top 1",
            "top 5",
            "top 15",
            "fact chunk MRR",
            "median rank",
            "expected-article chunk MRR",
        ],
        [
            [
                label,
                str(v["n_facts"]),
                str(v["fact_chunk_found"]),
                str(v["fact_chunk_top1"]),
                str(v["fact_chunk_top5"]),
                str(v["fact_chunk_top15"]),
                _num(v["fact_chunk_mrr"], 3),
                _num(v["fact_chunk_median_rank"], 0),
                _num(v["expected_chunk_mrr"], 3),
            ]
            for label, v in ranks.items()
        ],
    )
    out += [
        "",
        "`fact chunk` = the first chunk of the reranked list (before selection) whose own text "
        "holds every key fact; a question whose facts are spread over several chunks has none. "
        "`expected-article chunk` = the first chunk of an expected article.",
        "",
    ]
    out += ["## Operator seeds", ""]
    for r in records:
        if r["source"] != "operator":
            continue
        out += [f"**{r['id']}** ({r['language']}) {r['question']} — key facts {r['key_facts']}", ""]
        rows = []
        for name in variants:
            v = r["variants"][name]
            rows.append(
                [
                    name,
                    " ".join(v["passages"]),
                    ", ".join(v.get("key_facts_found", [])) or "—",
                ]
            )
        out += _table(
            ["variant", "passages sent (article, lang, #chunk or c=claim)", "facts"], rows
        )
        out.append("")
    out += ["## Abstain floor (top-1 rerank, selection-independent)", ""]
    out.append(
        f"Answerable: {floors['answerable_n']} questions, lowest top-1 "
        f"{_num(floors['answerable_min'], 3)}; unanswerable: {floors['unanswerable_n']}, "
        f"highest {_num(floors['unanswerable_max'], 3)}. Current floor "
        f"{meta['settings']['ask_pic_min_rerank_score']}."
    )
    out.append("")
    out += _table(
        ["floor", "answerable abstained", "unanswerable passed"],
        [
            [f, str(v["answerable_abstained"]), str(v["unanswerable_passed"])]
            for f, v in floors["by_floor"].items()
        ],
    )
    out += [
        "",
        "Lowest answerable: "
        + ", ".join(f"{i} {s:.3f}" for s, i in floors["answerable_lowest"])
        + ". Highest unanswerable: "
        + ", ".join(f"{i} {s:.3f}" for s, i in floors["unanswerable_highest"])
        + ".",
        "",
    ]
    return "\n".join(out)


def write_results(
    meta: dict, summary: dict, floors: dict, records: list[dict], *, out_dir: Path
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta['date']}-selection" + (f"-{meta['label']}" if meta.get("label") else "")
    md = out_dir / f"{stem}.md"
    js = out_dir / f"{stem}.json"
    md.write_text(render_markdown(meta, summary, floors, records), encoding="utf-8")
    js.write_text(
        json.dumps(
            {
                "meta": meta,
                "summary": summary,
                "ranks": rank_summary(records),
                "floor": floors,
                "records": records,
            },
            ensure_ascii=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return md, js


def variants_meta(variants: dict) -> dict:
    return {name: asdict(v) for name, v in variants.items()}


def latency_meta(records: list[dict]) -> dict:
    """p50 / p95 of retrieval, the rewrite call and their sum, over every question."""
    retrieval = [r.get("retrieval_ms") for r in records]
    rewrite = [r.get("rewrite_ms") for r in records if r.get("rewrite_ms") is not None]
    total = [
        (r.get("retrieval_ms") or 0) + (r.get("rewrite_ms") or 0)
        for r in records
        if r.get("retrieval_ms") is not None
    ]
    return {
        "retrieval_p50": percentile(retrieval, 50),
        "retrieval_p95": percentile(retrieval, 95),
        "rewrite_p50": percentile(rewrite, 50),
        "rewrite_p95": percentile(rewrite, 95),
        "total_p50": percentile(total, 50),
        "total_p95": percentile(total, 95),
    }


__all__ = [
    "DEFAULT_OUT_DIR",
    "DEFAULT_VARIANTS",
    "VARIANTS",
    "chunk_ranks",
    "evaluate_question",
    "floor_report",
    "latency_meta",
    "rank_summary",
    "run",
    "summarize",
    "variants_meta",
    "write_results",
]
