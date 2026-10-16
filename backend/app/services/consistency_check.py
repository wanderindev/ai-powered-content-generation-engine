"""Cross-article consistency check, shadow mode (#631, epic #618).

After an ``ARTICLE`` fact-check job has committed ``SUCCEEDED`` (and the #625
claims reindex has run), :func:`run_consistency_check` compares each of the
article's claims with the nearest **facts** from other articles and the
verified-claims KB, and records the pairs a Sonnet judge labels
``CONTRADICTS`` in ``consistency_findings``. It is called from the fact-check
runner's job tail only while ``CROSS_ARTICLE_CONSISTENCY_ENABLED`` is on.

Shadow mode: it never changes a ``fact_check_claims`` verdict, never gates a
publish, is not in any driver call plan, and **never raises** (a failure is
logged, sent to Sentry and leaves the existing findings untouched).

Pipeline (the Connection Pool rule: no session is open across a Voyage or
Sonnet call):

1. **Read** the article's claims (id order, at most ``consistency_max_claims``)
   and close the session.
2. **Embed** every claim in one ``embeddings.embed_queries`` call.
3. **Retrieve** per claim with ``retrieval.search(mode="vector",
   visibility="internal", source_types=[ARTICLE_CLAIM, VERIFIED_CLAIM],
   exclude_article_ids=[article], query_vector=...)``: other APPROVED articles'
   fact chunks (scheduled included) plus every KB chunk (``article_id`` NULL,
   so it survives the exclusion). Kept at cosine similarity
   ``>= consistency_min_similarity``, at most ``consistency_top_k`` per claim,
   at most ``consistency_max_pairs`` per article (most similar first). KB
   candidates are reloaded by id, so a chunk that lags its row is judged on the
   row's current verdict. Close the session.
4. **Judge** in batches of ``consistency_batch_pairs`` (Sonnet, ``effort:
   "low"``, ``feature="consistency_check"``). Malformed, missing or
   out-of-vocabulary lines, and a ``CONTRADICTS`` with no explanation, degrade
   to ``UNRELATED``, never to a finding (the #433 parser lesson). A failed
   batch is skipped; the run continues.
5. **Write** in a fresh session.

**INACCURATE / DISPUTED KB rows (the #625 review note).** Retrieval does *not*
pass ``facts_only``: a claim that repeats a known falsehood is exactly what
this check should surface. Instead every reference reaches the judge as an
established fact plus, where one exists, the known falsehood it replaced:

* KB ``VERIFIED`` -> the claim text is the fact.
* KB ``INACCURATE`` -> the **correction** is the fact and the KB claim text is
  shown as ``KNOWN FALSEHOOD``; with no correction recorded the row is skipped
  (there is no fact to compare with).
* KB ``DISPUTED`` -> skipped: reputable sources disagree, so neither side is a
  fact to contradict.
* ``ARTICLE_CLAIM`` -> the chunk text is already the fact (VERIFIED claim text
  or CORRECTED correction); a CORRECTED chunk's original claim is the known
  falsehood.

So a claim agreeing with an INACCURATE KB row is compared with its correction
and can only come out ``CONTRADICTS``, never ``CONSISTENT``.

**Re-runs.** A run whose every batch was judged replaces the article's *open*
findings (``resolved_at IS NULL``) and keeps resolved ones; a run with a failed
batch only adds. Either way a new pair whose (claim text, other text) already
has a finding for the article is not inserted again, so a dismissal sticks and
open rows are bounded by ``consistency_max_pairs``.

Cost and latency: ``create_message`` logs tokens per call under
``feature="consistency_check"``; one summary INFO line per article
(``consistency check: ...``) adds pairs judged, per-label counts, tokens, an
estimated cost and the elapsed time.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import sentry_sdk
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.consistency_finding import ConsistencyFinding
from app.models.enums import (
    ConsistencySource,
    ConsistencyVerdict,
    FactCheckSubject,
    FactCheckVerdict,
)
from app.models.fact_check import FactCheckClaim
from app.models.verified_claim import VerifiedClaim
from app.services import embeddings
from app.services.knowledge import retrieval
from app.services.llm import SONNET_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

FEATURE = "consistency_check"
# Chunks the vector stage returns per claim before the floor / top_k cut: HNSW
# filters after ef_search, and DISPUTED / correction-less KB rows are dropped
# after retrieval, so ask for more than top_k.
CANDIDATE_POOL = 20
# Sonnet 5.5 list price (USD per 1M tokens), for the logged estimate only.
# TODO: the repo records no Sonnet 5.5 price; same placeholder as scripts.eval_rag.
SONNET_PRICE_PER_MILLION = (3.0, 15.0)

_ARTICLE_CLAIM = ConsistencySource.ARTICLE_CLAIM.value
_VERIFIED_CLAIM = ConsistencySource.VERIFIED_CLAIM.value
_CONTRADICTS = ConsistencyVerdict.CONTRADICTS.value
_CONSISTENT = ConsistencyVerdict.CONSISTENT.value
_UNRELATED = ConsistencyVerdict.UNRELATED.value
_LABELS = set(ConsistencyVerdict.values())

_PAIR_LINE = re.compile(r"^PAIR:\s*(\d+)\s*\|\s*([A-Z_]+)\s*\|\s*(.*?)\s*$")

_JUDGE_PROMPT = """\
You are checking a Panama history publication for contradictions between its \
articles.

Each numbered pair below has a CLAIM from the article being checked and a \
REFERENCE: a fact already established elsewhere on the site (another \
article's checked claim, or the site's verified-claims knowledge base). Some \
pairs also show a KNOWN FALSEHOOD: a statement that was checked and found \
wrong, which the REFERENCE corrects.

For each pair choose one label:
- CONTRADICTS: the claim and the reference are about the same thing and \
cannot both be true (a different date, number, person, place or outcome), or \
the claim repeats the known falsehood.
- CONSISTENT: they are about the same thing and agree. Paraphrases, extra or \
missing detail, and reasonable rounding are CONSISTENT.
- UNRELATED: they are about different things, or you cannot tell.

When in doubt between CONTRADICTS and anything else, choose the other label: \
a contradiction must be clear from the two texts alone.

Output exactly one line per pair, nothing before or after:
PAIR: <number> | <CONTRADICTS or CONSISTENT or UNRELATED> | <one-line explanation>

PAIRS:
{pairs}"""


@dataclass
class _Reference:
    source_type: str
    source_id: int
    article_id: int | None
    fact: str
    falsehood: str | None
    similarity: float


@dataclass
class _Pair:
    claim_id: int
    claim_text: str
    ref: _Reference


def _key(text: str) -> str:
    return " ".join((text or "").lower().split())


def _kb_reference(row: VerifiedClaim, similarity: float) -> _Reference | None:
    """The established fact a KB row states, or None when it states none."""
    claim = (row.claim_text or "").strip()
    if row.verdict == FactCheckVerdict.VERIFIED.value:
        fact, falsehood = claim, None
    elif row.verdict == FactCheckVerdict.INACCURATE.value:
        fact, falsehood = (row.correction or "").strip(), claim or None
    else:  # DISPUTED (or anything else): no fact to contradict
        return None
    if not fact:
        return None
    return _Reference(_VERIFIED_CLAIM, row.id, None, fact, falsehood, similarity)


def _gather_pairs(
    db, claims: list[tuple[int, str]], vectors, exclude_article_ids: list[int], settings
):
    """Candidate pairs per claim (floor + top_k), then the per-article cap.

    The production run excludes the article being checked; the #632 eval
    passes ``[]`` because its seeded claim is new and must meet its source.
    """
    raw: list[tuple[int, str, list]] = []
    kb_ids: set[int] = set()
    for (claim_id, text), vector in zip(claims, vectors, strict=True):
        result = retrieval.search(
            db,
            text,
            mode="vector",
            k=CANDIDATE_POOL,
            candidates=CANDIDATE_POOL,
            source_types=[_ARTICLE_CLAIM, _VERIFIED_CLAIM],
            visibility="internal",
            exclude_article_ids=exclude_article_ids,
            query_vector=vector,
        )
        hits = []
        for chunk in result.chunks:
            if chunk.vector_distance is None:
                continue
            similarity = 1.0 - chunk.vector_distance
            if similarity < settings.consistency_min_similarity:
                continue
            hits.append((chunk, similarity))
            if chunk.source_type == _VERIFIED_CLAIM:
                kb_ids.add(chunk.source_id)
        raw.append((claim_id, text, hits))

    kb_rows = (
        {
            row.id: row
            for row in db.execute(
                select(VerifiedClaim).where(VerifiedClaim.id.in_(kb_ids))
            ).scalars()
        }
        if kb_ids
        else {}
    )

    pairs: list[_Pair] = []
    for claim_id, text, hits in raw:
        seen: set[tuple[str, int]] = set()
        kept = 0
        for chunk, similarity in hits:
            if kept >= settings.consistency_top_k:
                break
            ident = (chunk.source_type, chunk.source_id)
            if ident in seen:
                continue
            seen.add(ident)
            if chunk.source_type == _VERIFIED_CLAIM:
                row = kb_rows.get(chunk.source_id)
                ref = _kb_reference(row, similarity) if row is not None else None
            else:
                meta = chunk.meta or {}
                fact = (chunk.content or "").strip()
                falsehood = None
                if meta.get("verdict") == FactCheckVerdict.CORRECTED.value:
                    falsehood = (meta.get("original_claim") or "").strip() or None
                ref = (
                    _Reference(
                        _ARTICLE_CLAIM,
                        chunk.source_id,
                        chunk.article_id,
                        fact,
                        falsehood,
                        similarity,
                    )
                    if fact
                    else None
                )
            if ref is None:
                continue
            pairs.append(_Pair(claim_id, text, ref))
            kept += 1

    # Per-article cap: the most similar pairs are the likeliest to be about the
    # same thing. Stable sort keeps claim order among ties.
    pairs.sort(key=lambda p: -p.ref.similarity)
    return pairs[: max(0, settings.consistency_max_pairs)]


def _format_pairs(pairs: list[_Pair]) -> str:
    lines = []
    for i, pair in enumerate(pairs, start=1):
        lines.append(f"{i}. CLAIM: {pair.claim_text}")
        lines.append(f"   REFERENCE: {pair.ref.fact}")
        if pair.ref.falsehood:
            lines.append(f"   KNOWN FALSEHOOD: {pair.ref.falsehood}")
    return "\n".join(lines)


def parse_judge_lines(raw: str, count: int) -> dict[int, tuple[str, str]]:
    """``{0-based index: (label, explanation)}``; anything malformed is absent.

    An out-of-vocabulary label, or a CONTRADICTS without an explanation, reads
    as UNRELATED. A repeated number keeps its first line.
    """
    out: dict[int, tuple[str, str]] = {}
    for line in (raw or "").splitlines():
        match = _PAIR_LINE.match(line.strip())
        if not match:
            continue
        number, label, explanation = match.groups()
        index = int(number) - 1
        if not 0 <= index < count or index in out:
            continue
        explanation = explanation.strip()
        if label not in _LABELS:
            label = _UNRELATED
        if label == _CONTRADICTS and explanation in ("", "-"):
            label = _UNRELATED
        out[index] = (label, explanation)
    return out


def _judge(
    pairs: list[_Pair], *, feature: str = FEATURE
) -> tuple[dict[int, tuple[str, str]], int, int]:
    """Judge one batch. Returns (verdicts, input tokens, output tokens). Raises."""
    message = create_message(
        feature=feature,
        model=SONNET_MODEL,
        # Thinking tokens count toward max_tokens; a 20-pair batch needs ~1k
        # output tokens, the rest is headroom for whatever "low" still thinks
        # (same budget shape as the grounding judge, so it streams).
        max_tokens=16000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": _JUDGE_PROMPT.format(pairs=_format_pairs(pairs))}],
    )
    usage = getattr(message, "usage", None)
    tokens_in = getattr(usage, "input_tokens", 0)
    tokens_out = getattr(usage, "output_tokens", 0)
    tokens_in = tokens_in if isinstance(tokens_in, int) else 0
    tokens_out = tokens_out if isinstance(tokens_out, int) else 0
    return parse_judge_lines(first_text(message), len(pairs)), tokens_in, tokens_out


def _load_claims(article_id: int, limit: int) -> list[tuple[int, str]]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(FactCheckClaim.id, FactCheckClaim.claim_text)
            .where(
                FactCheckClaim.subject_type == FactCheckSubject.ARTICLE.value,
                FactCheckClaim.subject_id == article_id,
            )
            .order_by(FactCheckClaim.id)
            .limit(max(0, limit))
        ).all()
    finally:
        db.close()
    return [(cid, text) for cid, text in rows if (text or "").strip()]


def _write_findings(article_id: int, found: list[tuple[_Pair, str]], *, replace_open: bool) -> int:
    """Persist CONTRADICTS pairs; returns the number inserted."""
    db = SessionLocal()
    try:
        if replace_open:
            db.execute(
                delete(ConsistencyFinding).where(
                    ConsistencyFinding.article_id == article_id,
                    ConsistencyFinding.resolved_at.is_(None),
                )
            )
        existing = {
            (_key(c), _key(o))
            for c, o in db.execute(
                select(ConsistencyFinding.claim_text, ConsistencyFinding.other_text).where(
                    ConsistencyFinding.article_id == article_id
                )
            ).all()
        }
        inserted = 0
        for pair, explanation in found:
            key = (_key(pair.claim_text), _key(pair.ref.fact))
            if key in existing:
                continue
            existing.add(key)
            db.add(
                ConsistencyFinding(
                    article_id=article_id,
                    claim_id=pair.claim_id,
                    claim_text=pair.claim_text,
                    other_source_type=pair.ref.source_type,
                    other_source_id=pair.ref.source_id,
                    other_article_id=pair.ref.article_id,
                    other_text=pair.ref.fact,
                    verdict=_CONTRADICTS,
                    explanation=explanation,
                    similarity=round(pair.ref.similarity, 4),
                )
            )
            inserted += 1
        db.commit()
        return inserted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run(article_id: int) -> dict | None:
    started = time.perf_counter()
    settings = get_settings()
    if not embeddings.embeddings_enabled():
        logger.info("consistency check: article=%d skipped (embeddings disabled)", article_id)
        return None

    claims = _load_claims(article_id, settings.consistency_max_claims)
    pairs: list[_Pair] = []
    if claims:
        try:
            vectors = embeddings.embed_queries([t for _, t in claims], feature=FEATURE)
        except (embeddings.EmbeddingsDisabledError, embeddings.EmbeddingsError):
            logger.warning(
                "consistency check: article=%d skipped (query embedding failed)", article_id
            )
            return None
        db = SessionLocal()
        try:
            pairs = _gather_pairs(db, claims, vectors, [article_id], settings)
        finally:
            db.close()

    batch_size = max(1, settings.consistency_batch_pairs)
    counts = {label: 0 for label in ConsistencyVerdict.values()}
    found: list[tuple[_Pair, str]] = []
    tokens_in = tokens_out = batches = failed = 0
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        batches += 1
        try:
            verdicts, used_in, used_out = _judge(batch)
        except Exception as e:  # noqa: BLE001 — a failed batch is isolated
            failed += 1
            logger.warning(
                "consistency check: article=%d judge batch %d failed: %s",
                article_id,
                batches,
                type(e).__name__,
            )
            sentry_sdk.capture_exception(e)
            continue
        tokens_in += used_in
        tokens_out += used_out
        for i, pair in enumerate(batch):
            label, explanation = verdicts.get(i, (_UNRELATED, ""))
            counts[label] += 1
            if label == _CONTRADICTS:
                found.append((pair, explanation))

    recorded = _write_findings(article_id, found, replace_open=failed == 0)
    price_in, price_out = SONNET_PRICE_PER_MILLION
    cost = (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    summary = {
        "article_id": article_id,
        "claims": len(claims),
        "pairs": len(pairs),
        "batches": batches,
        "failed_batches": failed,
        "counts": counts,
        "recorded": recorded,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": round(cost, 4),
        "elapsed_ms": elapsed_ms,
    }
    logger.info(
        "consistency check: article=%d claims=%d pairs=%d batches=%d failed_batches=%d "
        "contradicts=%d consistent=%d unrelated=%d recorded=%d input_tokens=%d "
        "output_tokens=%d est_cost_usd=%.4f elapsed_ms=%d",
        article_id,
        len(claims),
        len(pairs),
        batches,
        failed,
        counts[_CONTRADICTS],
        counts[_CONSISTENT],
        counts[_UNRELATED],
        recorded,
        tokens_in,
        tokens_out,
        cost,
        elapsed_ms,
    )
    return summary


def run_consistency_check(article_id: int) -> dict | None:
    """Check one article against the others; the summary, or None. Never raises."""
    try:
        return _run(article_id)
    except Exception as e:  # noqa: BLE001 — shadow mode must never break the caller
        logger.exception("consistency check: article=%d failed", article_id)
        sentry_sdk.capture_exception(e)
        return None
