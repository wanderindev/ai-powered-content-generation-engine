"""Sweep articles and claims into ``knowledge_chunks`` (#624, #625, epic #617).

Runs :func:`app.services.knowledge.indexer.index_article` and
:func:`app.services.knowledge.claims.index_article_claims` over every article
(``--all``, any status: visibility is decided at query time) or one
(``--article N``); ``--all`` also syncs the verified-claims KB
(:func:`~app.services.knowledge.claims.index_verified_claims`). It is idempotent: chunks are matched by content hash, so an
unchanged corpus makes **zero** embedding calls and no writes. That makes it
both the first backfill and the repair pass for any reindex a background task
missed (a failed Voyage call, a write path that predates the indexer, a script
that edited ``content`` directly, and the ``/fact-check-process`` skill's SQL
fallback, which writes claim verdicts and the KB with direct SQL; its default
MCP path re-indexes through ``reindex_claims``).

``--dry-run`` calls neither Voyage nor any write, needs no ``VOYAGE_API_KEY``,
and reports per-article chunk counts (body and claims), how many chunks would be (re-)embedded,
and the estimated tokens and cost. Tokens are the chunker's ``ceil(chars / 4)``
estimate; the price is the embeddings seam's table.

**Situating sentences (#697).** With an ``ANTHROPIC_API_KEY``, every article
chunk without a ``meta["context"]`` sentence is given one (one Haiku call per
article language) and re-embedded with it, so the first ``--all`` after #697
re-embeds every article chunk: that is the one-time contextual reindex. The
dry run also reports how many sentences, Haiku calls, the estimated Haiku cost
and an estimated duration. Each article is swapped in one transaction (old rows
deleted and new ones inserted together), so the site can stay live: related
articles, KB matching and dedup never see an article half-indexed. Stopping
and re-running resumes: done articles are ``unchanged``. Claims and the KB get
no sentence (claim-to-claim matching stays on the bare claim).

**Sentence-less chunks (#712).** A chunk whose situating call returned no
sentence is retried on later runs up to ``context.MAX_ATTEMPTS`` times per
content hash, then left without one (and its article counts as ``unchanged``).
Every run reports how many article chunks have no sentence and how many of
those are out of attempts (for ``--dry-run``: as they stand now).
``--retry-context`` asks again for those chunks and restarts their count.

**Dead tuples (#711, #728).** Only rows whose stored values changed are
written: a kept row with the same content, meta and position is never
``UPDATE``d, so a run reports ``updated=`` next to ``kept=`` and leaves about
``deleted + updated`` dead tuples. After a real run the sweep logs
``knowledge_chunks``' ``n_dead_tup`` and, at :data:`VACUUM_DEAD_TUPLES` or
more, runs ``VACUUM (ANALYZE) knowledge_chunks`` itself (an ``AUTOCOMMIT``
connection: VACUUM cannot run in a transaction). Background reindexes never
vacuum; they write a few rows and autovacuum covers them.

Usage (from ``backend/``):
    python -m scripts.reindex_knowledge --all --dry-run       # Preview the backfill
    python -m scripts.reindex_knowledge --all                 # Index everything changed
    python -m scripts.reindex_knowledge --article 245         # One article (body + claims)
    python -m scripts.reindex_knowledge --all --workers 3     # Articles in parallel (max 4)
    python -m scripts.reindex_knowledge --all --retry-context # Retry chunks out of attempts
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from app.core.database import SessionLocal
from app.models.article import Article
from app.services import embeddings
from app.services.knowledge import context as chunk_context
from app.services.knowledge.claims import index_article_claims, index_verified_claims
from app.services.knowledge.indexer import IndexResult, index_article
from sqlalchemy import select, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Measured on the #697 eval build (113 articles, 226 Haiku calls: 7.7 min with
# 3 workers): seconds of wall time per situating call, embedding and write included.
SECONDS_PER_CONTEXT_CALL = 6.0
# Each worker opens its own sessions; the pool allows 2 + 3 overflow per process.
MAX_WORKERS = 4
# A real sweep that leaves this many dead knowledge_chunks tuples vacuums the
# table at the end (#728). Below it autovacuum's own threshold (50 + 0.2 x live
# rows, ~2,870 at 14k rows) eventually covers them.
VACUUM_DEAD_TUPLES = 1000


def article_ids(article_id: int | None = None) -> list[int]:
    """The ids to sweep: one article, or every article in id order."""
    if article_id is not None:
        return [article_id]
    with SessionLocal() as db:
        return list(db.execute(select(Article.id).order_by(Article.id)).scalars())


def _one(article_id: int, dry_run: bool, retry_context: bool = False) -> list[IndexResult]:
    return [
        index_article(article_id, dry_run=dry_run, retry_context=retry_context),
        index_article_claims(article_id, dry_run=dry_run),
    ]


def sweep(
    ids: list[int],
    *,
    dry_run: bool = False,
    verified: bool = False,
    workers: int = 1,
    retry_context: bool = False,
) -> list[IndexResult]:
    """Index each article's body and claims, then (``verified``) the KB.

    Each run opens and closes its own sessions, so ``workers`` > 1 indexes
    that many articles at once (the article row lock serialises any overlap).
    ``retry_context`` retries chunks out of situating attempts (#712).
    """
    results = []
    if workers > 1 and len(ids) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for pair in pool.map(lambda aid: _one(aid, dry_run, retry_context), ids):
                results.extend(pair)
    else:
        for article_id in ids:
            results.extend(_one(article_id, dry_run, retry_context))
    if verified:
        results.append(index_verified_claims(dry_run=dry_run))
    return results


def dead_tuples() -> tuple[int, int] | None:
    """(n_live_tup, n_dead_tup) of ``knowledge_chunks`` (estimates), or None."""
    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT n_live_tup, n_dead_tup FROM pg_stat_user_tables "
                "WHERE relname = 'knowledge_chunks'"
            )
        ).first()
    return (int(row[0]), int(row[1])) if row else None


def vacuum_knowledge_chunks() -> None:
    """``VACUUM (ANALYZE) knowledge_chunks`` on an AUTOCOMMIT connection (#711, #728)."""
    with SessionLocal() as db:
        bind = db.get_bind()
    with bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM (ANALYZE) knowledge_chunks"))


def report_dead_tuples(written: int) -> bool:
    """Log the table's dead tuples after a real sweep; vacuum above the threshold.

    ``written`` is the dead tuples this sweep made (rows deleted + updated); the
    statistics can lag a moment behind the commit, so the larger of the two
    decides. Returns whether it vacuumed. Never raises: the sweep's writes are
    already committed.
    """
    try:
        stats = dead_tuples()
        live, dead = stats if stats else (0, 0)
        logger.info(
            "knowledge_chunks: n_live_tup~%d n_dead_tup~%d (this run wrote %d dead)",
            live,
            dead,
            written,
        )
        if max(dead, written) < VACUUM_DEAD_TUPLES:
            return False
        logger.warning(
            "knowledge_chunks has ~%d dead tuples (>= %d): running VACUUM (ANALYZE)",
            max(dead, written),
            VACUUM_DEAD_TUPLES,
        )
        started = time.perf_counter()
        vacuum_knowledge_chunks()
        after = dead_tuples()
        logger.info(
            "VACUUM (ANALYZE) knowledge_chunks done in %.1f s; n_dead_tup~%s",
            time.perf_counter() - started,
            after[1] if after else "?",
        )
        return True
    except Exception:  # noqa: BLE001 — a failed vacuum must not fail a finished sweep
        logger.exception(
            "VACUUM (ANALYZE) knowledge_chunks failed; run it by hand (docs/claude/knowledge.md)"
        )
        return False


def summarize(results: list[IndexResult], *, dry_run: bool) -> dict:
    """Totals across a sweep, plus the estimated cost of the embedding work."""
    tokens = sum(r.embed_tokens for r in results)
    model = embeddings.document_model()
    statuses: dict[str, int] = {}
    by_source: dict[str, dict[str, int]] = {}
    for r in results:
        statuses[r.status] = statuses.get(r.status, 0) + 1
        src = by_source.setdefault(r.source_type, {"chunks": 0, "embedded": 0, "embed_tokens": 0})
        src["chunks"] += r.chunks
        src["embedded"] += r.embedded
        src["embed_tokens"] += r.embed_tokens
    context_calls = sum(r.context_calls for r in results)
    return {
        "context_wanted": sum(r.context_wanted for r in results),
        "context_written": sum(r.context_written for r in results),
        "context_calls": context_calls,
        "context_input_tokens": sum(r.context_input_tokens for r in results),
        "context_output_tokens": sum(r.context_output_tokens for r in results),
        "context_cost_usd": sum(r.context_cost_usd for r in results),
        "context_missing": sum(r.context_missing for r in results),
        "context_capped": sum(r.context_capped for r in results),
        "est_minutes": round(context_calls * SECONDS_PER_CONTEXT_CALL / 60, 1),
        "articles": len({r.article_id for r in results if r.article_id is not None}),
        "runs": len(results),
        "statuses": statuses,
        "by_source": by_source,
        "chunks": sum(r.chunks for r in results),
        "chunks_en": sum(r.by_language.get("en", 0) for r in results),
        "chunks_es": sum(r.by_language.get("es", 0) for r in results),
        "embedded": sum(r.embedded for r in results),
        "kept": sum(r.kept for r in results),
        "updated": sum(r.updated for r in results),
        "deleted": sum(r.deleted for r in results),
        "embed_tokens": tokens,
        "model": model,
        "est_cost_usd": embeddings.estimate_cost(model, tokens),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index articles into knowledge_chunks")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--all", action="store_true", help="Sweep every article, its claims, and the KB"
    )
    target.add_argument(
        "--article", type=int, metavar="N", help="Index one article (body + claims)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report chunk counts, changed chunks and estimated tokens/cost; write nothing",
    )
    parser.add_argument(
        "--retry-context",
        action="store_true",
        help="Ask again for situating sentences of chunks out of attempts (#712)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=f"Articles indexed in parallel (1-{MAX_WORKERS}, default 1)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers must be between 1 and {MAX_WORKERS}")

    if not args.dry_run and not embeddings.embeddings_enabled():
        logger.error("VOYAGE_API_KEY is not set; only --dry-run can run without it.")
        return 1

    if not chunk_context.context_enabled():
        logger.warning(
            "ANTHROPIC_API_KEY is not set: chunks are indexed without situating sentences (#697)."
        )
    started = time.perf_counter()
    results = sweep(
        article_ids(args.article),
        dry_run=args.dry_run,
        verified=args.all,
        workers=args.workers,
        retry_context=args.retry_context,
    )
    elapsed = time.perf_counter() - started
    verb = "would embed" if args.dry_run else "embedded"
    for r in results:
        if r.status in ("unchanged", "missing"):
            continue
        target = "KB" if r.article_id is None else f"article {r.article_id}"
        logger.info(
            "%s %s: %s chunks=%d (en=%d es=%d) %s=%d kept=%d %s=%d deleted=%d tokens~%d",
            target,
            r.source_type,
            r.status,
            r.chunks,
            r.by_language.get("en", 0),
            r.by_language.get("es", 0),
            verb,
            r.embedded,
            r.kept,
            "would update" if args.dry_run else "updated",
            r.updated,
            r.deleted,
            r.embed_tokens,
        )
    totals = summarize(results, dry_run=args.dry_run)
    for source_type, src in sorted(totals["by_source"].items()):
        logger.info(
            "  %s: chunks=%d %s=%d tokens~%d",
            source_type,
            src["chunks"],
            verb,
            src["embedded"],
            src["embed_tokens"],
        )
    logger.info(
        "%s%d article(s) %s: chunks=%d (en=%d es=%d) %s=%d kept=%d %s=%d deleted=%d "
        "tokens~%d est_cost_usd=%.4f (%s)",
        "DRY RUN: " if args.dry_run else "",
        totals["articles"],
        totals["statuses"],
        totals["chunks"],
        totals["chunks_en"],
        totals["chunks_es"],
        verb,
        totals["embedded"],
        totals["kept"],
        "would update" if args.dry_run else "updated",
        totals["updated"],
        totals["deleted"],
        totals["embed_tokens"],
        totals["est_cost_usd"],
        totals["model"],
    )
    logger.info(
        "%ssituating sentences (#697, %s): %s=%d of %d in %d Haiku call(s), "
        "tokens in~%d out~%d, %scost_usd=%.4f",
        "DRY RUN: " if args.dry_run else "",
        chunk_context.MODEL,
        "would write" if args.dry_run else "written",
        totals["context_wanted"] if args.dry_run else totals["context_written"],
        totals["context_wanted"],
        totals["context_calls"],
        totals["context_input_tokens"],
        totals["context_output_tokens"],
        "est_" if args.dry_run else "",
        totals["context_cost_usd"],
    )
    logger.info(
        "%ssentence-less article chunks (#712)%s: %d, of which %d out of attempts (max %d)%s",
        "DRY RUN: " if args.dry_run else "",
        " as indexed now" if args.dry_run else "",
        totals["context_missing"],
        totals["context_capped"],
        chunk_context.MAX_ATTEMPTS,
        "; --retry-context asks again" if totals["context_capped"] else "",
    )
    if args.dry_run:
        logger.info(
            "DRY RUN: estimated duration ~%.0f min with 1 worker (~%.0f min with %d)",
            totals["est_minutes"],
            totals["est_minutes"] / args.workers,
            args.workers,
        )
    else:
        logger.info("Done in %.1f min.", elapsed / 60)
        report_dead_tuples(totals["deleted"] + totals["updated"])
    return 1 if totals["statuses"].get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
