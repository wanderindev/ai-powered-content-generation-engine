"""Apply fact-check corrections to the content itself (issue #387).

Closes the loop after the verification tiers (epic #373): INACCURATE claims
already carry the correction text (deep-research corrections for suggestions and
research, grounding notes — "what the research says" — for articles). This
module turns those corrections into **targeted FIND/REPLACE edits** and applies
them to the subject's content, flipping applied claims to ``CORRECTED``.

Design (decisions locked with the operator, see #387):

* **Edits, not rewrites** — one Sonnet call per language returns plain-text
  edit blocks (claim id + exact FIND snippet + REPLACE snippet). Code applies
  an edit only when the FIND snippet occurs exactly once in the content, so
  the model can never rewrite unrelated prose.
* **Preview then apply** — ``build_preview`` generates and validates the edits
  statelessly; ``apply_corrections`` re-validates the previewed payload and
  writes. What you preview is what applies.
* **Per-claim atomicity** — a claim flips to CORRECTED only when *all* of its
  edits (both languages, for articles with Spanish content) apply; a claim
  with any failing edit is left completely untouched, so content is never
  half-corrected while still flagged INACCURATE.
* **Skips resolve too** — when the model reports the content never asserts a
  claim's inaccuracy (``EDIT_SKIPPED``), the operator sees that reason in the
  preview and applying echoes the skip back: the claim flips to CORRECTED
  without touching the content ("never asserted it" is the degenerate case of
  "no longer asserts it").
* **The KB stays INACCURATE** — the claim is still wrong in the world;
  CORRECTED describes the content. ``upsert_verified_claims`` is never called
  here and skips CORRECTED rows by construction.
"""

import copy
import logging
import re
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.enums import MaterialType
from app.models.fact_check import FactCheckClaim, FactCheckSubject, FactCheckVerdict
from app.services import suggestion_dedup
from app.services.edu_material_generation import blocks_to_markdown, slides_preview_text
from app.services.llm import SONNET_MODEL, create_message, first_text
from app.utils.guide_blocks import apply_to_blocks, blocks_prose, count_in_blocks
from app.utils.slide_text import (
    apply_to_segments,
    count_in_segments,
    slide_segments,
    slides_prose,
    write_segments,
)

logger = logging.getLogger(__name__)

# Statuses a previewed edit can carry. OK edits rewrite content on apply;
# SKIPPED entries resolve their claim without a content change.
EDIT_OK = "ok"
EDIT_NOT_FOUND = "not_found"  # FIND does not occur in the content
EDIT_AMBIGUOUS = "ambiguous"  # FIND occurs more than once
EDIT_SKIPPED = "skipped"  # the model said the inaccuracy is absent
EDIT_MISSING = "missing"  # the model returned neither an edit nor a skip

# ``ArticleSuggestion.title`` is String(255); a replacement overflowing it
# would blow up at flush time, so it fails the edit instead.
_SUGGESTION_TITLE_MAX = 255


@dataclass(frozen=True)
class CorrectionEdit:
    claim_id: int
    language: str  # "en" | "es"
    find: str
    replace: str
    status: str = EDIT_OK
    reason: str | None = None


# --- Sonnet edit generation ---------------------------------------------------

_CORRECTION_PROMPT = """\
You are applying fact-check corrections to content for a Panama history \
publication. For each numbered inaccurate claim below you are given the \
correction — the fact as it should be stated. Produce the smallest targeted \
edit(s) to the content that fix each inaccuracy.
{language_note}
Rules:
- FIND must be copied VERBATIM from the content — an exact, contiguous \
substring. Make it long enough to occur exactly once (include surrounding \
words when the phrase repeats).
- Change as little text as possible; never touch sentences that are already \
correct.
- REPLACE must read naturally in place of FIND.
- If the content does not actually assert a claim's inaccuracy, output \
SKIP: <claim id> | <short reason> instead of an edit for it.
- Output one EDIT block (or SKIP line) per claim, and nothing else.

Output format:
EDIT: <claim id>
FIND:
<exact snippet copied from the content>
REPLACE:
<corrected snippet>
END EDIT

INACCURATE CLAIMS (id | claim | correction):
{claims}

CONTENT:
{content}"""

# Deliberately says "is in SPANISH" rather than "is the Spanish translation":
# a slide deck is authored natively in Spanish (#320), so telling the model to
# look for a translation of an English original would be a false premise.
# Semantics are unchanged for the translated article/guide path.
_ES_LANGUAGE_NOTE = """
The content below is in SPANISH; the claims and corrections are stated in \
English. Find where the Spanish text asserts each inaccuracy and write the \
REPLACE snippet in Spanish, matching the surrounding style.
"""

# Appended for a slide deck (#464): part of this text is a script read aloud in
# a cloned voice, so an edit that is correct but unspeakable — an abbreviation,
# a parenthetical, a bare numeral — degrades the narration audio.
_SLIDES_LANGUAGE_NOTE = """
Some of this text is a narration script spoken aloud by a narrator. Keep every \
REPLACE snippet natural to say out loud: spell words out rather than \
abbreviating, and avoid parentheses and symbols.
"""


def _language_note(language: str, *, spoken: bool = False) -> str:
    """The prompt's language/context preamble for one content target.

    *spoken* is set for a slide deck, whose narration is read aloud.
    """
    note = _ES_LANGUAGE_NOTE if language == "es" else ""
    if spoken:
        note += _SLIDES_LANGUAGE_NOTE
    return note


def is_spoken_subject(subject_type: str, subject) -> bool:
    """True when the subject's text includes a narration script (#464).

    Keyed on ``material_type``, not ``subject_type`` — EDU_MATERIAL covers both
    guides and decks, and only the deck is spoken.
    """
    return (
        subject_type == FactCheckSubject.EDU_MATERIAL.value
        and getattr(subject, "material_type", None) == MaterialType.SLIDES.value
    )


_EDIT_START = re.compile(r"^EDIT:\s*(\d+)\s*$")
_SKIP_LINE = re.compile(r"^SKIP:\s*(\d+)\s*\|\s*(.*?)\s*$")
_BARE_MARKERS = ("FIND:", "REPLACE:", "END EDIT")


def _starts_new_block(stripped: str) -> bool:
    """True when a line opens a new edit/skip block — an implicit terminator
    for whatever block is still being collected (#433)."""
    return bool(_EDIT_START.match(stripped) or _SKIP_LINE.match(stripped))


def _contains_protocol_marker(text: str) -> bool:
    """True when *text* carries a raw edit-protocol line. Such lines can never
    legitimately appear in article/research prose, so a FIND/REPLACE holding
    one is parser fallout (a swallowed block), not content (#433)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in _BARE_MARKERS or _starts_new_block(stripped):
            return True
    return False


def parse_edit_blocks(raw: str) -> tuple[list[tuple[int, str, str]], dict[int, str]]:
    """Parse Sonnet's edit output. Returns ``(edits, skips)`` where *edits* is
    a list of ``(claim_id, find, replace)`` and *skips* maps claim id to the
    model's reason. Malformed blocks are dropped (the claim surfaces as
    ``missing`` downstream) — never guessed at.

    An ``EDIT:``/``SKIP:`` line met while collecting FIND or REPLACE lines is
    an *implicit* terminator: without it, a block missing its ``END EDIT``
    swallowed the whole next block into its REPLACE and spliced raw protocol
    text into the content (#433). The terminator line is not consumed — the
    outer loop re-reads it as the start of its own block. A block terminated
    while still collecting FIND has no REPLACE at all, so it is dropped; one
    terminated during REPLACE is complete but for its decoration, so it is
    kept. Any FIND/REPLACE still holding a protocol marker is dropped.
    """
    edits: list[tuple[int, str, str]] = []
    skips: dict[int, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        skip_match = _SKIP_LINE.match(line)
        if skip_match:
            skips[int(skip_match.group(1))] = skip_match.group(2)
            i += 1
            continue
        start = _EDIT_START.match(line)
        if not start:
            i += 1
            continue
        claim_id = int(start.group(1))
        i += 1
        if i >= len(lines) or lines[i].strip() != "FIND:":
            continue
        i += 1
        find_lines: list[str] = []
        cut_short = False
        while i < len(lines) and lines[i].strip() != "REPLACE:":
            if _starts_new_block(lines[i].strip()):
                cut_short = True
                break
            find_lines.append(lines[i])
            i += 1
        if cut_short:
            continue  # no REPLACE for this block — drop it, re-read the terminator
        if i >= len(lines):
            break
        i += 1  # past REPLACE:
        replace_lines: list[str] = []
        implicit_end = False
        while i < len(lines) and lines[i].strip() != "END EDIT":
            if _starts_new_block(lines[i].strip()):
                implicit_end = True
                break
            replace_lines.append(lines[i])
            i += 1
        if not implicit_end:
            if i >= len(lines):
                break
            i += 1  # past END EDIT
        find = "\n".join(find_lines).strip()
        replace = "\n".join(replace_lines).strip()
        if (
            find
            and find != replace
            and not _contains_protocol_marker(find)
            and not _contains_protocol_marker(replace)
        ):
            edits.append((claim_id, find, replace))
    return edits, skips


# One response must hold an edit block for every claim asked about, and a
# research doc can carry dozens of INACCURATE claims — 19 in one call
# overflowed max_tokens=4000 in prod (TruncationError, 2026-07-13). Edits are
# independent FIND/REPLACE blocks, so chunking the claim list across calls
# loses no cross-claim context. Chunking by claim *count* alone doesn't bound
# the *output* size, though: a research doc's FIND/REPLACE snippets are long,
# so even an 8-claim chunk overflowed max_tokens=8000 in prod (RESEARCH/34,
# 2026-07-15). So we do two things — give normal chunks generous headroom
# (24000, well under Sonnet's 128k output ceiling; also >= anthropic_stream_min
# _tokens → streamed), and bisect a chunk that still truncates until it fits.
_CLAIMS_PER_CALL = 8
_EDIT_MAX_TOKENS = 24000


def _edits_for_chunk(
    chunk: list[FactCheckClaim], content: str, *, language: str, spoken: bool = False
) -> tuple[list[tuple[int, str, str]], dict[int, str]]:
    """One Sonnet call for a chunk of claims, bisecting on truncation.

    Edit blocks are independent, so a chunk whose combined output overflows
    ``max_tokens`` can be split in half and each half retried with no loss of
    context. A single claim that overflows even alone is dropped (returned as
    no edit) rather than 500-ing the whole preview — the UI then shows it as
    not-correctable, same as a claim the model returns nothing for.
    """
    numbered = "\n".join(f"{c.id} | {c.claim_text} | {c.correction}" for c in chunk)
    message = create_message(
        feature="fact_check_corrections",
        model=SONNET_MODEL,
        max_tokens=_EDIT_MAX_TOKENS,
        reject_on_truncation=False,
        messages=[
            {
                "role": "user",
                "content": _CORRECTION_PROMPT.format(
                    language_note=_language_note(language, spoken=spoken),
                    claims=numbered,
                    content=content,
                ),
            }
        ],
    )
    if getattr(message, "stop_reason", None) == "max_tokens":
        if len(chunk) > 1:
            mid = len(chunk) // 2
            left = _edits_for_chunk(chunk[:mid], content, language=language, spoken=spoken)
            right = _edits_for_chunk(chunk[mid:], content, language=language, spoken=spoken)
            return left[0] + right[0], {**left[1], **right[1]}
        logger.warning(
            "correction edit for claim %s truncated at max_tokens=%d even alone; "
            "skipping (will surface as not-correctable)",
            chunk[0].id,
            _EDIT_MAX_TOKENS,
        )
        return [], {}
    return parse_edit_blocks(first_text(message))


def _generate_edits(
    claims: list[FactCheckClaim], content: str, *, language: str, spoken: bool = False
) -> tuple[list[tuple[int, str, str]], dict[int, str]]:
    """Sonnet calls, one per chunk of claims: content + claim/correction list
    -> parsed edit blocks, merged across chunks."""
    edits: list[tuple[int, str, str]] = []
    skips: dict[int, str] = {}
    for start in range(0, len(claims), _CLAIMS_PER_CALL):
        chunk = claims[start : start + _CLAIMS_PER_CALL]
        chunk_edits, chunk_skips = _edits_for_chunk(
            chunk, content, language=language, spoken=spoken
        )
        edits.extend(chunk_edits)
        skips.update(chunk_skips)
    return edits, skips


# --- Subject content access ----------------------------------------------------


def _suggestion_fields(suggestion: ArticleSuggestion) -> list[tuple[str, str]]:
    """The structured fields a suggestion's FIND snippets are matched across."""
    fields = [("title", suggestion.title or "")]
    if suggestion.main_topic:
        fields.append(("main_topic", suggestion.main_topic))
    for idx, topic in enumerate(suggestion.sub_topics or []):
        fields.append((f"sub_topics[{idx}]", topic))
    if suggestion.point_of_view:
        fields.append(("point_of_view", suggestion.point_of_view))
    return fields


def _content_targets(subject_type: str, subject) -> list[tuple[str, str]]:
    """``(language, text)`` targets edits are generated against. The suggestion
    text is the plain field values joined by blank lines — no labels, so a
    verbatim FIND always matches inside a single real field value."""
    if subject_type == FactCheckSubject.ARTICLE.value:
        targets = [("en", subject.content or "")]
        if (subject.content_es or "").strip():
            targets.append(("es", subject.content_es))
        return targets
    # Blog and edu research are both a single English markdown ``content``
    # column, so they share one target shape (#460).
    if subject_type in (
        FactCheckSubject.RESEARCH.value,
        FactCheckSubject.EDU_RESEARCH.value,
    ):
        return [("en", subject.content or "")]
    # Neither edu material type keeps prose on ``content`` — that is a derived
    # projection in both cases, so target the real source and a verbatim FIND
    # matches something actually stored.
    if subject_type == FactCheckSubject.EDU_MATERIAL.value:
        # A deck is Spanish-native (#320, #464): its text lives on edu_slides
        # rows and there is no English twin to correct alongside it.
        if subject.material_type == MaterialType.SLIDES.value:
            return [("es", slides_prose(subject.slides))]
        # A guide's prose lives in ``content_blocks`` (#461, #340).
        targets = [("en", blocks_prose(subject.content_blocks))]
        if subject.content_blocks_es:
            targets.append(("es", blocks_prose(subject.content_blocks_es)))
        return targets
    return [("en", "\n\n".join(text for _, text in _suggestion_fields(subject)))]


def correctable_claims(subject_type: str, subject_id: int, db: Session) -> list[FactCheckClaim]:
    """Only INACCURATE claims with a non-null correction are correctable —
    DISPUTED is excluded (no single right answer to apply)."""
    return (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == subject_type,
            FactCheckClaim.subject_id == subject_id,
            FactCheckClaim.verdict == FactCheckVerdict.INACCURATE.value,
            FactCheckClaim.correction.isnot(None),
        )
        .order_by(FactCheckClaim.id)
        .all()
    )


def _occurrences(content: str, find: str) -> int:
    return content.count(find)


# --- Preview -------------------------------------------------------------------


def build_preview(subject_type: str, subject, claims: list[FactCheckClaim]) -> list[CorrectionEdit]:
    """Generate and validate correction edits for every content target.

    Returns one entry per (claim, language) — an OK edit, a failed edit
    (not_found / ambiguous), or a placeholder (skipped / missing) so the UI
    can show why a claim is not correctable. Stateless: nothing is persisted.
    """
    results: list[CorrectionEdit] = []
    spoken = is_spoken_subject(subject_type, subject)
    for language, content in _content_targets(subject_type, subject):
        raw_edits, skips = _generate_edits(claims, content, language=language, spoken=spoken)
        by_claim: dict[int, list[tuple[str, str]]] = {}
        for claim_id, find, replace in raw_edits:
            by_claim.setdefault(claim_id, []).append((find, replace))
        for claim in claims:
            if claim.id in by_claim:
                for find, replace in by_claim[claim.id]:
                    count = _occurrences(content, find)
                    if count == 1:
                        status, reason = EDIT_OK, None
                    elif count == 0:
                        status, reason = EDIT_NOT_FOUND, "FIND snippet not present in the content"
                    else:
                        status, reason = (
                            EDIT_AMBIGUOUS,
                            f"FIND snippet occurs {count} times in the content",
                        )
                    results.append(
                        CorrectionEdit(claim.id, language, find, replace, status, reason)
                    )
            elif claim.id in skips:
                results.append(
                    CorrectionEdit(claim.id, language, "", "", EDIT_SKIPPED, skips[claim.id])
                )
            else:
                results.append(
                    CorrectionEdit(
                        claim.id, language, "", "", EDIT_MISSING, "no edit returned by the model"
                    )
                )
    logger.info(
        "Correction preview for %s %d: %d claims, %d edits ok of %d entries",
        subject_type,
        subject.id,
        len(claims),
        sum(1 for r in results if r.status == EDIT_OK),
        len(results),
    )
    return results


# --- Apply ---------------------------------------------------------------------


def _apply_to_suggestion(suggestion, find: str, replace: str) -> str | None:
    """Apply one edit across the suggestion's structured fields (works on the
    real row or a ``SimpleNamespace`` scratch copy). Returns an error reason,
    or ``None`` on success. The FIND must occur exactly once across ALL fields
    combined."""
    fields = _suggestion_fields(suggestion)
    hits = [(key, text) for key, text in fields if find in text]
    total = sum(_occurrences(text, find) for _, text in fields)
    if total == 0:
        return "FIND snippet no longer matches the content"
    if total > 1:
        return f"FIND snippet occurs {total} times in the content"
    key, text = hits[0]
    new_text = text.replace(find, replace)
    if key == "title" and len(new_text) > _SUGGESTION_TITLE_MAX:
        return f"corrected title exceeds {_SUGGESTION_TITLE_MAX} characters"
    if key == "title":
        suggestion.title = new_text
    elif key == "main_topic":
        suggestion.main_topic = new_text
    elif key == "point_of_view":
        suggestion.point_of_view = new_text
    else:  # sub_topics[idx] — reassign the list so SQLAlchemy sees the change
        idx = int(key[len("sub_topics[") : -1])
        topics = list(suggestion.sub_topics or [])
        topics[idx] = new_text
        suggestion.sub_topics = topics
    return None


def apply_corrections(subject_type: str, subject, edits: list[CorrectionEdit], db: Session) -> dict:
    """Re-validate and apply previewed edits, flipping fully-corrected claims
    to CORRECTED.

    Deterministic: no LLM call — only the previewed payload is applied, and
    each FIND must still occur exactly once at its turn (edits apply in claim
    order, so earlier edits can't silently invalidate later ones unnoticed).
    Claims are atomic: if any of a claim's edits fails, none of them is
    applied and the claim stays INACCURATE. An ``EDIT_SKIPPED`` entry (the
    model said the content never asserts the inaccuracy — reviewed by the
    operator in the preview) satisfies its language without touching the
    content; a claim whose languages are all skipped flips to CORRECTED as
    "already correct". Flushes but does not commit.

    Returns ``{corrected_claim_ids, already_correct_claim_ids,
    failed: [(claim_id, language, reason)]}``.
    """
    claims = {c.id: c for c in correctable_claims(subject_type, subject.id, db)}
    required_languages = {lang for lang, _ in _content_targets(subject_type, subject)}

    by_claim: dict[int, list[CorrectionEdit]] = {}
    skipped_languages: dict[int, set[str]] = {}
    failed: list[tuple[int, str, str]] = []
    for edit in edits:
        if edit.claim_id not in claims:
            failed.append((edit.claim_id, edit.language, "claim is not correctable"))
            continue
        if edit.language not in required_languages:
            failed.append((edit.claim_id, edit.language, "language not applicable to this subject"))
            continue
        if edit.status == EDIT_SKIPPED:
            skipped_languages.setdefault(edit.claim_id, set()).add(edit.language)
            continue
        if not edit.find or edit.find == edit.replace:
            failed.append((edit.claim_id, edit.language, "empty or no-op edit"))
            continue
        if _contains_protocol_marker(edit.find) or _contains_protocol_marker(edit.replace):
            # Apply is the write path and re-validates a client round-tripped
            # payload; a marker here means a swallowed block (#433).
            failed.append((edit.claim_id, edit.language, "edit contains raw edit-protocol markers"))
            continue
        by_claim.setdefault(edit.claim_id, []).append(edit)

    corrected_ids: list[int] = []
    already_correct_ids: list[int] = []
    for claim_id in sorted(set(by_claim) | set(skipped_languages)):
        claim_edits = by_claim.get(claim_id, [])
        languages = {e.language for e in claim_edits} | skipped_languages.get(claim_id, set())
        if languages != required_languages:
            missing = ", ".join(sorted(required_languages - languages))
            failed.append((claim_id, missing, "no edit provided for this language"))
            continue
        if not claim_edits:
            # Every language was skipped — the content already matches the
            # correction, so resolve the claim without touching anything.
            already_correct_ids.append(claim_id)
            continue

        # Simulate the claim's edits sequentially on a scratch copy — each
        # FIND must occur exactly once at its turn — then commit the result
        # only when every edit applied (atomic per claim).
        reasons: list[tuple[int, str, str]] = []
        if subject_type == FactCheckSubject.SUGGESTION.value:
            scratch = SimpleNamespace(
                title=subject.title,
                main_topic=subject.main_topic,
                sub_topics=subject.sub_topics,
                point_of_view=subject.point_of_view,
            )
            for edit in claim_edits:
                error = _apply_to_suggestion(scratch, edit.find, edit.replace)
                if error:
                    reasons.append((claim_id, edit.language, error))
                    break
            if not reasons:
                if scratch.title != subject.title or scratch.main_topic != subject.main_topic:
                    # The dedup vector and duplicate flag (#630) describe title + main topic: clear both.
                    suggestion_dedup.clear_embedding(db, subject.id)
                subject.title = scratch.title
                subject.main_topic = scratch.main_topic
                subject.sub_topics = scratch.sub_topics
                subject.point_of_view = scratch.point_of_view
        elif is_spoken_subject(subject_type, subject):
            # Edit the slide rows (what the renderer draws and the TTS speaks),
            # then re-derive the preview. Writing to ``content`` instead would
            # never reach the audio or the video.
            slides = list(subject.slides)
            segments = slide_segments(slides)
            for edit in claim_edits:
                total = count_in_segments(segments, edit.find)
                if total != 1:
                    reasons.append(
                        (
                            claim_id,
                            edit.language,
                            "FIND snippet no longer matches the content"
                            if total == 0
                            else f"FIND snippet occurs {total} times in the content",
                        )
                    )
                    break
                segments = apply_to_segments(segments, edit.find, edit.replace)
            if not reasons:
                write_segments(slides, segments)
                subject.content = slides_preview_text(
                    [{"title": s.title, "body": s.body, "narration": s.narration} for s in slides]
                )
        elif subject_type == FactCheckSubject.EDU_MATERIAL.value:
            # Edit the blocks (the PDF's source of truth), then re-derive the
            # markdown projection. Writing to ``content`` instead would never
            # reach the PDF and would be clobbered on the next regeneration.
            scratch_blocks = {"en": copy.deepcopy(subject.content_blocks or [])}
            if "es" in required_languages:
                scratch_blocks["es"] = copy.deepcopy(subject.content_blocks_es or [])
            for edit in claim_edits:
                blocks = scratch_blocks.get(edit.language)
                total = count_in_blocks(blocks, edit.find)
                if total != 1:
                    reasons.append(
                        (
                            claim_id,
                            edit.language,
                            "FIND snippet no longer matches the content"
                            if total == 0
                            else f"FIND snippet occurs {total} times in the content",
                        )
                    )
                    break
                scratch_blocks[edit.language] = apply_to_blocks(blocks, edit.find, edit.replace)
            if not reasons:
                subject.content_blocks = scratch_blocks["en"]
                subject.content = blocks_to_markdown(scratch_blocks["en"])
                if "es" in scratch_blocks:
                    subject.content_blocks_es = scratch_blocks["es"]
                    subject.content_es = blocks_to_markdown(scratch_blocks["es"])
        else:
            scratch_texts = {"en": subject.content or ""}
            if "es" in required_languages:
                scratch_texts["es"] = subject.content_es or ""
            for edit in claim_edits:
                total = _occurrences(scratch_texts.get(edit.language, ""), edit.find)
                if total != 1:
                    reasons.append(
                        (
                            claim_id,
                            edit.language,
                            "FIND snippet no longer matches the content"
                            if total == 0
                            else f"FIND snippet occurs {total} times in the content",
                        )
                    )
                    break
                scratch_texts[edit.language] = scratch_texts[edit.language].replace(
                    edit.find, edit.replace
                )
            if not reasons:
                subject.content = scratch_texts["en"]
                if "es" in scratch_texts:
                    subject.content_es = scratch_texts["es"]
        if reasons:
            failed.extend(reasons)
            continue
        corrected_ids.append(claim_id)

    for claim_id in corrected_ids + already_correct_ids:
        claims[claim_id].verdict = FactCheckVerdict.CORRECTED.value
    db.flush()

    logger.info(
        "Applied corrections to %s %d: %d claims corrected, %d already correct, %d edits failed",
        subject_type,
        subject.id,
        len(corrected_ids),
        len(already_correct_ids),
        len(failed),
    )
    return {
        "corrected_claim_ids": corrected_ids,
        "already_correct_claim_ids": already_correct_ids,
        "failed": failed,
    }


def load_correction_subject(subject_type: str, subject_id: int, db: Session):
    """Load the content-bearing row for a subject, or ``None``."""
    from app.models.edu_material import EduMaterial
    from app.models.edu_research import EduResearch
    from app.models.research import Research

    model = {
        FactCheckSubject.ARTICLE.value: Article,
        FactCheckSubject.RESEARCH.value: Research,
        FactCheckSubject.SUGGESTION.value: ArticleSuggestion,
        FactCheckSubject.EDU_RESEARCH.value: EduResearch,
        FactCheckSubject.EDU_MATERIAL.value: EduMaterial,
    }[subject_type]
    return db.get(model, subject_id)
