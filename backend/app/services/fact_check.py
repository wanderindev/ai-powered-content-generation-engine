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

* :func:`fact_check_subject` (#376) — the automated half of the hybrid
  external tier: Sonnet + the Anthropic web-search server tool verifies
  suggestion/research claims against reliable web sources. Claims the search
  pass cannot confidently resolve are marked ``NEEDS_DEEP_RESEARCH`` for the
  manual claude.ai deep-research loop (#377) — the model is prompted to prefer
  punting over guessing, since reputable academic sources are often not
  reachable by web search.

* :func:`extract_for_deep_research` (#389) — the extract-only external tier
  and the **default** for suggestions/research: extraction + KB matching
  alone (Haiku, pennies), with every unresolved claim sent straight to
  ``NEEDS_DEEP_RESEARCH`` for the free deep-research loop. The web-search
  tier above stays in place as an API-only escape hatch (``mode=web_search``
  on the fact-check endpoints).

All tiers are deliberately verdict-conservative: anything malformed or missing
from an LLM response degrades to ``UNVERIFIABLE`` / ``NEEDS_DEEP_RESEARCH``,
never to ``VERIFIED``.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.fact_check import (
    FactCheckClaim,
    FactCheckMethod,
    FactCheckSubject,
    FactCheckVerdict,
    FactClaimType,
)
from app.models.research import Research
from app.services.llm import HAIKU_MODEL, SONNET_MODEL, create_message, first_text

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
            max_tokens=4000,
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

# Grounding label -> stored verdict. Anything else (including a missing line)
# degrades to UNVERIFIABLE.
_GROUNDING_VERDICTS = {
    "SUPPORTED": FactCheckVerdict.VERIFIED.value,
    "CONTRADICTED": FactCheckVerdict.INACCURATE.value,
    "ABSENT": FactCheckVerdict.UNVERIFIABLE.value,
}

_GROUNDING_PROMPT = """\
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
{research}

CLAIMS:
{claims}"""


def _judge_batch(claims: list[ExtractedClaim], research_content: str) -> dict[int, tuple[str, str]]:
    """Judge one batch against the research. Returns {index: (label, note)}."""
    numbered = "\n".join(f"{i + 1}. {c.text}" for i, c in enumerate(claims))
    message = create_message(
        feature="fact_check_grounding",
        model=SONNET_MODEL,
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": _GROUNDING_PROMPT.format(research=research_content, claims=numbered),
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


def ground_article(
    article: Article, research: Research, db: Session, progress_callback=None
) -> dict:
    """Run the grounding check: extract the article's claims, judge each against
    the research document, and replace the article's ``fact_check_claims`` rows.

    Flushes but does not commit — the caller owns the transaction. Returns a
    summary dict: total / per-verdict counts / the persisted rows.
    """
    claims = extract_claims(article.content or "", feature="fact_check_extract_article")

    verdicts: dict[int, tuple[str, str]] = {}
    research_content = research.content or ""
    for start in range(0, len(claims), GROUNDING_BATCH_SIZE):
        batch = claims[start : start + GROUNDING_BATCH_SIZE]
        for index, verdict in _judge_batch(batch, research_content).items():
            verdicts[start + index] = verdict
        if progress_callback:
            done = min(start + GROUNDING_BATCH_SIZE, len(claims))
            progress_callback(done, len(claims), f"grounding {done}/{len(claims)} claims")

    # Re-running a check replaces the subject's rows wholesale.
    db.query(FactCheckClaim).filter(
        FactCheckClaim.subject_type == FactCheckSubject.ARTICLE.value,
        FactCheckClaim.subject_id == article.id,
    ).delete()

    now = datetime.now(UTC)
    rows: list[FactCheckClaim] = []
    for i, claim in enumerate(claims):
        label, note = verdicts.get(i, ("ABSENT", "no verdict returned by the judge"))
        verdict = _GROUNDING_VERDICTS.get(label, FactCheckVerdict.UNVERIFIABLE.value)
        note = note.strip()
        row = FactCheckClaim(
            subject_type=FactCheckSubject.ARTICLE.value,
            subject_id=article.id,
            claim_text=claim.text,
            claim_type=claim.claim_type,
            verdict=verdict,
            correction=note if note and note != "-" else None,
            checked_via=FactCheckMethod.GROUNDING.value,
            checked_at=now,
        )
        db.add(row)
        rows.append(row)
    db.flush()

    counts = {value: 0 for value in _GROUNDING_VERDICTS.values()}
    for row in rows:
        counts[row.verdict] += 1
    summary = {
        "total_claims": len(rows),
        "verified": counts[FactCheckVerdict.VERIFIED.value],
        "inaccurate": counts[FactCheckVerdict.INACCURATE.value],
        "unverifiable": counts[FactCheckVerdict.UNVERIFIABLE.value],
        "claims": rows,
    }
    logger.info(
        "Grounded article %d: %d claims (%d verified, %d inaccurate, %d unverifiable)",
        article.id,
        summary["total_claims"],
        summary["verified"],
        summary["inaccurate"],
        summary["unverifiable"],
    )
    return summary


# --- Web-search verification tier (issue #376) ---------------------------------

# Claims per Sonnet+search call: each claim may need its own search, so batches
# stay small enough that the tool's per-call use cap covers every claim.
SEARCH_BATCH_SIZE = 8
# Searches the server tool may run within one call (cost guard). Each search
# result re-processes the growing conversation, so per-call input cost scales
# with uses — the prompt tells the model to cover related claims in one query.
SEARCH_MAX_USES_PER_CALL = 8
# Claims verified per run (cost guard). Research docs can yield far more claims
# than is sane to search in one request; overflow rows persist as PENDING so a
# re-run (or the deep-research tier) can pick them up. Never truncate silently.
MAX_SEARCH_CLAIMS_PER_RUN = 60
# Server-side tool loops stop with ``pause_turn`` at ~10 iterations; resuming
# re-sends the conversation. Cap resumes so a wedged turn can't loop forever.
_MAX_PAUSE_RESUMES = 5

_WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": SEARCH_MAX_USES_PER_CALL,
}

# Search label -> stored verdict. Anything else (including a missing line)
# degrades to NEEDS_DEEP_RESEARCH — the punt, never a pass.
_SEARCH_VERDICTS = {
    "VERIFIED": FactCheckVerdict.VERIFIED.value,
    "INACCURATE": FactCheckVerdict.INACCURATE.value,
    "DISPUTED": FactCheckVerdict.DISPUTED.value,
    "NEEDS_DEEP_RESEARCH": FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
}

_SEARCH_VERDICT_LINE = re.compile(
    r"^VERDICT:\s*(\d+)\s*\|\s*([A-Z_]+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*$"
)

_SEARCH_PROMPT = """\
You are fact-checking claims about Panama's history for an educational \
publication. Use the web_search tool to verify each numbered claim below \
against reliable sources (academic, encyclopedic, governmental, or reputable \
journalistic sources).

Judge each claim as:
- VERIFIED — reliable search results confirm the claim
- INACCURATE — reliable search results contradict the claim; state the correction
- DISPUTED — reputable sources genuinely disagree; summarize the positions
- NEEDS_DEEP_RESEARCH — search results are absent, weak, or from low-quality sources

You have a limited number of searches for this whole batch — fewer than one \
per claim. Group related claims (same person, place, or event) and cover them \
with a single broader query instead of searching each claim separately.

Prefer NEEDS_DEEP_RESEARCH over guessing: many reputable academic historical \
sources are not reachable by web search, and a confident wrong verdict is worse \
than a deferral. NEVER mark a claim VERIFIED or INACCURATE from your own \
knowledge alone — those verdicts require supporting search results, and their \
source URLs are mandatory.

After searching, output your verdicts — exactly one line per claim, in claim \
order, with nothing after the last line:
VERDICT: <claim number> | <VERIFIED or INACCURATE or DISPUTED or \
NEEDS_DEEP_RESEARCH> | <note: the correction for INACCURATE, the positions for \
DISPUTED, a dash otherwise> | <source URLs, comma-separated; a dash for \
NEEDS_DEEP_RESEARCH>

CLAIMS:
{claims}"""


def _all_text(message) -> str:
    """Join every text block's text — search responses interleave text blocks
    with server-tool blocks, and the verdict lines land in the later ones.

    Falls back to ``content[0]`` when no block advertises a type (keeps test
    mocks without a real ``type`` attribute working, mirroring ``first_text``).
    """
    blocks = getattr(message, "content", None) or []
    text_blocks = [b for b in blocks if getattr(b, "type", "text") == "text"]
    if not text_blocks and blocks:
        text_blocks = [blocks[0]]
    return "\n".join(block.text for block in text_blocks)


def _search_call(prompt: str):
    """One Sonnet+web-search request, resuming ``pause_turn`` stops.

    The server-side search loop pauses after ~10 tool iterations; re-sending
    the conversation with the paused assistant turn appended resumes it.
    """
    messages: list[dict] = [{"role": "user", "content": prompt}]
    message = None
    for _ in range(_MAX_PAUSE_RESUMES + 1):
        message = create_message(
            feature="fact_check_web_search",
            model=SONNET_MODEL,
            max_tokens=8000,
            # Effort defaults to high, and Sonnet 5's adaptive-thinking tokens
            # count toward max_tokens/cost; emitting verdict lines from search
            # results doesn't need deep reasoning (cost trim, keeps Sonnet for
            # source-reliability judgment).
            output_config={"effort": "low"},
            tools=[_WEB_SEARCH_TOOL],
            messages=messages,
        )
        if getattr(message, "stop_reason", None) != "pause_turn":
            return message
        messages = messages + [{"role": "assistant", "content": message.content}]
    logger.warning("Web-search fact-check still paused after %d resumes", _MAX_PAUSE_RESUMES)
    return message


def _has_url(citation: str) -> bool:
    return "http://" in citation or "https://" in citation


def verify_claims_via_search(
    claims: list[ExtractedClaim], progress_callback=None
) -> list[tuple[str, str, str]]:
    """Verify claims against the web. Returns one ``(verdict, note, citation)``
    per input claim, in order.

    Conservative by construction: a missing or malformed verdict line, an
    off-vocabulary label, or a VERIFIED/INACCURATE verdict **without a source
    URL** all resolve to NEEDS_DEEP_RESEARCH.
    """
    results: list[tuple[str, str, str]] = [
        (FactCheckVerdict.NEEDS_DEEP_RESEARCH.value, "", "") for _ in claims
    ]
    for start in range(0, len(claims), SEARCH_BATCH_SIZE):
        batch = claims[start : start + SEARCH_BATCH_SIZE]
        numbered = "\n".join(f"{i + 1}. {c.text}" for i, c in enumerate(batch))
        message = _search_call(_SEARCH_PROMPT.format(claims=numbered))
        for line in _all_text(message).splitlines():
            match = _SEARCH_VERDICT_LINE.match(line.strip())
            if not match:
                continue
            number, label, note, citation = match.groups()
            index = int(number) - 1
            if not (0 <= index < len(batch)):
                continue
            verdict = _SEARCH_VERDICTS.get(label, FactCheckVerdict.NEEDS_DEEP_RESEARCH.value)
            if verdict in (
                FactCheckVerdict.VERIFIED.value,
                FactCheckVerdict.INACCURATE.value,
            ) and not _has_url(citation):
                logger.info("Downgrading uncited %s verdict for claim %d", verdict, start + index)
                verdict = FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
            results[start + index] = (verdict, note.strip(), citation.strip())
        if progress_callback:
            done = min(start + SEARCH_BATCH_SIZE, len(claims))
            progress_callback(done, len(claims), f"web search {done}/{len(claims)} claims")
    return results


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


def match_claims_against_kb(claims: list[ExtractedClaim], db: Session) -> dict:
    """Match new claims against the verified-claims KB. Returns
    ``{claim_index: VerifiedClaim}`` for the claims that matched.

    Two stages: an exact normalized-text match (free, deterministic), then a
    Haiku confirmation pass over word-overlap candidates. Conservative by
    construction — Haiku is told to prefer NONE, and a returned KB id is only
    accepted if it was among that specific claim's candidates.
    """
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

    for start in range(0, len(fuzzy), KB_MATCH_BATCH_SIZE):
        batch = fuzzy[start : start + KB_MATCH_BATCH_SIZE]
        kb_union: dict[int, object] = {}
        for _, candidates in batch:
            for row in candidates:
                kb_union[row.id] = row
        numbered = "\n".join(f"{j + 1}. {claims[idx].text}" for j, (idx, _) in enumerate(batch))
        kb_list = "\n".join(f"[{row.id}] {row.claim_text}" for row in kb_union.values())
        message = create_message(
            feature="fact_check_kb_match",
            model=HAIKU_MODEL,
            max_tokens=1500,
            messages=[
                {
                    "role": "user",
                    "content": _KB_MATCH_PROMPT.format(claims=numbered, kb=kb_list),
                }
            ],
        )
        for line in first_text(message).splitlines():
            match = _KB_MATCH_LINE.match(line.strip())
            if not match:
                continue
            number, kb_ref = match.groups()
            j = int(number) - 1
            if kb_ref == "NONE" or not (0 <= j < len(batch)):
                continue
            claim_index, candidates = batch[j]
            row = kb_union.get(int(kb_ref))
            # Guard: the id must be one of THIS claim's own candidates.
            if row is not None and any(row.id == c.id for c in candidates):
                matches[claim_index] = row

    if matches:
        logger.info("KB matched %d of %d claims", len(matches), len(claims))
    return matches


def upsert_verified_claims(rows: list[FactCheckClaim], db: Session) -> int:
    """Feed resolved external verdicts into the KB (latest verification wins).

    Only rows with a reusable verdict reached via WEB_SEARCH or DEEP_RESEARCH
    qualify — KB_MATCH rows are already *from* the KB, and grounding verdicts
    are relative to one research doc, not the historical record. Flushes but
    does not commit. Returns the number of DISTINCT claims upserted.

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


# Sentinel so the per-run cap default is read at CALL time — keeps the module
# constant patchable/tunable rather than frozen into the signature.
_DEFAULT_CAP = object()


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


def _resume_pending_fact_check(
    subject_type: str,
    subject_id: int,
    pending: list[FactCheckClaim],
    db: Session,
    *,
    max_claims,
    progress_callback=None,
) -> dict:
    """Resume mode: verify ONLY the subject's PENDING rows, updating them in
    place — no re-extraction, no delete, and no re-search of already-resolved
    claims. This is how a capped/interrupted run continues without paying for
    the whole subject again.
    """
    claims = [ExtractedClaim(text=row.claim_text, claim_type=row.claim_type) for row in pending]
    kb_matches = match_claims_against_kb(claims, db)
    unmatched = [(i, c) for i, c in enumerate(claims) if i not in kb_matches]

    cap = len(unmatched) if max_claims is None else max_claims
    to_check = unmatched[:cap]
    if len(unmatched) > cap:
        logger.warning(
            "Resumed fact-check for %s %d capped at %d searched claims; %d stay PENDING",
            subject_type,
            subject_id,
            cap,
            len(unmatched) - cap,
        )
    verdicts = verify_claims_via_search(
        [c for _, c in to_check], progress_callback=progress_callback
    )

    now = datetime.now(UTC)
    for i, kb_row in kb_matches.items():
        row = pending[i]
        row.verdict = kb_row.verdict
        row.correction = kb_row.correction
        row.citation = kb_row.citation
        row.checked_via = FactCheckMethod.KB_MATCH.value
        row.checked_at = now
    searched_rows: list[FactCheckClaim] = []
    for (i, _), (verdict, note, citation) in zip(to_check, verdicts, strict=True):
        row = pending[i]
        row.verdict = verdict
        row.correction = note if note and note != "-" else None
        row.citation = citation if citation and citation != "-" else None
        row.checked_via = FactCheckMethod.WEB_SEARCH.value
        row.checked_at = now
        searched_rows.append(row)
    db.flush()

    upsert_verified_claims(searched_rows, db)

    summary = _subject_summary(subject_type, subject_id, db)
    logger.info(
        "Resumed fact-check for %s %d: %d PENDING verified (%d via KB), now %s",
        subject_type,
        subject_id,
        len(kb_matches) + len(searched_rows),
        len(kb_matches),
        summary["counts"],
    )
    return summary


def fact_check_subject(
    subject_type: str,
    subject_id: int,
    text: str,
    db: Session,
    *,
    max_claims=_DEFAULT_CAP,
    progress_callback=None,
) -> dict:
    """Run the web-search tier on a suggestion or research document: extract
    claims from *text*, inherit verdicts for claims already in the verified
    KB (#378), verify the rest via web search (up to *max_claims*; ``None`` =
    unlimited, the background-job mode of #384), and replace the subject's
    ``fact_check_claims`` rows.

    Overflow claims beyond the cap persist as PENDING (logged, never silently
    dropped). Resolved search verdicts are upserted back into the KB. Flushes
    but does not commit — the caller owns the transaction. Returns a summary
    dict: total / per-verdict counts / the persisted rows.

    **Resume mode:** when the subject already has claim rows and some are
    PENDING (a capped or interrupted earlier run), extraction and the delete
    are skipped — only the PENDING rows are verified, in place. A full
    re-extraction happens only once no PENDING rows remain.
    """
    if max_claims is _DEFAULT_CAP:
        max_claims = MAX_SEARCH_CLAIMS_PER_RUN

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
    if pending:
        return _resume_pending_fact_check(
            subject_type,
            subject_id,
            pending,
            db,
            max_claims=max_claims,
            progress_callback=progress_callback,
        )

    feature = f"fact_check_extract_{subject_type.lower()}"
    claims = extract_claims(text, feature=feature)

    kb_matches = match_claims_against_kb(claims, db)
    unmatched = [(i, c) for i, c in enumerate(claims) if i not in kb_matches]

    cap = len(unmatched) if max_claims is None else max_claims
    to_check = unmatched[:cap]
    overflow = unmatched[cap:]
    if overflow:
        logger.warning(
            "Fact-check run for %s %d capped at %d searched claims; %d left PENDING",
            subject_type,
            subject_id,
            cap,
            len(overflow),
        )
    verdicts = verify_claims_via_search(
        [c for _, c in to_check], progress_callback=progress_callback
    )

    # Re-running a check replaces the subject's rows wholesale.
    db.query(FactCheckClaim).filter(
        FactCheckClaim.subject_type == subject_type,
        FactCheckClaim.subject_id == subject_id,
    ).delete()

    now = datetime.now(UTC)
    rows: list[FactCheckClaim] = []
    for i, claim in enumerate(claims):
        kb_row = kb_matches.get(i)
        if kb_row is None:
            continue
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
    searched_rows: list[FactCheckClaim] = []
    for (_, claim), (verdict, note, citation) in zip(to_check, verdicts, strict=True):
        row = FactCheckClaim(
            subject_type=subject_type,
            subject_id=subject_id,
            claim_text=claim.text,
            claim_type=claim.claim_type,
            verdict=verdict,
            correction=note if note and note != "-" else None,
            citation=citation if citation and citation != "-" else None,
            checked_via=FactCheckMethod.WEB_SEARCH.value,
            checked_at=now,
        )
        rows.append(row)
        searched_rows.append(row)
    for _, claim in overflow:
        rows.append(
            FactCheckClaim(
                subject_type=subject_type,
                subject_id=subject_id,
                claim_text=claim.text,
                claim_type=claim.claim_type,
                verdict=FactCheckVerdict.PENDING.value,
            )
        )
    db.add_all(rows)
    db.flush()

    upsert_verified_claims(searched_rows, db)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    summary = {"total_claims": len(rows), "counts": counts, "claims": rows}
    logger.info(
        "Web-search fact-check for %s %d: %d claims %s",
        subject_type,
        subject_id,
        len(rows),
        counts,
    )
    return summary


# --- Extract-only deep-research-first mode (issue #389) --------------------------

# External-tier modes for suggestion/research fact-checks, stored in the job's
# params. Deep-research-first is the default: the web-search tier cost ~$11 per
# research doc and still punted a third of its claims, while the deep-research
# loop is free for the operator (and pic-extension automates it).
MODE_DEEP_RESEARCH = "deep_research"
MODE_WEB_SEARCH = "web_search"


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
    already filters on it, and PENDING carries web-search resume semantics.

    **Half-checked subjects** (rows already exist and some are PENDING — e.g. a
    capped earlier web-search run): no re-extraction and no delete. The PENDING
    rows are KB-matched, and the rest flip to NEEDS_DEEP_RESEARCH in place;
    resolved verdicts are preserved.

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


def _external_tier_check(
    mode: str, subject_type: str, subject_id: int, text: str, db: Session, progress
) -> dict:
    """Dispatch a suggestion/research check to the mode's external tier (#389)."""
    if mode == MODE_WEB_SEARCH:
        return fact_check_subject(
            subject_type, subject_id, text, db, max_claims=None, progress_callback=progress
        )
    if mode == MODE_DEEP_RESEARCH:
        return extract_for_deep_research(
            subject_type, subject_id, text, db, progress_callback=progress
        )
    raise ValueError(f"Unknown fact-check mode: {mode!r}")


def _run_fact_check_for_subject(db: Session, job: CrawlJob, progress) -> dict:
    """Dispatch the job to the right tier and return a uniform stats dict."""
    params = job.params or {}
    subject_type = params.get("subject_type")
    subject_id = params.get("subject_id")
    # Jobs created before #389 carry no mode; deep-research-first is the default.
    mode = params.get("mode") or MODE_DEEP_RESEARCH

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
            mode, subject_type, subject_id, build_suggestion_text(suggestion), db, progress
        )
    elif subject_type == FactCheckSubject.RESEARCH.value:
        research = db.get(Research, subject_id)
        if research is None or not (research.content or "").strip():
            raise ValueError(f"Research {subject_id} not found or has no content")
        summary = _external_tier_check(
            mode, subject_type, subject_id, research.content, db, progress
        )
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
    """
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


def generate_deep_research_prompt(
    db: Session,
    subject_type: str | None = None,
    subject_id: int | None = None,
) -> dict | None:
    """Compile pending NEEDS_DEEP_RESEARCH claims into a verification prompt.

    Takes the oldest :data:`DEEP_RESEARCH_BATCH_LIMIT` claims (ordered like
    the skill: subject_type, subject_id, id), optionally scoped to one
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
        .limit(DEEP_RESEARCH_BATCH_LIMIT)
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
    rejected_reason}`` — ``rejected_reason`` is None normally, or a message
    when the whole document was rejected as a failed run (see below) and
    nothing was written.
    """
    parsed: dict[int, tuple[str, str, str]] = {}
    for line in content.splitlines():
        match = _SEARCH_VERDICT_LINE.match(line.strip())
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
            skipped.append((cid, f"claim is no longer pending (verdict={row.verdict})"))
            continue

        row.verdict = _DEEP_VERDICTS[label]
        row.correction = note if note and note != "-" else None
        row.citation = citation if citation and citation != "-" else None
        row.checked_via = FactCheckMethod.DEEP_RESEARCH.value
        row.checked_at = now
        written_rows.append(row)

    db.flush()
    kb_upserts = upsert_verified_claims(written_rows, db)

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
        "rejected_reason": None,
    }
