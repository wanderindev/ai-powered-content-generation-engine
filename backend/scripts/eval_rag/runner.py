"""The retrieval eval: golden questions x models x modes through ``retrieval.search`` (#627).

Each (model, mode) runs every golden question against that model's eval table
(:mod:`scripts.eval_rag.tables`) with ``visibility="public"`` (the Ask PIC
corpus), the question's ``search_languages``, and the query embedded with the
same model as the table. ``fts`` does not depend on the model, so it runs once,
against the first model's table (every eval table holds the same rows).

Per question it records the ranked article ids, the metrics
(:mod:`scripts.eval_rag.metrics`), latency (``timings_ms``), the billed Voyage
tokens and cost (the seam's usage counter, before/after the call), and four
confidence signals for the abstain floor:

* ``top1_vector_similarity`` — ``1 - cosine distance`` of the closest chunk;
* ``top1_fts_score`` — the best ``ts_rank``;
* ``top1_rrf_score`` — the fused score of the first chunk (hybrid);
* ``top1_rerank_score`` — the reranker's score of the first chunk.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from app.core.config import get_settings
from app.services import embeddings
from app.services.knowledge import retrieval
from app.services.knowledge.chunker import estimate_tokens
from sqlalchemy.orm import Session

from .golden import GoldenQuestion, GoldenSet, search_languages
from .metrics import distribution, floor_candidates, mean, percentile, question_metrics

MODE_SPECS: dict[str, tuple[str, bool]] = {
    "vector": ("vector", False),
    "fts": ("fts", False),
    "hybrid": ("hybrid", False),
    "hybrid+rerank": ("hybrid", True),
}
MODES = tuple(MODE_SPECS)
DEFAULT_MODELS = ("voyage-4-lite", "voyage-4")
FTS_MODEL_LABEL = "(any)"
METRIC_KEYS = ("recall@1", "recall@5", "recall@10", "mrr", "ndcg@10")
# The signals that can set the floor for each mode.
MODE_SIGNALS = {
    "vector": ("top1_vector_similarity",),
    "fts": ("top1_fts_score",),
    "hybrid": ("top1_rrf_score", "top1_vector_similarity"),
    "hybrid+rerank": ("top1_rerank_score", "top1_vector_similarity"),
}


def uses_embeddings(mode: str) -> bool:
    return mode != "fts"


def runs(models: list[str], modes: list[str]) -> list[tuple[str, str]]:
    """The (model label, mode) pairs, with ``fts`` once."""
    pairs = []
    for mode in modes:
        if uses_embeddings(mode):
            pairs += [(model, mode) for model in models]
        else:
            pairs.append((FTS_MODEL_LABEL, mode))
    return pairs


def _usage_delta(before: dict, after: dict) -> dict[tuple[str, str], int]:
    return {
        key: after[key] - before.get(key, 0) for key in after if after[key] != before.get(key, 0)
    }


def _signals(result: retrieval.SearchResult) -> dict[str, float | None]:
    chunks = result.chunks
    sims = [1.0 - c.vector_distance for c in chunks if c.vector_distance is not None]
    fts = [c.fts_score for c in chunks if c.fts_score is not None]
    first = chunks[0] if chunks else None
    return {
        "top1_vector_similarity": max(sims) if sims else None,
        "top1_fts_score": max(fts) if fts else None,
        "top1_rrf_score": first.rrf_score if first else None,
        "top1_rerank_score": first.rerank_score if first else None,
    }


def _section_rank(result: retrieval.SearchResult, question: GoldenQuestion) -> int | None:
    """Rank of the first chunk from an expected article whose heading matches the section."""
    section = (question.expected_section or "").strip().lower()
    if not section:
        return None
    expected = set(question.expected_article_ids)
    for chunk in result.chunks:
        if chunk.article_id in expected and section in (chunk.heading_path or "").lower():
            return chunk.rank
    return None


def run_question(
    db: Session,
    question: GoldenQuestion,
    *,
    model: str,
    mode: str,
    table,
    k: int,
    candidates: int,
) -> dict:
    """One search for one question; returns the per-question record."""
    search_mode, rerank = MODE_SPECS[mode]
    before = embeddings.get_usage_totals()
    result = retrieval.search(
        db,
        question.question,
        mode=search_mode,
        rerank=rerank,
        k=k,
        languages=search_languages(question),
        visibility="public",
        candidates=candidates,
        table=table,
        embedding_model=model if uses_embeddings(mode) else None,
    )
    db.rollback()  # read-only; release the connection between calls
    delta = _usage_delta(before, embeddings.get_usage_totals())
    embed_tokens = sum(n for (endpoint, _), n in delta.items() if endpoint == "embeddings")
    rerank_tokens = sum(n for (endpoint, _), n in delta.items() if endpoint == "rerank")
    cost = sum(embeddings.estimate_cost(m, n) for (_, m), n in delta.items())
    ranked = [hit.article_id for hit in result.results]
    return {
        "question_id": question.id,
        "language": question.language,
        "type": question.type,
        "model": model,
        "mode": mode,
        "expected_article_ids": list(question.expected_article_ids),
        "ranked_article_ids": ranked,
        "metrics": question_metrics(ranked, question.expected_article_ids),
        "section_rank": _section_rank(result, question),
        "signals": _signals(result),
        "latency_ms": result.timings_ms.get("total"),
        "timings_ms": dict(result.timings_ms),
        "embed_tokens": embed_tokens,
        "rerank_tokens": rerank_tokens,
        "cost_usd": cost,
        "degraded": list(result.degraded),
        "reranked": result.reranked,
    }


def run_eval(
    db: Session,
    golden: GoldenSet,
    *,
    models: list[str],
    modes: list[str],
    table_for: Callable[[str], object],
    k: int = retrieval.DEFAULT_K,
    candidates: int = retrieval.DEFAULT_CANDIDATES,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Every question through every (model, mode); returns the per-question records."""
    records = []
    for model, mode in runs(models, modes):
        table = table_for(models[0] if model == FTS_MODEL_LABEL else model)
        if progress:
            progress(f"{model} / {mode}: {len(golden.questions)} questions")
        for question in golden.questions:
            records.append(
                run_question(
                    db, question, model=model, mode=mode, table=table, k=k, candidates=candidates
                )
            )
    return records


def _group(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        groups[(record["model"], record["mode"])].append(record)
    return groups


def summarize(records: list[dict]) -> dict:
    """Aggregate rows (model x mode, and x type), and the abstain-floor analysis."""
    rows = []
    by_type = []
    abstain = []
    for (model, mode), group in _group(records).items():
        answerable = [r for r in group if r["type"] != "unanswerable"]
        latencies = [r["latency_ms"] for r in group if r["latency_ms"] is not None]
        row = {
            "model": model,
            "mode": mode,
            "questions": len(group),
            "answerable": len(answerable),
            **{key: mean(r["metrics"][key] for r in answerable) for key in METRIC_KEYS},
            "latency_p50_ms": percentile(latencies, 50),
            "latency_p95_ms": percentile(latencies, 95),
            "embed_tokens_per_query": mean(r["embed_tokens"] for r in group),
            "rerank_tokens_per_query": mean(r["rerank_tokens"] for r in group),
            "cost_usd_per_query": mean(r["cost_usd"] for r in group),
            "degraded_queries": sum(bool(r["degraded"]) for r in group),
        }
        rows.append(row)
        types: dict[str, list[dict]] = defaultdict(list)
        for r in answerable:
            types[r["type"]].append(r)
        for qtype, members in sorted(types.items()):
            by_type.append(
                {
                    "model": model,
                    "mode": mode,
                    "type": qtype,
                    "questions": len(members),
                    **{key: mean(r["metrics"][key] for r in members) for key in METRIC_KEYS},
                }
            )
        unanswerable = [r for r in group if r["type"] == "unanswerable"]
        for signal in MODE_SIGNALS[mode]:
            pos = [r["signals"][signal] for r in answerable]
            neg = [r["signals"][signal] for r in unanswerable]
            abstain.append(
                {
                    "model": model,
                    "mode": mode,
                    "signal": signal,
                    "answerable": distribution(pos),
                    "unanswerable": distribution(neg),
                    "floors": floor_candidates(pos, neg),
                    "per_unanswerable": {
                        r["question_id"]: r["signals"][signal] for r in unanswerable
                    },
                }
            )
    return {"rows": rows, "by_type": by_type, "abstain": abstain}


def estimate_query_cost(
    golden: GoldenSet, models: list[str], modes: list[str], *, avg_chunk_tokens: float
) -> dict:
    """Estimated Voyage tokens / USD for the query side of a run (indexing is separate)."""
    q_tokens = sum(estimate_tokens(q.question) for q in golden.questions)
    n = len(golden.questions)
    embed_runs = sum(1 for mode in modes if uses_embeddings(mode))
    rerank_runs = sum(1 for mode in modes if MODE_SPECS[mode][1])
    rerank_model = get_settings().rerank_model
    embed_cost = sum(embeddings.estimate_cost(m, q_tokens * embed_runs) for m in models)
    rerank_tokens = int(
        n * retrieval.RERANK_TOP_N * (avg_chunk_tokens + q_tokens / max(n, 1)) * rerank_runs
    ) * len(models)
    return {
        "query_embed_tokens_est": q_tokens * embed_runs * len(models),
        "rerank_tokens_est": rerank_tokens,
        "cost_usd_est": round(
            embed_cost + embeddings.estimate_cost(rerank_model, rerank_tokens), 6
        ),
    }
