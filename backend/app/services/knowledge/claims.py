"""Claim indexer: fact-check claims and the verified-claims KB as chunks (#625, epic #617).

Two kinds of ``knowledge_chunks`` row, one claim per row (``chunk_index`` 0,
no ``heading_path``), both ``language = "en"``: claims are extracted from the
English article body / English research, and the KB is English-only.

* ``ARTICLE_CLAIM`` (:func:`index_article_claims`): an article's
  ``fact_check_claims`` rows (``subject_type = ARTICLE``) that are **facts** —
  ``VERIFIED`` (the claim text) and ``CORRECTED`` (the *correction* text: the
  claim itself was wrong, the content now says the correction). Every other
  verdict (``INACCURATE`` / ``DISPUTED`` / ``UNVERIFIABLE`` /
  ``NEEDS_DEEP_RESEARCH`` / ``PENDING``) is not indexed. ``source_id`` is the
  claim id and ``article_id`` the article, so an article delete cascades.
  A fact-check re-run deletes and re-inserts the claims with **new ids**, so
  the run owns every ``ARTICLE_CLAIM`` row of the article (selected by
  ``article_id``, never by old claim ids): rows whose text is still wanted keep
  their vector and move to the new claim id, the rest are deleted. No orphans.
* ``VERIFIED_CLAIM`` (:func:`index_verified_claims`): one row per
  ``verified_claims`` KB entry (``source_id`` = its id, no ``article_id``).
  The KB holds VERIFIED, INACCURATE and DISPUTED verdicts, and all three are
  indexed because KB matching (#628) must find all three; the text is the claim
  plus its note under a verdict label (``Correction:`` for INACCURATE,
  ``Positions:`` for DISPUTED), and ``meta.verdict`` says which. **Only
  ``meta.verdict == "VERIFIED"`` is a fact**: a consumer looking for supporting
  evidence must filter on it. Any other verdict in the table is skipped. The
  whole KB is re-planned on every run (a read of every row plus hashes; only
  changed text is embedded), because the upsert paths do not report ids.

Same mechanics as the article indexer (:mod:`app.services.knowledge.indexer`):
hash-matched plan, read and close -> embed with no session open -> write in a
fresh session under a lock (the article row ``FOR UPDATE``; a transaction
advisory lock for the KB), stopping as ``superseded`` if the claims changed
mid-run (whatever changed them scheduled its own run). Both **never raise**
and skip quietly with an empty ``VOYAGE_API_KEY``.

Hooks (all after the claim write has committed): the fact-check job runner
(an ``ARTICLE`` job reindexes that article's claims; an external-tier job,
which can upsert the KB, reindexes the KB), the deep-research verdicts endpoint
and the corrections apply endpoint (``schedule_claims_reindex``), and the MCP
``submit_fact_check_verdicts`` tool (``reindex_claims``, after its session is
closed; the ``/fact-check-process`` skill's default path). The skill's SQL
fallback writes claims and the KB with direct SQL, so
``scripts.reindex_knowledge --all`` is its repair path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sentry_sdk
from fastapi import BackgroundTasks
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.enums import FactCheckSubject, FactCheckVerdict, KnowledgeSourceType, Language
from app.models.fact_check import FactCheckClaim
from app.models.verified_claim import VerifiedClaim
from app.services import embeddings
from app.services.knowledge import indexer
from app.services.knowledge.chunker import Chunk, content_hash, estimate_tokens
from app.services.knowledge.indexer import IndexResult

logger = logging.getLogger(__name__)

ARTICLE_CLAIM = KnowledgeSourceType.ARTICLE_CLAIM.value
VERIFIED_CLAIM = KnowledgeSourceType.VERIFIED_CLAIM.value
CLAIM_LANGUAGE = Language.EN.value

# Article claim verdicts that state a fact (and which text states it).
ARTICLE_FACT_VERDICTS = (FactCheckVerdict.VERIFIED.value, FactCheckVerdict.CORRECTED.value)

# KB verdict -> label of its note in the chunk text. A verdict not listed here
# is not indexed (the KB only ever holds these three; see verified_claim.py).
KB_NOTE_LABELS = {
    FactCheckVerdict.VERIFIED.value: "Note",
    FactCheckVerdict.INACCURATE.value: "Correction",
    FactCheckVerdict.DISPUTED.value: "Positions",
}

# pg_advisory_xact_lock key serialising KB reindex writes ("KBCHUNKS").
_KB_LOCK_KEY = 0x4B4243484E4B53

_C = indexer._C


@dataclass(frozen=True)
class ClaimChunk(Chunk):
    """A claim chunk: a :class:`Chunk` that also carries its source row id."""

    source_id: int = 0


def _claim_chunk(source_id: int, text: str, meta: dict) -> ClaimChunk:
    return ClaimChunk(
        chunk_index=0,
        heading_path=None,
        content=text,
        embedding_text=text,
        content_hash=content_hash(text),
        token_count=estimate_tokens(text),
        meta={"language": CLAIM_LANGUAGE, **meta},
        source_id=source_id,
    )


def article_claim_text(claim: FactCheckClaim) -> str | None:
    """The fact an article claim states, or None when it is not indexed as a fact."""
    if claim.verdict == FactCheckVerdict.VERIFIED.value:
        text = claim.claim_text
    elif claim.verdict == FactCheckVerdict.CORRECTED.value:
        text = claim.correction
    else:
        return None
    text = (text or "").strip()
    return text or None


def article_claim_chunks(claims: list[FactCheckClaim]) -> list[ClaimChunk]:
    """The ARTICLE_CLAIM chunks for one article's claim rows (in claim id order)."""
    chunks = []
    for claim in sorted(claims, key=lambda c: c.id):
        text = article_claim_text(claim)
        if text is None:
            continue
        meta = {
            "verdict": claim.verdict,
            "claim_type": claim.claim_type,
            "checked_via": claim.checked_via,
            "citation": claim.citation,
        }
        if claim.verdict == FactCheckVerdict.CORRECTED.value:
            meta["original_claim"] = claim.claim_text
        chunks.append(_claim_chunk(claim.id, text, meta))
    return chunks


def verified_claim_text(row: VerifiedClaim) -> str | None:
    """A KB row's chunk text (claim + labelled note), or None when not indexed."""
    label = KB_NOTE_LABELS.get(row.verdict)
    claim = (row.claim_text or "").strip()
    if label is None or not claim:
        return None
    note = (row.correction or "").strip()
    return f"{claim}\n{label}: {note}" if note else claim


def verified_claim_chunks(rows: list[VerifiedClaim]) -> list[ClaimChunk]:
    """The VERIFIED_CLAIM chunks for the KB rows (in id order)."""
    chunks = []
    for row in sorted(rows, key=lambda r: r.id):
        text = verified_claim_text(row)
        if text is None:
            continue
        meta = {
            "verdict": row.verdict,
            "claim_type": row.claim_type,
            "citation": row.citation,
            "source_method": row.source_method,
        }
        chunks.append(_claim_chunk(row.id, text, meta))
    return chunks


def _row_values(source_type: str, article_id: int | None):
    def values(chunk: ClaimChunk) -> dict:
        return {
            "heading_path": None,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            "token_count": chunk.token_count,
            "meta": chunk.meta,
            "source_type": source_type,
            "source_id": chunk.source_id,
            "article_id": article_id,
            "language": CLAIM_LANGUAGE,
            "chunk_index": chunk.chunk_index,
        }

    return values


def _load_article_claims(db, article_id: int) -> list[FactCheckClaim]:
    return list(
        db.execute(
            select(FactCheckClaim).where(
                FactCheckClaim.subject_type == FactCheckSubject.ARTICLE.value,
                FactCheckClaim.subject_id == article_id,
                FactCheckClaim.verdict.in_(ARTICLE_FACT_VERDICTS),
            )
        ).scalars()
    )


def _load_kb(db) -> list[VerifiedClaim]:
    return list(db.execute(select(VerifiedClaim)).scalars())


def _hashes(chunks: list[ClaimChunk]) -> list[tuple[int, str]]:
    return [(c.source_id, c.content_hash) for c in chunks]


def _guarded(label: str, article_id: int | None, source_type: str, run) -> IndexResult:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 — a background hook must not raise
        logger.exception("knowledge claim index failed for %s", label)
        sentry_sdk.capture_exception(exc)
        return IndexResult(article_id=article_id, status="failed", source_type=source_type)


def index_article_claims(article_id: int, *, dry_run: bool = False) -> IndexResult:
    """Bring one article's ARTICLE_CLAIM chunks up to date. Never raises."""
    return _guarded(
        f"article {article_id} claims",
        article_id,
        ARTICLE_CLAIM,
        lambda: _index_article_claims(article_id, dry_run=dry_run),
    )


def _index_article_claims(article_id: int, *, dry_run: bool) -> IndexResult:
    def result(status: str, plan=None, updated: int = 0) -> IndexResult:
        if plan is None:
            return IndexResult(article_id=article_id, status=status, source_type=ARTICLE_CLAIM)
        return indexer._result(article_id, status, plan, ARTICLE_CLAIM, updated=updated)

    if not dry_run and not embeddings.embeddings_enabled():
        logger.info("knowledge claim index skipped for article %d: embeddings disabled", article_id)
        return result("disabled")

    owned = (_C.source_type == ARTICLE_CLAIM, _C.article_id == article_id)
    row_values = _row_values(ARTICLE_CLAIM, article_id)

    with SessionLocal() as db:
        if db.get(Article, article_id) is None:
            return result("missing")  # its chunks cascaded with it
        wanted = article_claim_chunks(_load_article_claims(db, article_id))
        plan = indexer._plan(wanted, indexer._load_existing(db, *owned))
        changed = indexer._changed_kept(db, plan, row_values)

    if not plan.new and not plan.stale_ids and not changed:
        return result("unchanged", plan)
    if dry_run:
        return result("dry_run", plan, len(changed))

    vectors = indexer._embed_new(plan)

    with SessionLocal() as db:
        locked = db.execute(
            select(Article.id).where(Article.id == article_id).with_for_update(of=Article)
        ).scalar_one_or_none()
        if locked is None:
            return result("missing")
        fresh = article_claim_chunks(_load_article_claims(db, article_id))
        if _hashes(fresh) != _hashes(wanted):
            logger.info(
                "knowledge claim index for article %d superseded: claims changed mid-run",
                article_id,
            )
            return result("superseded", plan)
        plan = indexer._plan(fresh, indexer._load_existing(db, *owned))
        indexer._drop_unembedded(plan, vectors, f"article {article_id} claims")
        updated = indexer._write(db, plan, vectors, row_values)
        db.commit()

    out = result("indexed", plan, updated)
    logger.info(
        "knowledge claim index: article=%d claims=%d embedded=%d kept=%d updated=%d deleted=%d",
        article_id,
        out.chunks,
        out.embedded,
        out.kept,
        out.updated,
        out.deleted,
    )
    return out


def index_verified_claims(*, dry_run: bool = False) -> IndexResult:
    """Bring the VERIFIED_CLAIM chunks in step with the whole KB. Never raises."""
    return _guarded(
        "the verified-claims KB",
        None,
        VERIFIED_CLAIM,
        lambda: _index_verified_claims(dry_run=dry_run),
    )


def _index_verified_claims(*, dry_run: bool) -> IndexResult:
    def result(status: str, plan=None, updated: int = 0) -> IndexResult:
        if plan is None:
            return IndexResult(article_id=None, status=status, source_type=VERIFIED_CLAIM)
        return indexer._result(None, status, plan, VERIFIED_CLAIM, updated=updated)

    if not dry_run and not embeddings.embeddings_enabled():
        logger.info("knowledge claim index skipped for the KB: embeddings disabled")
        return result("disabled")

    owned = (_C.source_type == VERIFIED_CLAIM,)
    row_values = _row_values(VERIFIED_CLAIM, None)

    with SessionLocal() as db:
        wanted = verified_claim_chunks(_load_kb(db))
        plan = indexer._plan(wanted, indexer._load_existing(db, *owned))
        changed = indexer._changed_kept(db, plan, row_values)

    if not plan.new and not plan.stale_ids and not changed:
        return result("unchanged", plan)
    if dry_run:
        return result("dry_run", plan, len(changed))

    vectors = indexer._embed_new(plan)

    with SessionLocal() as db:
        db.execute(select(func.pg_advisory_xact_lock(_KB_LOCK_KEY)))
        fresh = verified_claim_chunks(_load_kb(db))
        if _hashes(fresh) != _hashes(wanted):
            logger.info("knowledge claim index for the KB superseded: KB changed mid-run")
            return result("superseded", plan)
        plan = indexer._plan(fresh, indexer._load_existing(db, *owned))
        indexer._drop_unembedded(plan, vectors, "the verified-claims KB")
        updated = indexer._write(db, plan, vectors, row_values)
        db.commit()

    out = result("indexed", plan, updated)
    logger.info(
        "knowledge claim index: KB claims=%d embedded=%d kept=%d updated=%d deleted=%d",
        out.chunks,
        out.embedded,
        out.kept,
        out.updated,
        out.deleted,
    )
    return out


def reindex_claims(article_ids=(), *, verified: bool = False) -> None:
    """Reindex the given articles' claims, then (optionally) the KB. Never raises."""
    for article_id in dict.fromkeys(article_ids):
        index_article_claims(article_id)
    if verified:
        index_verified_claims()


def schedule_claims_reindex(
    background_tasks: BackgroundTasks, article_ids=(), *, verified: bool = False
) -> None:
    """Queue :func:`reindex_claims` to run after the response is sent."""
    article_ids = list(article_ids)
    if article_ids or verified:
        background_tasks.add_task(reindex_claims, article_ids, verified=verified)
