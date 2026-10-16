"""Fact-check services: claim extraction + article grounding (epic #373).

Two building blocks live here:

* :func:`extract_claims` — Haiku pulls atomic factual claims (events, people,
  dates, places, quantities) out of any pipeline text. Long inputs are chunked
  first: past ~2,500 words models drift off format/constraint instructions
  (the same learning behind ``CONDENSE_THRESHOLD`` in article generation), and
  a research doc is 4,000–8,000 words. Output is a plain-text ``CLAIM:`` line
  format — JSON containing prose is a known-fragile shape in this codebase.

* :func:`ground_article` (#375) — judges an article's claims against the
  approved research document it was written from. The research **is** the
  ground truth for an article, so this tier needs no web access; it exists to
  catch the writer (or the condensation step) drifting beyond the research.
  Suggestion/research-level claims need *external* verification instead
  (#376/#377) — grounding is only meaningful for derived content.

* :func:`extract_for_deep_research` (#389) — the external tier for
  suggestions, blog research and edu research: extraction + KB matching
  alone (Haiku, pennies), with every unresolved claim sent straight to
  ``NEEDS_DEEP_RESEARCH`` for the free deep-research loop (#377). A
  Sonnet + web-search tier (#376) once sat beside it as an API-only escape
  hatch; it ran once and was removed in #751. Its ``WEB_SEARCH`` claim and
  KB rows are historical and still served via KB matching.

All tiers are deliberately verdict-conservative: anything malformed or missing
from an LLM response degrades to ``UNVERIFIABLE`` / ``NEEDS_DEEP_RESEARCH``,
never to ``VERIFIED``.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.enums import MaterialType
from app.models.fact_check import (
    FactCheckClaim,
    FactCheckMethod,
    FactCheckSubject,
    FactCheckVerdict,
    FactClaimType,
)
from app.models.research import Research
from app.services.consistency_check import run_consistency_check
from app.services.knowledge.claims import reindex_claims
from app.services.llm import (
    HAIKU_MODEL,
    SONNET_MODEL,
    create_message,
    first_text,
)
from app.utils.guide_blocks import blocks_prose
from app.utils.slide_text import slides_prose

logger = logging.getLogger(__name__)

# Words per extraction chunk — keeps each Haiku call well under the drift zone.
CHUNK_WORDS = 1500
# Claims judged per grounding call. The full research doc rides along in every
# call, so the batch only bounds the numbered list the judge must answer
# line-by-line — small enough that no line gets skipped.
GROUNDING_BATCH_SIZE = 25


# --- Claim extraction (issue #374) -------------------------------------------


@dataclass(frozen=True)
class ExtractedClaim:
    text: str
    claim_type: str  # a FactClaimType value


_CLAIM_LINE = re.compile(r"^CLAIM:\s*([A-Z_]+)\s*\|\s*(.+?)\s*$")

_EXTRACTION_PROMPT = """\
You are extracting atomic factual claims from content for a Panama history \
publication, so that each claim can be independently fact-checked.

Extract every discrete, checkable factual claim: historical events, people and \
their roles or associations, dates, places, and quantities. Skip opinions, \
analysis, framing, and anything that is not a verifiable statement of fact.

Output format — one claim per line, nothing else before or after:
CLAIM: <TYPE> | <one self-contained sentence stating the claim>

<TYPE> is one of: EVENT, PERSON, DATE, PLACE, NUMBER.
Every claim sentence must stand alone: name people, places, and events \
explicitly — never use pronouns or references like "the event" or "he".
If the text contains no factual claims, output exactly: NO CLAIMS

TEXT:
{text}"""


def _normalize(text: str) -> str:
    """Dedup key: lowercase, alphanumeric-and-spaces only, collapsed spaces."""
    return " ".join(re.sub(r"[^a-z0-9\s]", "", text.lower()).split())


def _chunk_words(text: str, chunk_words: int = CHUNK_WORDS) -> list[str]:
    words = text.split()
    if len(words) <= chunk_words:
        return [text]
    return [" ".join(words[i : i + chunk_words]) for i in range(0, len(words), chunk_words)]


def parse_claim_lines(raw: str) -> list[ExtractedClaim]:
    """Parse ``CLAIM: TYPE | text`` lines; skip malformed lines, coerce
    off-vocabulary types to OTHER."""
    claims = []
    for line in raw.splitlines():
        match = _CLAIM_LINE.match(line.strip())
        if not match:
            continue
        claim_type, text = match.groups()
        if claim_type not in FactClaimType.values():
            claim_type = FactClaimType.OTHER.value
        claims.append(ExtractedClaim(text=text, claim_type=claim_type))
    return claims


def extract_claims(text: str, *, feature: str = "fact_check_extract") -> list[ExtractedClaim]:
    """Extract atomic factual claims from *text* via Haiku, chunking long input.

    Claims are de-duplicated across chunks (normalized-text key), preserving
    first-seen order.
    """
    claims: list[ExtractedClaim] = []
    seen: set[str] = set()
    for chunk in _chunk_words(text):
        message = create_message(
            feature=feature,
            model=HAIKU_MODEL,
            # Haiku 5.5 thinks at medium effort and thinking counts toward the
            # cap (#610); 8000 also moves the call onto the streamed timeout.
            max_tokens=8000,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(text=chunk)}],
        )
        for claim in parse_claim_lines(first_text(message)):
            key = _normalize(claim.text)
            if key and key not in seen:
                seen.add(key)
                claims.append(claim)
    logger.info("Extracted %d unique claims (feature=%s)", len(claims), feature)
    return claims


# --- Article grounding check (issue #375) -------------------------------------

_VERDICT_LINE = re.compile(r"^VERDICT:\s*(\d+)\s*\|\s*([A-Z]+)\s*\|\s*(.*?)\s*$")

# Grounding label -> stored verdict. An unknown label degrades to UNVERIFIABLE;
# a claim the judge returned no line for is treated as ABSENT.
_GROUNDING_VERDICTS = {
    "SUPPORTED": FactCheckVerdict.VERIFIED.value,
    "CONTRADICTED": FactCheckVerdict.INACCURATE.value,
    "ABSENT": FactCheckVerdict.UNVERIFIABLE.value,
}

# Per-subject overrides of the mapping above (#555). An article claim its
# research does not cover is queued for the deep-research loop (the
# pic-extension drain) instead of being parked as UNVERIFIABLE, and the agent
# driver's WAIT_ARTICLE_CLAIMS_RESOLVED gate holds the article's corrections
# step until the operator has resolved it. EDU_MATERIAL shares ground_subject
# but has no override: ABSENT stays UNVERIFIABLE there.
_GROUNDING_VERDICT_OVERRIDES: dict[str, dict[str, str]] = {
    FactCheckSubject.ARTICLE.value: {"ABSENT": FactCheckVerdict.NEEDS_DEEP_RESEARCH.value},
}


def grounding_verdict(subject_type: str, label: str) -> str:
    """The stored verdict for grounding *label* on a *subject_type* subject."""
    override = _GROUNDING_VERDICT_OVERRIDES.get(subject_type, {})
    if label in override:
        return override[label]
    return _GROUNDING_VERDICTS.get(label, FactCheckVerdict.UNVERIFIABLE.value)


# Split into a stable prefix (instructions + research document) and a
# per-batch suffix (the claim list) so the prefix can be sent as its own
# cached content block — see _judge_batch (issue #413). Concatenating
# _GROUNDING_PREFIX.format(...) + _GROUNDING_SUFFIX.format(...) is
# byte-identical to the single-template prompt this replaces.
_GROUNDING_PREFIX = """\
You are fact-checking an article against the research document it was written \
from. The research document is the ONLY source of truth here — do NOT use any \
outside knowledge, even about well-known facts.

For each numbered claim below, judge it strictly against the research document:
- SUPPORTED — the research states the claim or directly implies it
- CONTRADICTED — the research states something incompatible with the claim
- ABSENT — the research neither supports nor contradicts the claim

Output format — exactly one line per claim, in claim order, nothing else:
VERDICT: <claim number> | <SUPPORTED or CONTRADICTED or ABSENT> | <note>

For CONTRADICTED the note must state what the research actually says. For \
SUPPORTED and ABSENT the note may be a single dash.

RESEARCH DOCUMENT:
{research}"""

_GROUNDING_SUFFIX = "\n\nCLAIMS:\n{claims}"


def _judge_batch(claims: list[ExtractedClaim], research_content: str) -> dict[int, tuple[str, str]]:
    """Judge one batch against the research. Returns {index: (label, note)}."""
    numbered = "\n".join(f"{i + 1}. {c.text}" for i, c in enumerate(claims))
    message = create_message(
        feature="fact_check_grounding",
        model=SONNET_MODEL,
        # Sonnet 5.5 always runs adaptive thinking and its thinking tokens
        # count toward max_tokens; at the default effort ("high") a 25-claim
        # batch against a full research doc can spend the whole budget on
        # thinking and truncate before the verdict lines (TruncationError).
        # Line-by-line grounding against a document in context doesn't need
        # deep reasoning — trim the effort, and keep generous headroom for
        # whatever thinking remains.
        max_tokens=16000,
        output_config={"effort": "low"},
        # The research document is identical across every batch for this
        # article, so it goes in its own cache_control'd block — the claims
        # block (unique per batch) is never cached (issue #413).
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _GROUNDING_PREFIX.format(research=research_content),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": _GROUNDING_SUFFIX.format(claims=numbered),
                    },
                ],
            }
        ],
    )
    verdicts: dict[int, tuple[str, str]] = {}
    for line in first_text(message).splitlines():
        match = _VERDICT_LINE.match(line.strip())
        if not match:
            continue
        number, label, note = match.groups()
        index = int(number) - 1
        if 0 <= index < len(claims):
            verdicts[index] = (label, note)
    return verdicts


def ground_subject(
    subject_type: str,
    subject_id: int,
    text: str,
    research_content: str,
    db: Session,
    *,
    feature: str,
    progress_callback=None,
) -> dict:
    """Run the grounding check for any subject derived from a research doc:
    extract *text*'s claims, judge each against *research_content*, and replace
    the subject's ``fact_check_claims`` rows.

    A claim the judge would queue as ``NEEDS_DEEP_RESEARCH`` (an ARTICLE's
    ABSENT) first keeps the subject's earlier deep-research verdict for the
    same ``claim_key``, then inherits a verified-claims KB match as
    ``KB_MATCH`` (#575); only what neither resolves is queued. A KB lookup
    failure is Sentry-captured and degrades to queued — the grounding still
    succeeds (#586). A supplied *progress_callback* is also invoked once just
    before the KB's Haiku pass, so a caller whose callback commits releases
    its pool slot for that call.

    The tier for content that should assert nothing its source research does not
    support — articles against blog research, and edu study guides against their
    ``edu_research`` (#461). Flushes but does not commit — the caller owns the
    transaction. Returns a summary dict: total / per-verdict counts / the rows.
    """
    claims = extract_claims(text or "", feature=feature)

    verdicts: dict[int, tuple[str, str]] = {}
    for start in range(0, len(claims), GROUNDING_BATCH_SIZE):
        batch = claims[start : start + GROUNDING_BATCH_SIZE]
        for index, verdict in _judge_batch(batch, research_content).items():
            verdicts[start + index] = verdict
        if progress_callback:
            done = min(start + GROUNDING_BATCH_SIZE, len(claims))
            progress_callback(done, len(claims), f"grounding {done}/{len(claims)} claims")

    now = datetime.now(UTC)
    # Per claim: (verdict, correction, citation, checked_via, checked_at).
    outcomes: list[tuple] = []
    for i in range(len(claims)):
        label, note = verdicts.get(i, ("ABSENT", "no verdict returned by the judge"))
        note = note.strip()
        outcomes.append(
            (
                grounding_verdict(subject_type, label),
                note if note and note != "-" else None,
                None,
                FactCheckMethod.GROUNDING.value,
                now,
            )
        )

    # A claim this grounding would queue for deep research (an ARTICLE's ABSENT,
    # #555) first reuses what is already known (#575): the verdict deep research
    # resolved for the same claim_key on an earlier run of this subject, then
    # the verified-claims KB. EDU_MATERIAL never maps to NEEDS_DEEP_RESEARCH,
    # so it never reaches this lookup.
    queued = [
        i
        for i, outcome in enumerate(outcomes)
        if outcome[0] == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
    ]
    kept = kb_matched = 0
    if queued:
        prior = _resolved_deep_research_rows(subject_type, subject_id, db)
        unresolved: list[int] = []
        for i in queued:
            previous = prior.get(_normalize(claims[i].text))
            if previous is None:
                unresolved.append(i)
                continue
            # Still the deep-research verdict, stamped when it was reached.
            outcomes[i] = (
                previous.verdict,
                previous.correction,
                previous.citation,
                FactCheckMethod.DEEP_RESEARCH.value,
                previous.checked_at,
            )
            kept += 1
        if unresolved:
            # The caller's progress seam commits in the job runner, which
            # frees the pool slot the reads above pinned before the Haiku
            # pass (#586). Nothing has been written yet — the delete is below.
            before_llm = None
            if progress_callback:

                def before_llm() -> None:
                    progress_callback(
                        len(claims),
                        len(claims),
                        f"matching {len(unresolved)} queued claims against the verified-claims KB",
                    )

            try:
                found = match_claims_against_kb(
                    [claims[i] for i in unresolved], db, before_llm=before_llm
                )
            except Exception as exc:
                # Degrade to queued: the paid-for extract + judge must not be
                # lost to a KB failure (Haiku error, budget, refusal,
                # truncation, network) — the claims stay NEEDS_DEEP_RESEARCH.
                logger.exception(
                    "Grounding %s %d: KB lookup failed; leaving %d claims queued for deep research",
                    subject_type,
                    subject_id,
                    len(unresolved),
                )
                sentry_sdk.capture_exception(exc)
                found = {}
            for j, kb_row in found.items():
                outcomes[unresolved[j]] = (
                    kb_row.verdict,
                    kb_row.correction,
                    kb_row.citation,
                    FactCheckMethod.KB_MATCH.value,
                    now,
                )
            kb_matched = len(found)

    # Re-running a check replaces the subject's rows wholesale.
    db.query(FactCheckClaim).filter(
        FactCheckClaim.subject_type == subject_type,
        FactCheckClaim.subject_id == subject_id,
    ).delete()

    rows: list[FactCheckClaim] = []
    for claim, (verdict, correction, citation, checked_via, checked_at) in zip(
        claims, outcomes, strict=True
    ):
        row = FactCheckClaim(
            subject_type=subject_type,
            subject_id=subject_id,
            claim_text=claim.text,
            claim_type=claim.claim_type,
            verdict=verdict,
            correction=correction,
            citation=citation,
            checked_via=checked_via,
            checked_at=checked_at,
        )
        db.add(row)
        rows.append(row)
    db.flush()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    summary = {
        "total_claims": len(rows),
        "verified": counts.get(FactCheckVerdict.VERIFIED.value, 0),
        "inaccurate": counts.get(FactCheckVerdict.INACCURATE.value, 0),
        "unverifiable": counts.get(FactCheckVerdict.UNVERIFIABLE.value, 0),
        "needs_deep_research": counts.get(FactCheckVerdict.NEEDS_DEEP_RESEARCH.value, 0),
        "claims": rows,
    }
    logger.info(
        "Grounded %s %d: %d claims (%d verified, %d inaccurate, %d unverifiable, "
        "%d need deep research; %d deep-research verdicts kept, %d via KB)",
        subject_type,
        subject_id,
        summary["total_claims"],
        summary["verified"],
        summary["inaccurate"],
        summary["unverifiable"],
        summary["needs_deep_research"],
        kept,
        kb_matched,
    )
    return summary


# Deep-research verdicts a re-ground carries over (#575). CORRECTED is left
# out on purpose: it describes content an earlier correction edited, not the
# claim — re-extracted text that asserts the claim verbatim asserts it again,
# so it falls through to the KB, which keeps the INACCURATE verdict.
_KEPT_DEEP_RESEARCH_VERDICTS = (
    FactCheckVerdict.VERIFIED.value,
    FactCheckVerdict.INACCURATE.value,
    FactCheckVerdict.DISPUTED.value,
    FactCheckVerdict.UNVERIFIABLE.value,
)


def _resolved_deep_research_rows(
    subject_type: str, subject_id: int, db: Session
) -> dict[str, FactCheckClaim]:
    """The subject's deep-research-resolved claim rows keyed by ``claim_key``
    (the KB's ``_normalize``); the newest row wins on a duplicate key."""
    rows = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == subject_type,
            FactCheckClaim.subject_id == subject_id,
            FactCheckClaim.checked_via == FactCheckMethod.DEEP_RESEARCH.value,
            FactCheckClaim.verdict.in_(_KEPT_DEEP_RESEARCH_VERDICTS),
        )
        .order_by(FactCheckClaim.id)
        .all()
    )
    return {key: row for row in rows if (key := _normalize(row.claim_text))}


def ground_article(
    article: Article, research: Research, db: Session, progress_callback=None
) -> dict:
    """Ground a blog article against the research document it was written from."""
    return ground_subject(
        FactCheckSubject.ARTICLE.value,
        article.id,
        article.content or "",
        research.content or "",
        db,
        feature="fact_check_extract_article",
        progress_callback=progress_callback,
    )


def build_suggestion_text(suggestion) -> str:
    """Flatten an ArticleSuggestion's factual surface for claim extraction."""
    parts = [f"Title: {suggestion.title}"]
    if suggestion.main_topic:
        parts.append(f"Main topic: {suggestion.main_topic}")
    if suggestion.sub_topics:
        parts.append("Sub-topics:\n" + "\n".join(f"- {t}" for t in suggestion.sub_topics))
    if suggestion.point_of_view:
        parts.append(f"Point of view: {suggestion.point_of_view}")
    return "\n".join(parts)


# --- Verified-claims knowledge base (issue #378) --------------------------------

# New claims per Haiku matching call.
KB_MATCH_BATCH_SIZE = 20
# Fuzzy-match candidates per claim (bounds the KB list in the match prompt).
KB_CANDIDATES_PER_CLAIM = 8
# Only resolved verdicts are reusable knowledge — PENDING / UNVERIFIABLE /
# NEEDS_DEEP_RESEARCH rows carry nothing worth inheriting.
_KB_REUSABLE_VERDICTS = (
    FactCheckVerdict.VERIFIED.value,
    FactCheckVerdict.INACCURATE.value,
    FactCheckVerdict.DISPUTED.value,
)

_KB_MATCH_LINE = re.compile(r"^MATCH:\s*(\d+)\s*\|\s*(NONE|\d+)\s*$")

_KB_MATCH_PROMPT = """\
You are matching newly extracted factual claims against a knowledge base of \
already-verified claims about Panama's history. Two claims MATCH only when \
they assert THE SAME FACT: the same people/places/events AND the same dates \
and numbers. Be conservative — if any entity, date, or number differs, or if \
one claim is broader than the other, answer NONE. A wrong match silently \
propagates a verdict to a different fact; a missed match merely costs one \
re-verification.

Output exactly one line per new claim, in order, nothing else:
MATCH: <new claim number> | <KB id or NONE>

NEW CLAIMS:
{claims}

KB CLAIMS:
{kb}"""


def _significant_words(text: str) -> set[str]:
    return {w for w in _normalize(text).split() if len(w) >= 4}


def _kb_candidates(claim: ExtractedClaim, kb_rows: list) -> list:
    """KB rows sharing at least two significant words with the claim, best first."""
    words = _significant_words(claim.text)
    scored = []
    for row in kb_rows:
        overlap = len(words & _significant_words(row.claim_text))
        if overlap >= 2:
            scored.append((-overlap, row.id, row))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in scored[:KB_CANDIDATES_PER_CLAIM]]


# Vector candidates (#628). HNSW yields at most ``ef_search`` rows before the
# VERIFIED_CLAIM filter applies (article and article-claim chunks share the
# index), so each claim asks for a wide pool and keeps the top_k above the
# floor itself.
KB_VECTOR_POOL = 100
KB_MATCH_FEATURE = "fact_check_kb_match"


def kb_vector_hits(
    db: Session, texts: list[str], vectors: list[list[float]], *, pool: int = KB_VECTOR_POOL
) -> list[list[tuple[int, float]]]:
    """Per claim, its nearest KB rows as ``(verified_claims id, cosine similarity)``,
    nearest first, reusable verdicts only (INACCURATE / DISPUTED included).

    Reads ``knowledge_chunks`` only through ``retrieval.search`` with the
    caller's precomputed query vectors, so no embedding call happens here.
    """
    from app.services.knowledge import retrieval
    from app.services.knowledge.claims import VERIFIED_CLAIM

    out: list[list[tuple[int, float]]] = []
    for text, vector in zip(texts, vectors, strict=True):
        result = retrieval.search(
            db,
            text,
            mode="vector",
            k=pool,
            candidates=pool,
            source_types=[VERIFIED_CLAIM],
            visibility="internal",
            query_vector=vector,
        )
        hits: list[tuple[int, float]] = []
        seen: set[int] = set()
        for chunk in result.chunks:
            if chunk.vector_distance is None or chunk.source_id in seen:
                continue
            if (chunk.meta or {}).get("verdict") not in _KB_REUSABLE_VERDICTS:
                continue
            seen.add(chunk.source_id)
            hits.append((chunk.source_id, 1.0 - chunk.vector_distance))
        out.append(hits)
    return out


def select_vector_candidates(
    hits: list[tuple[int, float]],
    *,
    top_k: int,
    min_similarity: float,
    exclude_id: int | None = None,
) -> list[int]:
    """KB ids at or above the similarity floor, nearest first, at most ``top_k``."""
    return [kb_id for kb_id, sim in hits if sim >= min_similarity and kb_id != exclude_id][:top_k]


def confirm_kb_candidates(
    claim_texts: list[str],
    fuzzy_ids: list[tuple[int, list[int]]],
    kb_text: dict[int, str],
    *,
    feature: str = KB_MATCH_FEATURE,
    on_message: Callable[[object], None] | None = None,
) -> dict[int, int]:
    """The Haiku confirmation pass. ``fuzzy_ids`` is ``[(claim index, candidate
    KB ids)]``; returns ``{claim index: KB id}`` for the confirmed matches.

    Works on plain values only (see :func:`match_claims_against_kb`). A
    returned id is accepted only when it is among that claim's own candidates.
    ``on_message`` sees each raw response (the replay script reads its usage).
    """
    found: dict[int, int] = {}
    for start in range(0, len(fuzzy_ids), KB_MATCH_BATCH_SIZE):
        batch = fuzzy_ids[start : start + KB_MATCH_BATCH_SIZE]
        union_ids: dict[int, None] = {}
        for _, candidate_ids in batch:
            for kb_id in candidate_ids:
                union_ids[kb_id] = None
        numbered = "\n".join(f"{j + 1}. {claim_texts[idx]}" for j, (idx, _) in enumerate(batch))
        kb_list = "\n".join(f"[{kb_id}] {kb_text[kb_id]}" for kb_id in union_ids)
        message = create_message(
            feature=feature,
            model=HAIKU_MODEL,
            max_tokens=4000,  # thinking headroom at medium effort (#610)
            output_config={"effort": "medium"},
            messages=[
                {
                    "role": "user",
                    "content": _KB_MATCH_PROMPT.format(claims=numbered, kb=kb_list),
                }
            ],
        )
        if on_message is not None:
            on_message(message)
        for line in first_text(message).splitlines():
            match = _KB_MATCH_LINE.match(line.strip())
            if not match:
                continue
            number, kb_ref = match.groups()
            j = int(number) - 1
            if kb_ref == "NONE" or not (0 <= j < len(batch)):
                continue
            claim_index, candidate_ids = batch[j]
            ref_id = int(kb_ref)
            # Guard: the id must be one of THIS claim's own candidates.
            if ref_id in candidate_ids:
                found[claim_index] = ref_id
    return found


def match_claims_against_kb(
    claims: list[ExtractedClaim],
    db: Session,
    *,
    before_llm: Callable[[], None] | None = None,
) -> dict:
    """Match new claims against the verified-claims KB. Returns
    ``{claim_index: VerifiedClaim}`` for the claims that matched.

    Two stages: an exact normalized-text match (free, deterministic), then a
    Haiku confirmation pass over candidates. Conservative by construction —
    Haiku is told to prefer NONE, and a returned KB id is only accepted if it
    was among that specific claim's candidates.

    Candidates come from word overlap (#378) unless
    ``kb_vector_matching_enabled`` (#628) is on, when they are each claim's
    nearest ``VERIFIED_CLAIM`` chunks (see :func:`_match_via_vectors`). The
    vector path falls back to word overlap — once per call, logged — when
    embeddings are disabled or the query embedding fails.

    *before_llm*, when given, is called before each external call phase —
    on the word-overlap path once, only if there is a Haiku pass to make,
    after the KB reads and immediately before the first call; on the vector
    path also before the Voyage query embedding — so a caller that owns the
    transaction can commit and release its pool slot (#586). The Haiku loop
    therefore works on plain values snapshotted from the KB rows, never on
    ORM attributes; the returned rows may be expired by then and refresh on
    first access. This function never commits itself.
    """
    if claims and get_settings().kb_vector_matching_enabled:
        found = _match_via_vectors(claims, db, before_llm=before_llm)
        if found is not None:
            return found
    return _match_via_word_overlap(claims, db, before_llm=before_llm)


def _match_via_word_overlap(
    claims: list[ExtractedClaim],
    db: Session,
    *,
    before_llm: Callable[[], None] | None = None,
) -> dict:
    """The #378 path: load every reusable KB row, word-overlap candidates."""
    from app.models.verified_claim import VerifiedClaim

    kb_rows = db.query(VerifiedClaim).filter(VerifiedClaim.verdict.in_(_KB_REUSABLE_VERDICTS)).all()
    if not kb_rows or not claims:
        return {}

    by_key = {row.claim_key: row for row in kb_rows}
    matches: dict[int, object] = {}
    fuzzy: list[tuple[int, list]] = []
    for i, claim in enumerate(claims):
        exact = by_key.get(_normalize(claim.text))
        if exact is not None:
            matches[i] = exact
            continue
        candidates = _kb_candidates(claim, kb_rows)
        if candidates:
            fuzzy.append((i, candidates))

    # Snapshot plain values: *before_llm* may commit, which expires every
    # loaded row, and touching an expired attribute would re-SELECT and
    # re-pin a pool slot for the rest of the Haiku loop.
    kb_by_id = {row.id: row for row in kb_rows}
    kb_text = {row.id: row.claim_text for row in kb_rows}
    fuzzy_ids = [(i, [row.id for row in candidates]) for i, candidates in fuzzy]

    if fuzzy_ids and before_llm is not None:
        before_llm()

    confirmed = confirm_kb_candidates([c.text for c in claims], fuzzy_ids, kb_text)
    for claim_index, kb_id in confirmed.items():
        matches[claim_index] = kb_by_id[kb_id]

    if matches:
        logger.info("KB matched %d of %d claims", len(matches), len(claims))
    return matches


def _match_via_vectors(
    claims: list[ExtractedClaim],
    db: Session,
    *,
    before_llm: Callable[[], None] | None = None,
) -> dict | None:
    """The #628 path, or None to fall back to word overlap.

    Exact ``claim_key`` match first (one keyed query, not a load of the whole
    KB), then one batched Voyage query embedding for the rest (no statement
    open: *before_llm* runs first), then a vector search per claim, the
    candidate rows by id, and the same Haiku pass.
    """
    from app.models.verified_claim import VerifiedClaim
    from app.services import embeddings

    if not embeddings.embeddings_enabled():
        logger.info("KB matching: embeddings disabled; using word-overlap candidates")
        return None
    settings = get_settings()

    keys = [_normalize(c.text) for c in claims]
    exact_rows = (
        db.query(VerifiedClaim)
        .filter(
            VerifiedClaim.claim_key.in_(set(keys)),
            VerifiedClaim.verdict.in_(_KB_REUSABLE_VERDICTS),
        )
        .all()
    )
    by_key = {row.claim_key: row for row in exact_rows}
    matches: dict[int, object] = {}
    rest: list[int] = []
    for i, key in enumerate(keys):
        exact = by_key.get(key)
        if exact is not None:
            matches[i] = exact
        else:
            rest.append(i)

    if rest:
        if before_llm is not None:
            before_llm()
        texts = [claims[i].text for i in rest]
        try:
            vectors = embeddings.embed_queries(texts, feature=KB_MATCH_FEATURE)
        except (embeddings.EmbeddingsDisabledError, embeddings.EmbeddingsError):
            logger.warning(
                "KB matching: query embedding failed for %d claims; using word-overlap candidates",
                len(rest),
            )
            return None
        hits = kb_vector_hits(db, texts, vectors)
        fuzzy_ids: list[tuple[int, list[int]]] = []
        for i, claim_hits in zip(rest, hits, strict=True):
            candidate_ids = select_vector_candidates(
                claim_hits,
                top_k=settings.kb_vector_top_k,
                min_similarity=settings.kb_vector_min_similarity,
            )
            if candidate_ids:
                fuzzy_ids.append((i, candidate_ids))

        union = {kb_id for _, ids in fuzzy_ids for kb_id in ids}
        kb_rows = (
            db.query(VerifiedClaim)
            .filter(
                VerifiedClaim.id.in_(union),
                VerifiedClaim.verdict.in_(_KB_REUSABLE_VERDICTS),
            )
            .all()
            if union
            else []
        )
        # A chunk can outlive its row's verdict until the next reindex: keep
        # only candidates whose row is still reusable. Snapshot plain values
        # (see the word-overlap path) before *before_llm* may expire them.
        kb_by_id = {row.id: row for row in kb_rows}
        kb_text = {row.id: row.claim_text for row in kb_rows}
        fuzzy_ids = [(i, [kb_id for kb_id in ids if kb_id in kb_by_id]) for i, ids in fuzzy_ids]
        fuzzy_ids = [(i, ids) for i, ids in fuzzy_ids if ids]

        if fuzzy_ids and before_llm is not None:
            before_llm()
        confirmed = confirm_kb_candidates([c.text for c in claims], fuzzy_ids, kb_text)
        for claim_index, kb_id in confirmed.items():
            matches[claim_index] = kb_by_id[kb_id]

    if matches:
        logger.info("KB matched %d of %d claims (vector candidates)", len(matches), len(claims))
    return matches


def upsert_verified_claims(rows: list[FactCheckClaim], db: Session) -> int:
    """Feed resolved external verdicts into the KB (latest verification wins).

    Only rows with a reusable verdict reached via DEEP_RESEARCH (or the
    historical WEB_SEARCH, a tier removed in #751) qualify — KB_MATCH rows are
    already *from* the KB, and grounding verdicts are relative to one research
    doc, not the historical record. Flushes but does not commit. Returns the number of DISTINCT claims upserted.

    Two claims in one batch can normalize to the same key (the same fact
    extracted from two different suggestions — e.g. "Napoleon invaded Spain
    in 1808" from #53 and #54). The session runs with ``autoflush=False``, so
    a second such row would not see the first one pending and both would be
    INSERTed, violating the unique ``claim_key`` and 500ing the whole upload.
    Track what this batch has already staged and update it in place instead.
    """
    from app.models.verified_claim import VerifiedClaim

    staged: dict[str, VerifiedClaim] = {}
    for row in rows:
        if row.verdict not in _KB_REUSABLE_VERDICTS:
            continue
        if row.checked_via not in (
            FactCheckMethod.WEB_SEARCH.value,
            FactCheckMethod.DEEP_RESEARCH.value,
        ):
            continue
        key = _normalize(row.claim_text)
        if not key:
            continue
        existing = staged.get(key)
        if existing is None:
            existing = db.query(VerifiedClaim).filter(VerifiedClaim.claim_key == key).first()
        if existing is not None:
            # Last write wins, whether the row came from the DB or this batch.
            existing.verdict = row.verdict
            existing.correction = row.correction
            existing.citation = row.citation
            existing.source_method = row.checked_via
        else:
            existing = VerifiedClaim(
                claim_key=key,
                claim_text=row.claim_text,
                claim_type=row.claim_type,
                verdict=row.verdict,
                correction=row.correction,
                citation=row.citation,
                source_method=row.checked_via,
            )
            db.add(existing)
        staged[key] = existing
    upserted = len(staged)
    db.flush()
    return upserted


def _subject_summary(subject_type: str, subject_id: int, db: Session) -> dict:
    """Summary dict over ALL of a subject's persisted claim rows."""
    rows = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == subject_type,
            FactCheckClaim.subject_id == subject_id,
        )
        .order_by(FactCheckClaim.id)
        .all()
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    return {"total_claims": len(rows), "counts": counts, "claims": rows}


# --- Extract-only deep-research-first mode (issue #389) --------------------------


def extract_for_deep_research(
    subject_type: str,
    subject_id: int,
    text: str,
    db: Session,
    *,
    progress_callback=None,
) -> dict:
    """Extract-only external tier (#389): extract claims from *text*, inherit
    verdicts for claims already in the verified KB (#378), and persist every
    remaining claim as ``NEEDS_DEEP_RESEARCH`` — no web search. The
    deep-research loop (#377) resolves the queue for free.

    Rows go to NEEDS_DEEP_RESEARCH, not PENDING: ``generate_deep_research_prompt``
    filters on it, and the deep-research loop only resolves that queue.

    **Half-checked subjects** (rows already exist and some are PENDING): no
    re-extraction and no delete. The PENDING rows are KB-matched, and the rest
    flip to NEEDS_DEEP_RESEARCH in place; resolved verdicts are preserved.
    Only the removed web-search tier (#751) wrote PENDING claim rows (as
    capped-run overflow), and prod had none left when it was removed; this
    branch is the convergence path should one still turn up.

    Flushes but does not commit — the caller owns the transaction. Returns a
    summary dict: total / per-verdict counts / the subject's rows.
    """
    pending = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == subject_type,
            FactCheckClaim.subject_id == subject_id,
            FactCheckClaim.verdict == FactCheckVerdict.PENDING.value,
        )
        .order_by(FactCheckClaim.id)
        .all()
    )
    now = datetime.now(UTC)
    if pending:
        claims = [ExtractedClaim(text=row.claim_text, claim_type=row.claim_type) for row in pending]
        kb_matches = match_claims_against_kb(claims, db)
        for i, row in enumerate(pending):
            kb_row = kb_matches.get(i)
            if kb_row is not None:
                row.verdict = kb_row.verdict
                row.correction = kb_row.correction
                row.citation = kb_row.citation
                row.checked_via = FactCheckMethod.KB_MATCH.value
                row.checked_at = now
            else:
                row.verdict = FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
        db.flush()
        summary = _subject_summary(subject_type, subject_id, db)
        logger.info(
            "Extract-only fact-check for %s %d: flipped %d PENDING rows (%d via KB), now %s",
            subject_type,
            subject_id,
            len(pending),
            len(kb_matches),
            summary["counts"],
        )
        return summary

    feature = f"fact_check_extract_{subject_type.lower()}"
    claims = extract_claims(text, feature=feature)
    kb_matches = match_claims_against_kb(claims, db)

    # Re-running a check replaces the subject's rows wholesale.
    db.query(FactCheckClaim).filter(
        FactCheckClaim.subject_type == subject_type,
        FactCheckClaim.subject_id == subject_id,
    ).delete()

    rows: list[FactCheckClaim] = []
    for i, claim in enumerate(claims):
        kb_row = kb_matches.get(i)
        if kb_row is not None:
            rows.append(
                FactCheckClaim(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    claim_text=claim.text,
                    claim_type=claim.claim_type,
                    verdict=kb_row.verdict,
                    correction=kb_row.correction,
                    citation=kb_row.citation,
                    checked_via=FactCheckMethod.KB_MATCH.value,
                    checked_at=now,
                )
            )
        else:
            rows.append(
                FactCheckClaim(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    claim_text=claim.text,
                    claim_type=claim.claim_type,
                    verdict=FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
                )
            )
    db.add_all(rows)
    db.flush()

    if progress_callback:
        progress_callback(len(rows), len(rows), f"extracted {len(rows)} claims")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    summary = {"total_claims": len(rows), "counts": counts, "claims": rows}
    logger.info(
        "Extract-only fact-check for %s %d: %d claims %s",
        subject_type,
        subject_id,
        len(rows),
        counts,
    )
    return summary


# --- Background fact-check jobs (issue #384) ------------------------------------

# Cap the stored error message so a giant traceback can't blow up the row.
_MAX_JOB_ERROR_LEN = 2000

_ACTIVE_JOB_STATUSES = (CrawlJobStatus.PENDING.value, CrawlJobStatus.RUNNING.value)


def active_fact_check_job(db: Session, subject_type: str, subject_id: int) -> CrawlJob | None:
    """Return the in-flight fact-check job for this subject, or ``None``.

    Different subjects may run concurrently; the same subject must not — a
    re-run deletes and replaces the subject's rows, so two overlapping runs
    would corrupt each other. Mirrors ``crawl_jobs.active_job``'s staleness
    handling by ignoring jobs older than an hour (dead worker).
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=1)
    stmt = (
        select(CrawlJob)
        .where(
            CrawlJob.job_type == CrawlJobType.FACT_CHECK.value,
            CrawlJob.status.in_(_ACTIVE_JOB_STATUSES),
            CrawlJob.created_at >= cutoff,
        )
        .order_by(CrawlJob.created_at.desc())
    )
    for job in db.execute(stmt).scalars():
        params = job.params or {}
        if params.get("subject_type") == subject_type and params.get("subject_id") == subject_id:
            return job
    return None


class FactCheckJobInFlightError(RuntimeError):
    """A fact-check job is already running for this subject.

    Carries the in-flight job so the API layer can name it in the 409.
    """

    def __init__(self, job: CrawlJob):
        self.job = job
        super().__init__(f"Fact-check job {job.id} is already running for this item")


def create_fact_check_job(db: Session, subject_type: str, subject_id: int) -> CrawlJob:
    """Create and commit a fact-check job row for one subject (#384).

    The shared core behind every router's kickoff endpoint: guards against a
    second in-flight run for the SAME subject (a re-run replaces the subject's
    rows, so overlapping runs would corrupt each other), then persists the job.
    Scheduling the background task is the caller's job — that needs the
    request's ``BackgroundTasks``.

    Raises ``FactCheckJobInFlightError`` if one is already running.
    """
    existing = active_fact_check_job(db, subject_type, subject_id)
    if existing is not None:
        raise FactCheckJobInFlightError(existing)
    params = {"subject_type": subject_type, "subject_id": subject_id}
    job = CrawlJob(job_type=CrawlJobType.FACT_CHECK.value, params=params)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _external_tier_check(
    subject_type: str, subject_id: int, text: str, db: Session, progress
) -> dict:
    """Run a suggestion/research check through the extract-only external tier (#389).

    Older job rows may carry a ``mode`` param (``deep_research``, or the
    ``web_search`` tier removed in #751); it is ignored.
    """
    return extract_for_deep_research(subject_type, subject_id, text, db, progress_callback=progress)


def edu_material_source_research(material: EduMaterial, db: Session) -> str:
    """The research text an edu material must be grounded against.

    A material hangs off exactly one of ``edu_research`` or blog ``research``
    (enforced by ``ck_edu_materials_one_research_fk``), so resolve whichever is
    set. Raises when the source is missing or empty — grounding against nothing
    would mark every claim unverifiable and read as a clean check.
    """
    if material.research_id is not None:
        research = db.get(EduResearch, material.research_id)
        label = f"Edu research {material.research_id}"
    elif material.blog_research_id is not None:
        research = db.get(Research, material.blog_research_id)
        label = f"Blog research {material.blog_research_id}"
    else:
        raise ValueError(f"Edu material {material.id} has no research to ground against")

    if research is None or not (research.content or "").strip():
        raise ValueError(f"{label} for edu material {material.id} not found or has no content")
    return research.content


#: Material types that can be grounded, and where their checkable prose lives.
#: ``video`` is deliberately absent: an MP4 is a render of the deck's narration,
#: so checking the deck (#464) IS the video's fact-check.
GROUNDABLE_MATERIAL_TYPES = (MaterialType.GUIDE.value, MaterialType.SLIDES.value)


def edu_material_prose(material: EduMaterial) -> str:
    """The checkable text of an edu material, by type.

    Neither type keeps its real content on ``EduMaterial.content`` — that is a
    derived projection in both cases. Guides read their structured blocks (the
    PDF's source), decks read their ``edu_slides`` rows (the renderer's and the
    TTS's source).
    """
    if material.material_type == MaterialType.GUIDE.value:
        return blocks_prose(material.content_blocks)
    return slides_prose(material.slides)


def _ground_edu_material(db: Session, subject_id: int, progress) -> dict:
    """Ground a generated edu material against its source research (#461, #464).

    Grounded against the **full** research, never a material's condensed
    ``source_briefing``: the briefing is an intermediate the deck was generated
    from, so judging against it would ratify anything the condense pass dropped
    or distorted. The full document is the authority.
    """
    material = db.get(EduMaterial, subject_id)
    if material is None:
        raise ValueError(f"Edu material {subject_id} not found")
    if material.material_type not in GROUNDABLE_MATERIAL_TYPES:
        raise ValueError(
            f"Edu material {subject_id} is a {material.material_type!r}; only "
            f"{' and '.join(GROUNDABLE_MATERIAL_TYPES)} can be grounded "
            "(a video is a render of its deck's narration — check the deck)"
        )
    if material.material_type == MaterialType.GUIDE.value:
        # content_blocks is the PDF's source of truth and the only correctable
        # target; the markdown projection is derived. A legacy block-less guide
        # (pre-#340) would produce claims no correction could ever apply to.
        if not material.content_blocks:
            raise ValueError(
                f"Edu material {subject_id} has no content_blocks (legacy markdown guide); "
                "regenerate it before fact-checking"
            )
        feature = "fact_check_extract_edu_guide"
    else:
        if not material.slides:
            raise ValueError(
                f"Edu material {subject_id} has no slide rows; generate the deck before "
                "fact-checking"
            )
        feature = "fact_check_extract_edu_slides"

    research_content = edu_material_source_research(material, db)
    return ground_subject(
        FactCheckSubject.EDU_MATERIAL.value,
        material.id,
        edu_material_prose(material),
        research_content,
        db,
        feature=feature,
        progress_callback=progress,
    )


def _run_fact_check_for_subject(db: Session, job: CrawlJob, progress) -> dict:
    """Dispatch the job to the right tier and return a uniform stats dict."""
    params = job.params or {}
    subject_type = params.get("subject_type")
    subject_id = params.get("subject_id")

    if subject_type == FactCheckSubject.ARTICLE.value:
        article = db.get(Article, subject_id)
        if article is None or not (article.content or "").strip():
            raise ValueError(f"Article {subject_id} not found or has no content")
        research = db.get(Research, article.research_id)
        if research is None or not (research.content or "").strip():
            raise ValueError(f"Research for article {subject_id} not found or empty")
        summary = ground_article(article, research, db, progress_callback=progress)
    elif subject_type == FactCheckSubject.SUGGESTION.value:
        suggestion = db.get(ArticleSuggestion, subject_id)
        if suggestion is None:
            raise ValueError(f"Suggestion {subject_id} not found")
        summary = _external_tier_check(
            subject_type, subject_id, build_suggestion_text(suggestion), db, progress
        )
    elif subject_type == FactCheckSubject.RESEARCH.value:
        research = db.get(Research, subject_id)
        if research is None or not (research.content or "").strip():
            raise ValueError(f"Research {subject_id} not found or has no content")
        summary = _external_tier_check(subject_type, subject_id, research.content, db, progress)
    elif subject_type == FactCheckSubject.EDU_MATERIAL.value:
        summary = _ground_edu_material(db, subject_id, progress)
    elif subject_type == FactCheckSubject.EDU_RESEARCH.value:
        # Edu research is English markdown in a single ``content`` column, so it
        # takes the same external tier as blog research (#460). Materials
        # generated from it ground against it separately (#461).
        edu_research = db.get(EduResearch, subject_id)
        if edu_research is None or not (edu_research.content or "").strip():
            raise ValueError(f"Edu research {subject_id} not found or has no content")
        summary = _external_tier_check(subject_type, subject_id, edu_research.content, db, progress)
    else:
        raise ValueError(f"Unknown fact-check subject_type: {subject_type!r}")

    # Uniform stats regardless of tier (grounding summaries carry fixed keys).
    counts: dict[str, int] = {}
    for row in summary["claims"]:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "total_claims": len(summary["claims"]),
        "counts": counts,
    }


def run_fact_check_job(job_id: int) -> None:
    """Execute a fact-check job to completion, persisting progress and result.

    Runs in a FastAPI ``BackgroundTask`` with its own DB session (the request's
    session closes with the response). Never raises — failures land on the job
    row and go to Sentry. Mirrors ``crawl_jobs.run_crawl_job``.

    After a success, with the job's session closed, the claim chunks the run
    may have changed are reindexed (#625): an ARTICLE job re-replaced that
    article's claims; an external-tier job may have upserted the KB. That
    reindex never raises and runs after the job row is already SUCCEEDED.
    An ARTICLE job then runs the shadow-mode cross-article consistency check
    (#631) when ``cross_article_consistency_enabled`` is on; it never raises.
    """
    stats = None
    db = SessionLocal()
    try:
        job = db.get(CrawlJob, job_id)
        if job is None:
            logger.error("Fact-check job %d not found at execution time", job_id)
            return

        job.status = CrawlJobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        db.commit()

        def progress(done: int, total: int, current: str) -> None:
            job.units_done = done
            job.units_total = total
            job.current_unit = current[:500] if current else None
            db.commit()

        stats = _run_fact_check_for_subject(db, job, progress)
        db.commit()  # the tier services flush; the runner owns the transaction

        job.stats = stats
        job.status = CrawlJobStatus.SUCCEEDED.value
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("Fact-check job %d succeeded: %s", job_id, stats)

    except Exception as e:
        stats = None
        logger.exception("Fact-check job %d failed", job_id)
        sentry_sdk.capture_exception(e)
        # Recording the failure needs the DB too — if the DB itself is the
        # problem, log and give up rather than raising out of a BackgroundTask.
        try:
            db.rollback()
            job = db.get(CrawlJob, job_id)
            if job is not None:
                job.status = CrawlJobStatus.FAILED.value
                job.error = str(e)[:_MAX_JOB_ERROR_LEN]
                job.finished_at = datetime.now(UTC)
                db.commit()
        except Exception:
            logger.exception("Failed to record fact-check job %d failure", job_id)
    finally:
        db.close()

    if stats is not None:
        _reindex_claim_chunks(stats)
        _check_cross_article_consistency(stats)


# Subjects checked by the external (extract-only + deep-research) tier.
_EXTERNAL_SUBJECTS = (
    FactCheckSubject.SUGGESTION.value,
    FactCheckSubject.RESEARCH.value,
    FactCheckSubject.EDU_RESEARCH.value,
)


def _reindex_claim_chunks(stats: dict) -> None:
    """Reindex the claim chunks a successful job touched (#625). Never raises."""
    try:
        if stats.get("subject_type") == FactCheckSubject.ARTICLE.value:
            reindex_claims([stats["subject_id"]])
        elif stats.get("subject_type") in _EXTERNAL_SUBJECTS:
            reindex_claims(verified=True)
    except Exception as e:  # noqa: BLE001 — reindex_claims never raises; belt and braces
        logger.exception("Claim reindex after a fact-check job failed")
        sentry_sdk.capture_exception(e)


def _check_cross_article_consistency(stats: dict) -> None:
    """Shadow-mode cross-article consistency check of an ARTICLE job (#631).

    Behind ``cross_article_consistency_enabled`` (default off: no query, no
    call). Runs after the job is SUCCEEDED and its session closed, and after
    the claims reindex (it compares against *other* articles, so it does not
    need this article's fresh chunks). Never raises.
    """
    if stats.get("subject_type") != FactCheckSubject.ARTICLE.value:
        return
    if not get_settings().cross_article_consistency_enabled:
        return
    try:
        run_consistency_check(stats["subject_id"])
    except Exception as e:  # noqa: BLE001 — run_consistency_check never raises; belt and braces
        logger.exception("Cross-article consistency check after a fact-check job failed")
        sentry_sdk.capture_exception(e)


# --- Deep-research loop (issue #377) ------------------------------------------

# A deep-research run's internal search budget exhausts before ~30 claims
# per prompt (a 2026-07-13 run covered 29 of 32 before running dry), so cap
# batches at 25 to leave headroom (mirrors the /fact-check-prompt skill,
# which this API surface replaces for automation).
DEEP_RESEARCH_BATCH_LIMIT = 25

# Verdict labels the deep-research tier may write. NEEDS_DEEP_RESEARCH is not
# one of them — a claim the deep researcher can't resolve stays pending via a
# missing/UNVERIFIABLE line, it is never re-punted.
_DEEP_VERDICTS = {
    "VERIFIED": FactCheckVerdict.VERIFIED.value,
    "INACCURATE": FactCheckVerdict.INACCURATE.value,
    "DISPUTED": FactCheckVerdict.DISPUTED.value,
    "UNVERIFIABLE": FactCheckVerdict.UNVERIFIABLE.value,
}

# Definitive verdicts must carry a citation; an uncited one is skipped (the
# row stays NEEDS_DEEP_RESEARCH) — same conservatism as the search tier.
_DEEP_VERDICTS_REQUIRING_CITATION = {"VERIFIED", "INACCURATE", "DISPUTED"}
# ``VERDICT: n | LABEL | note | citation`` — the line shape deep-research
# verdict documents use.
_DEEP_VERDICT_LINE = re.compile(r"^VERDICT:\s*(\d+)\s*\|\s*([A-Z_]+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*$")

# Skip reason for a verdict whose claim was already resolved (a stale or
# replayed document); submit_deep_research_verdicts keys ``replayed`` off it.
_NO_LONGER_PENDING = "claim is no longer pending"

# Template kept in lockstep with .claude/skills/fact-check-prompt/SKILL.md —
# the skill and this endpoint must emit the same contract.
_DEEP_RESEARCH_PROMPT = """\
REF: fact_check_{batch_id}

You are verifying factual claims for a college-level historical education
platform about Panama. The claims below require verification against primary
and scholarly secondary sources (academic journals, books by recognized
historians, archival material); your job is to check each one against such
sources.

THE DELIVERABLE IS A MACHINE-READ VERDICT TABLE. Your report is parsed by a
script, not read by a person. The script looks for lines beginning with
"VERDICT:" and IGNORES EVERYTHING ELSE. A beautifully researched report that
states its verdicts only in prose, headings, or a summary table is DISCARDED
IN FULL and the whole research run is wasted. Write the verdict lines first,
before any narrative, and never substitute another format for them.

Your report MUST open with a section headed exactly "## Verdicts" containing
nothing but verdict lines — one per claim you researched, in the order the
claims are listed below. Each line, on a SINGLE line:

VERDICT: <claim number> | <VERIFIED or INACCURATE or DISPUTED or UNVERIFIABLE> | <note> | <citations>

Worked example of the required output:

## Verdicts
VERDICT: 41 | VERIFIED | - | Castillero Calvo, Historia General de Panamá, 2004
VERDICT: 42 | INACCURATE | The city was founded in 1519, not 1521 | Hernández Mora, Martín & Aram, Historical Archaeology 55(2), 2021
VERDICT: 43 | DISPUTED | Sources split between 1563 (decree) and 1565 (resumed function) | Wikipedia, Real Audiencia of Panama; PARES, A.G.I. Panamá 236
VERDICT: 44 | UNVERIFIABLE | - | -

Field rules:
- <claim number> — the number printed with the claim below. Copy it exactly;
  never renumber, never use list position.
- <note> — the correction for INACCURATE, the competing positions for
  DISPUTED, a dash otherwise.
- <citations> — full source citations (author, title, year — include a URL
  when one exists), semicolon-separated. MANDATORY for VERIFIED, INACCURATE,
  and DISPUTED (an uncited one is rejected); a dash for UNVERIFIABLE.
- Never wrap a verdict line, never bold or bullet it, never put prose on it.

After "## Verdicts" you may add "## Notes" with as much discussion, nuance,
and narrative as you like — it is ignored by the processor, so put every
caveat there rather than bending the verdict lines.

For EACH numbered claim, determine a verdict:
- VERIFIED — reliable sources confirm the claim
- INACCURATE — reliable sources contradict it; state the correct fact
- DISPUTED — reputable sources genuinely disagree; summarize the positions
- UNVERIFIABLE — you could not find sufficient reliable sourcing either way

Pick exactly one of those four labels per line. Hedged labels
("PARTIALLY VERIFIED", "VERIFIED with nuance", "VERIFIED / DISPUTED") are
NOT in the vocabulary and are rejected by the parser: choose the closest of
the four and put the nuance in <note> or in "## Notes".

Ground every verdict in sources you actually retrieved and read DURING THIS
SESSION. NEVER assign VERIFIED, INACCURATE, or DISPUTED from your own
knowledge alone — those verdicts require consulted sources, and their
citations must refer to those sources, never to works cited from memory.
UNVERIFIABLE is a research outcome, not an error state: use it only for a
claim you actually searched and still could not source either way. If your
research tools fail and you cannot search for a claim at all, OMIT that
claim's verdict line entirely and describe the tool failure in prose — an
unanswered claim is automatically re-queued for a later run, while any
verdict, even UNVERIFIABLE, is recorded as a finished check.

CLAIMS:
{claims}

REMINDER — the deliverable: begin your report with "## Verdicts" followed by
one "VERDICT: <claim number> | <label> | <note> | <citations>" line per claim
you researched, using only the four labels above. Prose-only verdicts cannot
be read by the processor and the run is discarded. Put narrative in "## Notes"
after the verdict lines."""


# The subject types that can hold NEEDS_DEEP_RESEARCH claims, i.e. the valid
# scopes of a deep-research batch. EDU_RESEARCH runs the same external tier as
# RESEARCH (#460). ARTICLE grounding queues claims its research does not cover
# (ABSENT) here too (#555), and the unscoped call already includes them;
# scoping lets a caller drain one article's claims so its
# WAIT_ARTICLE_CLAIMS_RESOLVED gate clears (#574). EDU_MATERIAL is absent: its
# grounding maps ABSENT to UNVERIFIABLE.
DEEP_RESEARCH_SCOPES = (
    FactCheckSubject.SUGGESTION.value,
    FactCheckSubject.RESEARCH.value,
    FactCheckSubject.EDU_RESEARCH.value,
    FactCheckSubject.ARTICLE.value,
)


class DeepResearchScopeError(ValueError):
    """An invalid scope for a deep-research batch (the HTTP route's 400)."""


class VerdictDocumentRejected(ValueError):
    """A verdict document that, as a whole, wrote nothing (the HTTP route's 400).

    ``str(err)`` is the operator-facing reason; it never quotes the document.
    """


def deep_research_batch(
    db: Session,
    subject_type: str | None = None,
    subject_id: int | None = None,
    limit: int = DEEP_RESEARCH_BATCH_LIMIT,
) -> dict | None:
    """Validate a batch scope, then :func:`generate_deep_research_prompt`.

    The one entry point of ``POST .../fact-check/deep-research/generate-prompt``
    and the MCP ``get_deep_research_batch`` tool (#641). ``subject_type`` is
    case-insensitive; raises :class:`DeepResearchScopeError` for a type outside
    :data:`DEEP_RESEARCH_SCOPES` or a ``subject_id`` without a type. Returns
    ``None`` when nothing is pending.
    """
    subject_type = subject_type.upper() if subject_type else None
    if subject_type is not None and subject_type not in DEEP_RESEARCH_SCOPES:
        raise DeepResearchScopeError(
            f"subject_type must be one of {', '.join(DEEP_RESEARCH_SCOPES)}"
        )
    if subject_id is not None and subject_type is None:
        raise DeepResearchScopeError("subject_id requires subject_type")
    return generate_deep_research_prompt(db, subject_type, subject_id, limit=limit)


def submit_deep_research_verdicts(
    db: Session,
    content: str,
    claim_ids: list[int] | None = None,
) -> dict:
    """Write a verdict document back and commit; the shared write path (#641).

    Used by ``POST .../fact-check/deep-research/verdicts`` and the MCP
    ``submit_fact_check_verdicts`` tool. Runs
    :func:`process_deep_research_verdicts` (all its conservatism), raises
    :class:`VerdictDocumentRejected` when the whole document is a failed run
    or holds no ``VERDICT:`` line (nothing written, nothing committed), and
    otherwise commits. Does **not** reindex claims: the caller does that once
    its session is done (``reindex_claims`` makes Voyage calls).

    Adds ``replayed`` to the result: True when nothing was written because
    every parsed verdict's claim was already resolved (the same document
    submitted twice). That is a success, not an error, so a retrying client
    converges.
    """
    result = process_deep_research_verdicts(content, db, claim_ids)
    if result["rejected_reason"]:
        raise VerdictDocumentRejected(f"Verdict document rejected: {result['rejected_reason']}")
    if result["written"] == 0 and not result["skipped"]:
        raise VerdictDocumentRejected("No VERDICT lines found in the document")
    db.commit()
    result["replayed"] = result["written"] == 0 and all(
        reason.startswith(_NO_LONGER_PENDING) for _cid, reason in result["skipped"]
    )
    return result


def generate_deep_research_prompt(
    db: Session,
    subject_type: str | None = None,
    subject_id: int | None = None,
    *,
    limit: int = DEEP_RESEARCH_BATCH_LIMIT,
) -> dict | None:
    """Compile pending NEEDS_DEEP_RESEARCH claims into a verification prompt.

    Takes the oldest ``limit`` claims (default and ceiling
    :data:`DEEP_RESEARCH_BATCH_LIMIT`; ordered like the skill: subject_type,
    subject_id, id), optionally scoped to one
    subject. Returns ``None`` when nothing is pending, else a dict with
    ``batch_id``, ``claim_ids``, ``pending_total`` (including claims beyond
    the batch cap), and ``prompt_text``. Stateless: nothing is persisted —
    verdict upload re-validates row state.
    """
    query = db.query(FactCheckClaim).filter(
        FactCheckClaim.verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
    )
    if subject_type is not None:
        query = query.filter(FactCheckClaim.subject_type == subject_type)
    if subject_id is not None:
        query = query.filter(FactCheckClaim.subject_id == subject_id)

    pending_total = query.count()
    if pending_total == 0:
        return None
    claims = (
        query.order_by(FactCheckClaim.subject_type, FactCheckClaim.subject_id, FactCheckClaim.id)
        .limit(max(1, min(limit, DEEP_RESEARCH_BATCH_LIMIT)))
        .all()
    )

    batch_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    claim_lines = "\n".join(f"CLAIM {c.id} ({c.claim_type}): {c.claim_text}" for c in claims)
    return {
        "batch_id": batch_id,
        "claim_ids": [c.id for c in claims],
        "pending_total": pending_total,
        "prompt_text": _DEEP_RESEARCH_PROMPT.format(batch_id=batch_id, claims=claim_lines),
    }


def process_deep_research_verdicts(
    content: str,
    db: Session,
    claim_ids: list[int] | None = None,
) -> dict:
    """Parse a deep-research verdict document and write the verdicts back.

    Mirrors the /fact-check-process skill's validation exactly: last
    occurrence wins for duplicate ids; off-vocabulary verdicts are skipped;
    VERIFIED/INACCURATE/DISPUTED require a non-dash citation; rows must exist
    and still be NEEDS_DEEP_RESEARCH (stale docs from a re-run are skipped);
    with *claim_ids* (the batch the prompt asked about), verdicts outside the
    batch are skipped and unanswered batch ids are reported as missing.

    Writes ``checked_via='DEEP_RESEARCH'`` and feeds definitive verdicts into
    the verified-claims KB. Flushes but does not commit. Returns
    ``{written, skipped: [(claim_id, reason)], missing_claim_ids, kb_upserts,
    article_ids, rejected_reason}`` (``article_ids``: the ARTICLE subjects whose
    claims were written, for the claim-chunk reindex, #625) — ``rejected_reason`` is None normally, or a message
    when the whole document was rejected as a failed run (see below) and
    nothing was written.
    """
    parsed: dict[int, tuple[str, str, str]] = {}
    for line in content.splitlines():
        match = _DEEP_VERDICT_LINE.match(line.strip())
        if match is None:
            continue
        cid, label, note, citation = match.groups()
        parsed[int(cid)] = (label, note.strip(), citation.strip())

    # A healthy research run grounds at least one verdict. A document whose
    # every verdict line is an uncited UNVERIFIABLE is the signature of a run
    # whose web tools never came up — the prompt tells such runs to OMIT the
    # lines (#392), but broken runs have emitted them anyway (2026-07-13),
    # and writing them records a whole batch of bogus "finished checks".
    # Reject the document wholesale so every row stays NEEDS_DEEP_RESEARCH
    # and re-queues. The rare genuinely-unverifiable-only batch stays queued
    # too; the operator can record it deliberately via /fact-check-process.
    if parsed and all(
        label == "UNVERIFIABLE" and citation in ("", "-")
        for label, _note, citation in parsed.values()
    ):
        logger.warning(
            "Deep-research verdict document rejected: all %d verdicts are "
            "uncited UNVERIFIABLE (failed-run signature); nothing written",
            len(parsed),
        )
        return {
            "written": 0,
            "skipped": [],
            "missing_claim_ids": sorted(claim_ids if claim_ids else parsed.keys()),
            "kb_upserts": 0,
            "article_ids": [],
            "rejected_reason": (
                f"all {len(parsed)} verdicts are uncited UNVERIFIABLE — the "
                "signature of a research run whose web tools failed; nothing "
                "was written and the claims remain queued"
            ),
        }

    skipped: list[tuple[int, str]] = []
    written_rows: list[FactCheckClaim] = []
    now = datetime.now(UTC)

    for cid, (label, note, citation) in sorted(parsed.items()):
        if claim_ids is not None and cid not in claim_ids:
            skipped.append((cid, "not in this batch's claim_ids"))
            continue
        if label not in _DEEP_VERDICTS:
            skipped.append((cid, f"unknown verdict label '{label}'"))
            continue
        if label in _DEEP_VERDICTS_REQUIRING_CITATION and citation in ("", "-"):
            skipped.append((cid, f"{label} verdict without a citation"))
            continue
        row = db.query(FactCheckClaim).filter(FactCheckClaim.id == cid).first()
        if row is None:
            skipped.append((cid, "claim no longer exists"))
            continue
        if row.verdict != FactCheckVerdict.NEEDS_DEEP_RESEARCH.value:
            skipped.append((cid, f"{_NO_LONGER_PENDING} (verdict={row.verdict})"))
            continue

        row.verdict = _DEEP_VERDICTS[label]
        row.correction = note if note and note != "-" else None
        row.citation = citation if citation and citation != "-" else None
        row.checked_via = FactCheckMethod.DEEP_RESEARCH.value
        row.checked_at = now
        written_rows.append(row)

    db.flush()
    kb_upserts = upsert_verified_claims(written_rows, db)
    article_ids = sorted(
        {r.subject_id for r in written_rows if r.subject_type == FactCheckSubject.ARTICLE.value}
    )

    missing = sorted(set(claim_ids or []) - set(parsed.keys()))
    logger.info(
        "Deep-research verdicts: %d written, %d skipped, %d missing, %d KB upserts",
        len(written_rows),
        len(skipped),
        len(missing),
        kb_upserts,
    )
    return {
        "written": len(written_rows),
        "skipped": skipped,
        "missing_claim_ids": missing,
        "kb_upserts": kb_upserts,
        "article_ids": article_ids,
        "rejected_reason": None,
    }
