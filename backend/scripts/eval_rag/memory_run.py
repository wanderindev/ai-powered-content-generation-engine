"""Pipeline-memory eval run: B4 contradictions + B3 duplicates over a seed file (#632).

**Writes nothing to the database.** Every network phase (Voyage, Sonnet) runs
after a rollback, so no connection is held through it; the CLI hands this
module a ``postgresql_readonly`` session.

**B4 — contradictions** (:mod:`app.services.consistency_check`, reused, not
re-implemented):

1. every item's text in one ``embeddings.embed_queries`` call, as #631 does;
2. ``consistency_check._gather_pairs`` with the production ``top_k`` and the
   **lowest** floor of the sweep, and ``exclude_article_ids=[]``. Production
   excludes the article being checked because its own claims are the input;
   here the seeded claim is *new* text and the point is to meet the fact it
   was derived from, so its source article stays in scope. Retrieval returns
   chunks most-similar first and the floor only cuts the tail, so the pairs at
   any higher floor ``f`` are exactly this run's pairs with similarity
   ``>= f``: one retrieval and one judge pass serve the whole sweep;
3. the real Sonnet judge (``consistency_check._judge``, same prompt and
   parser, ``feature="rag_eval_memory_judge"``) in batches of
   ``consistency_batch_pairs``, pair order shuffled with a fixed seed so a
   perturbed and a paraphrased variant of related facts rarely share a batch.

An item is **flagged** at floor ``f`` when any of its pairs at similarity
``>= f`` is judged ``CONTRADICTS`` (what would land in
``consistency_findings``). Positives are the ``perturbed`` items. Also
reported: the **retrieval-hit rate** (the source claim's chunk is among the
item's pairs at ``f``) and the source chunk's similarity itself (read
directly, regardless of ``top_k``), judge accuracy on the source pair alone,
and judge tokens / cost per item (each batch's tokens split evenly over its
pairs).

**B3 — duplicates** (:mod:`app.services.suggestion_dedup`): each item's
``suggestion_text(title, main_topic)`` embedded as a *document* (one
``embed_documents`` call) and compared with every article centroid through
``related.nearest_articles(k=1, visibility="internal")``, which is the
article half of ``flag_duplicates``. The suggestion-to-suggestion half is
left out: it compares with unwritten pitches, not with published articles.
An item is flagged at threshold ``t`` when the best similarity is ``>= t``;
positives are the ``near_duplicate`` items. A flag on a near-duplicate counts
as a hit whichever article it points at; how often it points at the source
article is reported separately.

**Selection rule** (both): :func:`metrics.select_threshold`, max F1 with
precision >= ``min_precision`` (0.9), ties to the higher value.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from types import SimpleNamespace

from app.core.config import get_settings
from app.models.article import Article
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import consistency_check as cc
from app.services import embeddings
from app.services.knowledge import related
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import metrics
from .drafting import SONNET_INPUT_PRICE_PER_MTOK, SONNET_OUTPUT_PRICE_PER_MTOK

EMBED_FEATURE = "rag_eval_memory"
JUDGE_FEATURE = "rag_eval_memory_judge"
DEFAULT_FLOORS = metrics.threshold_grid(0.30, 0.70, 0.05)
DEFAULT_THRESHOLDS = metrics.threshold_grid(0.70, 0.95, 0.025)
MIN_PRECISION = 0.9
# Recommendation bars (stated in the report next to the numbers).
B3_MIN_RECALL = 0.6
B4_MIN_PRECISION = 0.8
B4_MIN_RECALL = 0.5
# Dry-run judge estimate: tokens per pair, and per-batch prompt overhead.
_PAIR_INPUT_TOKENS = 90
_PAIR_OUTPUT_TOKENS = 40
_BATCH_OVERHEAD_TOKENS = 350
_ARTICLE_CLAIM = cc._ARTICLE_CLAIM

Progress = Callable[[str], None]


def _sonnet_cost(tokens_in: float, tokens_out: float) -> float:
    return (
        tokens_in * SONNET_INPUT_PRICE_PER_MTOK + tokens_out * SONNET_OUTPUT_PRICE_PER_MTOK
    ) / 1e6


def estimate(seed: dict, *, top_k: int, batch_pairs: int) -> dict:
    """Dry-run cost estimate (upper bound on pairs: ``top_k`` per contradiction item)."""
    contra = seed["contradictions"]
    dups = seed["duplicates"]
    pairs = len(contra) * top_k
    batches = -(-pairs // max(1, batch_pairs)) if pairs else 0
    tokens_in = pairs * _PAIR_INPUT_TOKENS + batches * _BATCH_OVERHEAD_TOKENS
    tokens_out = pairs * _PAIR_OUTPUT_TOKENS
    voyage_tokens = sum(len(i["text"]) // 3 + 1 for i in contra + dups)
    model = embeddings.document_model()
    return {
        "contradiction_items": len(contra),
        "duplicate_items": len(dups),
        "pairs_max": pairs,
        "judge_calls_max": batches,
        "judge_input_tokens_est": tokens_in,
        "judge_output_tokens_est": tokens_out,
        "judge_cost_usd_est": round(_sonnet_cost(tokens_in, tokens_out), 4),
        "voyage_tokens_est": voyage_tokens,
        "voyage_cost_usd_est": round(embeddings.estimate_cost(model, voyage_tokens), 6),
    }


def _subset_rates(items: list[dict], predicted_key: Callable[[dict], bool], positive: str) -> dict:
    expected = [i["expected_label"] == positive for i in items]
    counts = metrics.confusion(expected, [predicted_key(i) for i in items])
    return {"n": len(items), **counts, **metrics.precision_recall_f1(counts)}


# --- B3 -------------------------------------------------------------------------------


def run_duplicates(
    db: Session,
    items: list[dict],
    *,
    thresholds: list[float],
    min_precision: float = MIN_PRECISION,
    progress: Progress = print,
) -> dict:
    db.rollback()  # no connection held through the Voyage call
    vectors = embeddings.embed_documents([i["text"] for i in items], feature=EMBED_FEATURE)
    progress(f"B3: embedded {len(items)} topics; nearest article centroid per topic")
    records = []
    for item, vector in zip(items, vectors, strict=True):
        hits = related.nearest_articles(db, vector, k=1, visibility="internal")
        best = hits[0] if hits else None
        records.append(
            {
                "id": item["id"],
                "type": item["type"],
                "language": item["language"],
                "text": item["text"],
                "expected_label": item["expected_label"],
                "source_article_id": item.get("source_article_id"),
                "best_article_id": best.article_id if best else None,
                "similarity": round(best.similarity, 6) if best else None,
                "review": item.get("review"),
                "suspect": item.get("suspect"),
            }
        )
    ids = {r["best_article_id"] for r in records if r["best_article_id"]}
    titles = (
        dict(db.execute(select(Article.id, Article.title).where(Article.id.in_(ids))).all())
        if ids
        else {}
    )
    db.rollback()
    for r in records:
        r["best_article_title"] = titles.get(r["best_article_id"])
        r["source_match"] = (
            r["best_article_id"] == r["source_article_id"]
            if r["type"] == "near_duplicate"
            else None
        )

    scores = [r["similarity"] for r in records]
    expected = [r["expected_label"] == "DUPLICATE" for r in records]
    sweep = metrics.threshold_sweep(scores, expected, thresholds)
    selection = metrics.select_threshold(sweep, min_precision=min_precision)
    current = get_settings().suggestion_dedup_min_similarity
    current_row = metrics.threshold_sweep(scores, expected, [current])[0]
    chosen = selection["row"]["threshold"] if selection["row"] else current

    def flagged_at(t):
        return lambda r: r["similarity"] is not None and r["similarity"] >= t

    by_language = {
        lang: _subset_rates(
            [r for r in records if r["language"] == lang], flagged_at(chosen), "DUPLICATE"
        )
        for lang in ("en", "es")
    }
    near = [r for r in records if r["type"] == "near_duplicate"]
    novel = [r for r in records if r["type"] == "novel"]
    return {
        "items": len(records),
        "near_duplicates": len(near),
        "novel": len(novel),
        "novel_unreviewed": sum(r["review"] == "pending" for r in novel),
        "novel_suspect": sum(bool(r["suspect"]) for r in novel),
        "sweep": sweep,
        "selection": selection,
        "current_default": current_row,
        "chosen_threshold": chosen,
        "by_language": by_language,
        "source_match_rate": metrics.mean([1.0 if r["source_match"] else 0.0 for r in near]),
        "similarity": {
            "near_duplicate": metrics.distribution([r["similarity"] for r in near]),
            "novel": metrics.distribution([r["similarity"] for r in novel]),
        },
        "records": records,
    }


# --- B4 -------------------------------------------------------------------------------


def _source_similarity(db: Session, chunk_id: int, vector: list[float]) -> float | None:
    c = KnowledgeChunk.__table__.c
    distance = db.execute(
        select(c.embedding.cosine_distance(vector)).where(
            c.id == chunk_id, c.embedding.is_not(None)
        )
    ).scalar()
    return None if distance is None else 1.0 - float(distance)


def run_contradictions(
    db: Session,
    items: list[dict],
    *,
    floors: list[float],
    min_precision: float = MIN_PRECISION,
    shuffle_seed: int = 632,
    progress: Progress = print,
) -> dict:
    settings = get_settings()
    configured = settings.consistency_min_similarity
    top_k = settings.consistency_top_k
    batch_size = max(1, settings.consistency_batch_pairs)
    floor_grid = sorted(set(floors) | {configured})
    retrieval_floor = floor_grid[0]

    db.rollback()
    vectors = embeddings.embed_queries([i["text"] for i in items], feature=EMBED_FEATURE)
    gather_settings = SimpleNamespace(
        consistency_min_similarity=retrieval_floor,
        consistency_top_k=top_k,
        consistency_max_pairs=len(items) * top_k,  # the per-article cap never binds here
    )
    claims = [(index, item["text"]) for index, item in enumerate(items)]
    pairs = cc._gather_pairs(db, claims, vectors, [], gather_settings)
    source_sims = [
        _source_similarity(db, item["source_chunk_id"], vector)
        for item, vector in zip(items, vectors, strict=True)
    ]
    db.rollback()  # nothing below touches the database
    progress(
        f"B4: {len(pairs)} pairs at floor >= {retrieval_floor:g} (top_k {top_k}); "
        f"judging in batches of {batch_size}"
    )

    order = list(range(len(pairs)))
    random.Random(shuffle_seed).shuffle(order)
    judged: dict[int, dict] = {}
    tokens_in = tokens_out = failed = calls = 0
    for start in range(0, len(order), batch_size):
        batch_idx = order[start : start + batch_size]
        batch = [pairs[i] for i in batch_idx]
        calls += 1
        try:
            verdicts, used_in, used_out = cc._judge(batch, feature=JUDGE_FEATURE)
        except Exception as exc:  # noqa: BLE001 — a failed batch is isolated and counted
            failed += 1
            progress(
                f"  judge batch {calls} failed ({type(exc).__name__}); its pairs count as unjudged"
            )
            for i in batch_idx:
                judged[i] = {
                    "label": None,
                    "explanation": None,
                    "tokens_in": 0.0,
                    "tokens_out": 0.0,
                }
            continue
        tokens_in += used_in
        tokens_out += used_out
        for pos, i in enumerate(batch_idx):
            label, explanation = verdicts.get(pos, (cc._UNRELATED, ""))
            judged[i] = {
                "label": label,
                "explanation": explanation,
                "tokens_in": used_in / len(batch),
                "tokens_out": used_out / len(batch),
            }

    per_item: list[list[dict]] = [[] for _ in items]
    for i, pair in enumerate(pairs):
        item = items[pair.claim_id]
        verdict = judged[i]
        per_item[pair.claim_id].append(
            {
                "source_type": pair.ref.source_type,
                "source_id": pair.ref.source_id,
                "article_id": pair.ref.article_id,
                "similarity": round(pair.ref.similarity, 6),
                "fact": pair.ref.fact,
                "falsehood": pair.ref.falsehood,
                "is_source": pair.ref.source_type == _ARTICLE_CLAIM
                and pair.ref.source_id == item["source_claim_id"],
                "label": verdict["label"],
                "explanation": verdict["explanation"],
                "cost_usd": _sonnet_cost(verdict["tokens_in"], verdict["tokens_out"]),
                "tokens_in": verdict["tokens_in"],
                "tokens_out": verdict["tokens_out"],
            }
        )

    records = []
    for item, item_pairs, source_sim in zip(items, per_item, source_sims, strict=True):
        records.append(
            {
                "id": item["id"],
                "type": item["type"],
                "language": item["language"],
                "perturbation": item.get("perturbation"),
                "text": item["text"],
                "original_text": item["original_text"],
                "expected_label": item["expected_label"],
                "source_article_id": item["source_article_id"],
                "source_claim_id": item["source_claim_id"],
                "source_similarity": None if source_sim is None else round(source_sim, 6),
                "pairs": item_pairs,
                "tokens_in": sum(p["tokens_in"] for p in item_pairs),
                "tokens_out": sum(p["tokens_out"] for p in item_pairs),
                "cost_usd": sum(p["cost_usd"] for p in item_pairs),
            }
        )

    def at(floor):
        def flagged(r):
            return any(
                p["label"] == cc._CONTRADICTS and p["similarity"] >= floor for p in r["pairs"]
            )

        return flagged

    def row(floor: float) -> dict:
        expected = [r["expected_label"] == "CONTRADICTS" for r in records]
        counts = metrics.confusion(expected, [at(floor)(r) for r in records])
        kept = [[p for p in r["pairs"] if p["similarity"] >= floor] for r in records]
        hits = [any(p["is_source"] for p in k) for k in kept]
        return {
            "threshold": floor,
            **counts,
            **metrics.precision_recall_f1(counts),
            "source_hit_rate": metrics.mean([1.0 if h else 0.0 for h in hits]),
            "pairs_per_item": metrics.mean([len(k) for k in kept]),
            "judge_cost_per_item_usd": metrics.mean([sum(p["cost_usd"] for p in k) for k in kept]),
        }

    sweep = [row(f) for f in floor_grid]
    selection = metrics.select_threshold(sweep, min_precision=min_precision)
    chosen = selection["row"]["threshold"] if selection["row"] else configured
    for r in records:
        r["flagged_at_configured"] = at(configured)(r)
        r["flagged_at_chosen"] = at(chosen)(r)

    # The judge alone: the source pair's label, where the source was retrieved at all.
    with_source = []
    for r in records:
        source_pair = next((p for p in r["pairs"] if p["is_source"]), None)
        if source_pair and source_pair["label"] is not None:
            with_source.append(source_pair["label"] == r["expected_label"])
    by_language = {
        lang: _subset_rates(
            [r for r in records if r["language"] == lang], at(chosen), "CONTRADICTS"
        )
        for lang in ("en", "es")
    }
    by_perturbation = {}
    for kind in sorted({r["perturbation"] for r in records if r["perturbation"]}):
        subset = [r for r in records if r["perturbation"] == kind]
        by_perturbation[kind] = {
            "n": len(subset),
            "recall": metrics.mean([1.0 if at(chosen)(r) else 0.0 for r in subset]),
        }
    voyage = embeddings.get_usage_totals()
    return {
        "items": len(records),
        "perturbed": sum(r["type"] == "perturbed" for r in records),
        "paraphrase": sum(r["type"] == "paraphrase" for r in records),
        "top_k": top_k,
        "batch_pairs": batch_size,
        "configured_floor": configured,
        "retrieval_floor": retrieval_floor,
        "pairs": len(pairs),
        "judge_calls": calls,
        "failed_batches": failed,
        "judge_input_tokens": tokens_in,
        "judge_output_tokens": tokens_out,
        "judge_cost_usd": _sonnet_cost(tokens_in, tokens_out),
        "sweep": sweep,
        "selection": selection,
        "configured_row": next(r for r in sweep if r["threshold"] == configured),
        "chosen_floor": chosen,
        "by_language": by_language,
        "by_perturbation": by_perturbation,
        "judge_source_pair_accuracy": metrics.mean([1.0 if ok else 0.0 for ok in with_source]),
        "judge_source_pairs": len(with_source),
        "source_similarity": {
            kind: metrics.distribution(
                [r["source_similarity"] for r in records if r["type"] == kind]
            )
            for kind in ("perturbed", "paraphrase")
        },
        "source_missing": sum(r["source_similarity"] is None for r in records),
        "voyage_tokens": {f"{e}:{m}": n for (e, m), n in voyage.items()},
        "records": records,
    }


# --- Recommendation -------------------------------------------------------------------


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def recommend(b3: dict | None, b4: dict | None) -> dict:
    """Recommendation lines generated from the numbers (the switch is the operator's call)."""
    out = {}
    settings = get_settings()
    if b3 is not None:
        sel = b3["selection"]
        row = sel["row"]
        if row is None:
            out["b3"] = {
                "switch_on": False,
                "text": "No usable threshold (no positives or no flags).",
            }
        else:
            t, p, r = row["threshold"], row["precision"], row["recall"]
            if sel["met_precision"] and r is not None and r >= B3_MIN_RECALL:
                text = (
                    f"Switch on: SUGGESTION_DEDUP_ENABLED=true with "
                    f"SUGGESTION_DEDUP_MIN_SIMILARITY={t:g}. At that threshold precision is "
                    f"{_pct(p)} and recall {_pct(r)} (bars: precision >= {MIN_PRECISION:.0%}, "
                    f"recall >= {B3_MIN_RECALL:.0%}). The flag is advisory, so a false flag "
                    "costs a glance, not a lost suggestion."
                )
                on = True
            elif sel["met_precision"]:
                text = (
                    f"Low-risk but weak: at {t:g} precision is {_pct(p)} but recall only "
                    f"{_pct(r)} (< {B3_MIN_RECALL:.0%}). Switching on is safe (advisory) "
                    "but would miss most duplicates; keep off unless that is acceptable."
                )
                on = False
            else:
                text = (
                    f"Keep off: no threshold reaches precision >= {MIN_PRECISION:.0%}; the "
                    f"best F1 is at {t:g} (precision {_pct(p)}, recall {_pct(r)})."
                )
                on = False
            out["b3"] = {"switch_on": on, "threshold": t, "text": text}
    if b4 is not None:
        sel = b4["selection"]
        row = sel["row"]
        if row is None:
            out["b4"] = {"switch_on": False, "text": "No usable floor (no flags at any floor)."}
        else:
            f, p, r = row["threshold"], row["precision"], row["recall"]
            per_article = (row["judge_cost_per_item_usd"] or 0.0) * settings.consistency_max_claims
            cost = (
                f"Judge cost about ${row['judge_cost_per_item_usd'] or 0:.4f} per claim, so at most "
                f"~${per_article:.2f} per article at consistency_max_claims="
                f"{settings.consistency_max_claims}."
            )
            if p is not None and r is not None and p >= B4_MIN_PRECISION and r >= B4_MIN_RECALL:
                text = (
                    f"Switch on (shadow mode): CROSS_ARTICLE_CONSISTENCY_ENABLED=true with "
                    f"CONSISTENCY_MIN_SIMILARITY={f:g}. Precision {_pct(p)}, recall {_pct(r)}, "
                    f"source retrieved for {_pct(row['source_hit_rate'])} of items (bars: "
                    f"precision >= {B4_MIN_PRECISION:.0%}, recall >= {B4_MIN_RECALL:.0%}). " + cost
                )
                on = True
            else:
                text = (
                    f"Keep off: at the best floor {f:g} precision is {_pct(p)} and recall "
                    f"{_pct(r)} (bars: precision >= {B4_MIN_PRECISION:.0%}, recall >= "
                    f"{B4_MIN_RECALL:.0%}). " + cost
                )
                on = False
            out["b4"] = {"switch_on": on, "floor": f, "text": text}
    return out
