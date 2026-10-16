"""Educational material generation service — generates study guides from approved research."""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.excursions import focus_for, get_excursion
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.models.enums import GuideBlockType, SlideTemplate
from app.prompts.edu_material_generation import (
    REVIEW_SECTION_TITLES,
    SLIDES_CONDENSED_TARGET,
    _review_ladder_for_band,
    build_edu_blocks_prompt,
    build_edu_condense_prompt,
    build_edu_narration_prompt,
    build_edu_outline_prompt,
    build_edu_slides_prompt,
    build_edu_slides_subset_prompt,
)
from app.schemas.edu import MAX_GRADE_BANDS
from app.services.llm import OPUS_MODEL, create_message, first_text, parse_llm_json

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

# Research shorter than this goes directly to outline (no condensation needed)
CONDENSE_THRESHOLD = 2500

# Every edu call (guide condense/outline/content, deck condense/slides, narration)
# runs on Opus 5.5 — the operator's call on 2026-10-04, as the blog pipeline did.
# Each site passes effort "high" explicitly, which matters: Opus 5.5 defaults to
# "medium". The caps below were measured on Sonnet; Opus tends to think more per
# turn, so a TruncationError here is the first thing to check against the logs.
#
# That happened on the first Opus runs (jobs 264/265, Panama History Museum
# 9th-12th, 2026-10-04/05): edu_outline hit its 12,000 cap both times and
# edu_condense used 19,380-19,439 of 20,000. Every cap was then raised to
# roughly 2x its Sonnet value. They are ceilings, not spend, and every call
# streams, where the 600s SDK timeout is per read, not per call.
EDU_MODEL = OPUS_MODEL

# Condensation runs with adaptive thinking (#338): deciding what to keep
# is a judgment task worth a reasoning model, and at a handful of guides total the
# cost is negligible. max_tokens must cover the thinking spend PLUS the briefing
# output (the largest band target ~4500 words ≈ ~9k tokens, measured on Sonnet 5),
# with headroom so the create_message truncation guard never trips on a long briefing.
CONDENSE_MAX_TOKENS = 40_000

# Full-deck slide content (the 16-22 slide JSON array of build_edu_slides_prompt).
# Adaptive thinking counts toward max_tokens (measured on Sonnet 5), and planning a deck arc
# over a ~3500-4000-word briefing at effort "high" is an expensive think: a
# 7th-8th band deck tripped the create_message truncation guard at 12k, having
# spent most of the cap before it finished emitting the array (~4-5k tokens of
# Spanish JSON for 22 slides). 20k leaves the thinking room and keeps the call
# above anthropic_stream_min_tokens, so it streams on the 600s timeout.
SLIDES_MAX_TOKENS = 40_000


def _parse_outline_response(text: str) -> tuple[str, str]:
    """Parse the TITLE: / --- / outline plain-text format from the LLM.

    Returns (title, outline). Falls back gracefully if format is unexpected.
    """
    text = text.strip()

    # Look for "TITLE:" prefix on the first non-empty line
    lines = text.split("\n")
    title = ""
    outline_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("TITLE:"):
            title = stripped[len("TITLE:") :].strip()
            outline_start = i + 1
            break

    # Skip the --- separator line if present
    remaining = lines[outline_start:]
    for i, line in enumerate(remaining):
        stripped = line.strip()
        if stripped and not stripped.startswith("---"):
            outline = "\n".join(remaining[i:]).strip()
            return title, outline

    # If we only found the title line with no outline after it
    if title:
        return title, ""

    # Couldn't parse — treat entire response as outline
    return "", text


def _condense_research(
    research_content: str,
    suggestion: EduSuggestion,
    grade_band: str,
    band_focus: str | None = None,
    target_words: int | None = None,
    feature: str = "edu_condense",
) -> str:
    """Condense a long research doc into a per-band briefing (``EDU_MODEL`` + thinking).

    ``target_words``/``feature`` let the slides path reuse this with its own
    (larger) briefing target and its own telemetry label.
    """
    word_count = len(research_content.split())
    if word_count <= CONDENSE_THRESHOLD:
        logger.info(
            "Research is %d words (under %d), skipping condensation",
            word_count,
            CONDENSE_THRESHOLD,
        )
        return research_content

    logger.info(
        "Condensing research from %d words for band=%s",
        word_count,
        grade_band,
    )

    prompt = build_edu_condense_prompt(
        research_content=research_content,
        suggestion_title=suggestion.title,
        sub_topics=suggestion.sub_topics,
        grade_band=grade_band,
        excursion_key=suggestion.excursion_key,
        band_focus=band_focus,
        target_words=target_words,
    )

    msg = create_message(
        feature=feature,
        model=EDU_MODEL,
        max_tokens=CONDENSE_MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )
    # With thinking the first content block is the thinking block; take the
    # first *text* block.
    condensed = first_text(msg).strip()
    condensed_words = len(condensed.split())
    logger.info("Condensed research to %d words", condensed_words)
    return condensed


GUIDE_BLOCK_VALUES = set(GuideBlockType.values())


def _as_str(val) -> str | None:
    s = str(val).strip() if val is not None else ""
    return s or None


# Guides must never carry URLs or editor link-placeholders (#5 of the study-guide
# redesign). The prompts forbid them; this strip is the defensive backstop.
_LINK_RE = re.compile(r"https?://\S+|www\.\S+|\[LINK NEEDED[^\]]*\]", re.IGNORECASE)


def _strip_links(text: str | None) -> str | None:
    if not text:
        return text
    cleaned, n = _LINK_RE.subn("", text)
    if n:
        logger.warning("Stripped %d link artifact(s) from guide block text", n)
        cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned or None


def _txt(val) -> str | None:
    """Human-text field: coerce/trim like _as_str, then strip link artifacts."""
    return _strip_links(_as_str(val))


def _txt_list(val) -> list[str]:
    if not isinstance(val, list):
        return []
    return [s for s in (_txt(v) for v in val) if s]


def _media_fields(raw: dict) -> dict:
    """Human-picked media selection for image-bearing blocks (#6): passed through
    untouched so an edit round-trip preserves it. Generation never sets these —
    an editor picks the photo in the admin UI."""
    out: dict = {}
    if isinstance(raw.get("media_id"), int):
        out["media_id"] = raw["media_id"]
    media_path = _as_str(raw.get("media_path"))
    if media_path:
        out["media_path"] = media_path
    return out


# Photo frame shapes the guide renderer supports (guide_pdf._FIGURE_ASPECTS).
# "landscape" is the renderer default, so it is stored implicitly (absent key).
_PHOTO_ASPECTS = frozenset({"wide", "portrait"})


def _aspect_field(raw: dict) -> dict:
    """Editor-chosen photo frame shape for image/map blocks; unknown values and
    the default ("landscape") normalize to the absent key."""
    aspect = _as_str(raw.get("aspect"))
    return {"aspect": aspect} if aspect in _PHOTO_ASPECTS else {}


def _normalize_block(raw: dict) -> dict | None:
    """Validate/normalize one LLM guide block (#340). Returns None to drop an
    unknown type or an empty block (no usable content)."""
    block_type = str(raw.get("type", "")).strip().lower()
    if block_type not in GUIDE_BLOCK_VALUES:
        return None

    if block_type == GuideBlockType.COVER.value:
        title = _txt(raw.get("title"))
        if not title:
            return None
        return {
            "type": block_type,
            "title": title,
            "subtitle": _txt(raw.get("subtitle")),
            **_media_fields(raw),
        }
    if block_type == GuideBlockType.SECTION.value:
        title = _txt(raw.get("title"))
        return {"type": block_type, "title": title} if title else None
    if block_type == GuideBlockType.PARAGRAPH.value:
        text = _txt(raw.get("text"))
        return {"type": block_type, "text": text} if text else None
    if block_type in (GuideBlockType.BULLETS.value, GuideBlockType.ORDERED_LIST.value):
        items = _txt_list(raw.get("items"))
        return {"type": block_type, "items": items} if items else None
    if block_type == GuideBlockType.QUOTE.value:
        text = _txt(raw.get("text"))
        return (
            {"type": block_type, "text": text, "attribution": _txt(raw.get("attribution"))}
            if text
            else None
        )
    if block_type == GuideBlockType.TIMELINE.value:
        events = []
        for e in raw.get("events") or []:
            if not isinstance(e, dict):
                continue
            label, text = _txt(e.get("label")), _txt(e.get("text"))
            if label or text:
                events.append({"label": label, "text": text})
        return {"type": block_type, "events": events} if events else None
    if block_type == GuideBlockType.CALLOUT.value:
        text = _txt(raw.get("text"))
        terms = []
        for t in raw.get("terms") or []:
            if not isinstance(t, dict):
                continue
            term, definition = _txt(t.get("term")), _txt(t.get("definition"))
            if term or definition:
                terms.append({"term": term, "definition": definition})
        if not (text or terms):
            return None
        return {"type": block_type, "label": _txt(raw.get("label")), "text": text, "terms": terms}
    if block_type == GuideBlockType.MC_QUESTION.value:
        question = _txt(raw.get("question"))
        choices = _txt_list(raw.get("choices"))
        # A question with fewer than 2 choices isn't multiple-choice.
        if not question or len(choices) < 2:
            return None
        return {"type": block_type, "question": question, "choices": choices}
    if block_type == GuideBlockType.MAP.value:
        image_query = _as_str(raw.get("image_query"))
        locations = _txt_list(raw.get("locations"))
        if not (image_query or locations):
            return None
        return {
            "type": block_type,
            "image_query": image_query,
            "caption": _txt(raw.get("caption")),
            "locations": locations,
            **_media_fields(raw),
            **_aspect_field(raw),
        }
    # image
    image_query = _as_str(raw.get("image_query"))
    if not image_query:
        return None
    return {
        "type": block_type,
        "image_query": image_query,
        "caption": _txt(raw.get("caption")),
        **_media_fields(raw),
        **_aspect_field(raw),
    }


def normalize_guide_blocks(raw_blocks, strict: bool = False) -> list[dict]:
    """Normalize a raw LLM block array into clean block dicts, dropping junk.

    ``strict=True`` (admin content edits, #7) raises ``ValueError`` naming every
    block that would be dropped instead of silently dropping it — a UI bug must
    not be able to destroy content on save. The default lenient mode is for LLM
    output, where dropping junk is the point.
    """
    if not isinstance(raw_blocks, list):
        if strict:
            raise ValueError("content_blocks must be a JSON array of block objects")
        return []
    out: list[dict] = []
    rejected: list[str] = []
    for idx, x in enumerate(raw_blocks):
        if not isinstance(x, dict):
            rejected.append(f"block {idx}: not an object")
            continue
        b = _normalize_block(x)
        if b is None:
            rejected.append(f"block {idx}: invalid or empty {str(x.get('type', '?'))!r} block")
            continue
        out.append(b)
    if strict and rejected:
        raise ValueError("Rejected blocks: " + "; ".join(rejected))
    return out


def validate_review_ladder(blocks: list[dict], grade_band: str) -> list[str]:
    """Check the post-visit review structure against the band's ladder (#4).

    Returns human-readable problem strings (empty when compliant). Warn-only by
    design: callers log the problems instead of failing a multi-minute
    generation run — the admin edit UI is the correction path.
    """
    mc_count, choice_count, reflection_count = _review_ladder_for_band(grade_band)
    review_title, reflection_title = REVIEW_SECTION_TITLES["en"]

    def _section_span(title: str) -> list[dict]:
        for i, b in enumerate(blocks):
            if b.get("type") == "section" and b.get("title") == title:
                span = []
                for nxt in blocks[i + 1 :]:
                    if nxt.get("type") == "section":
                        break
                    span.append(nxt)
                return span
        return []

    problems: list[str] = []
    questions = [b for b in _section_span(review_title) if b.get("type") == "mc_question"]
    if not any(b.get("type") == "section" and b.get("title") == review_title for b in blocks):
        problems.append(f"missing section {review_title!r}")
    elif len(questions) != mc_count:
        problems.append(f"expected {mc_count} mc_question blocks, got {len(questions)}")
    for q in questions:
        if len(q.get("choices", [])) != choice_count:
            problems.append(
                f"question {q.get('question', '')[:40]!r} has "
                f"{len(q.get('choices', []))} choices, expected {choice_count}"
            )
    reflections = [b for b in _section_span(reflection_title) if b.get("type") == "callout"]
    if not any(b.get("type") == "section" and b.get("title") == reflection_title for b in blocks):
        problems.append(f"missing section {reflection_title!r}")
    elif len(reflections) != reflection_count:
        problems.append(
            f"expected {reflection_count} reflection callout(s), got {len(reflections)}"
        )
    return problems


def apply_spanish_block_overrides(
    en_blocks: list[dict], es_blocks: list[dict], excursion_key: str | None
) -> None:
    """Post-translation fixups on freshly translated ES blocks (in place).

    - Cover title ← the excursion's ``display_name_es`` (#2): DeepL mangles proper
      names like "Panamá Viejo Archeological Complex", so the canonical ES name
      wins for excursion-linked guides.
    - The two review section titles ← the canonical ES strings (#4), matched by
      position against the EN blocks (translate_blocks preserves block order), so
      machine-translated variants never reach the rendered guide.
    """
    excursion = get_excursion(excursion_key) if excursion_key else None
    if excursion and excursion.display_name_es:
        cover = next((b for b in es_blocks if b.get("type") == "cover"), None)
        if cover:
            cover["title"] = excursion.display_name_es

    en_to_es = dict(zip(REVIEW_SECTION_TITLES["en"], REVIEW_SECTION_TITLES["es"], strict=True))
    for i, b in enumerate(en_blocks):
        if (
            b.get("type") == "section"
            and b.get("title") in en_to_es
            and i < len(es_blocks)
            and es_blocks[i].get("type") == "section"
        ):
            es_blocks[i]["title"] = en_to_es[b["title"]]


def blocks_to_markdown(blocks: list[dict]) -> str:
    """Project structured guide blocks to markdown for ``content`` (admin UI, search,
    back-compat). Images/maps become caption placeholders — the PDF renders the real
    figures from the blocks themselves."""
    out: list[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "cover":
            out.append(f"# {b.get('title', '')}")
            if b.get("subtitle"):
                out.append(f"_{b['subtitle']}_")
        elif t == "section":
            out.append(f"## {b.get('title', '')}")
        elif t == "paragraph":
            out.append(b.get("text", ""))
        elif t == "bullets":
            out.append("\n".join(f"- {i}" for i in b.get("items", [])))
        elif t == "ordered_list":
            out.append("\n".join(f"{n}. {i}" for n, i in enumerate(b.get("items", []), 1)))
        elif t == "quote":
            q = f"> {b.get('text', '')}"
            if b.get("attribution"):
                q += f"\n> — {b['attribution']}"
            out.append(q)
        elif t == "timeline":
            out.append(
                "\n".join(
                    f"- **{e.get('label') or ''}** {e.get('text') or ''}".strip()
                    for e in b.get("events", [])
                )
            )
        elif t == "map":
            line = f"_[Map: {b.get('caption') or b.get('image_query') or 'map'}]_"
            if b.get("locations"):
                line += "\n" + "\n".join(f"- {loc}" for loc in b["locations"])
            out.append(line)
        elif t == "image":
            out.append(f"_[Image: {b.get('caption') or b.get('image_query') or ''}]_")
        elif t == "callout":
            lines = []
            if b.get("label"):
                lines.append(f"**{b['label']}**")
            if b.get("text"):
                lines.append(b["text"])
            for term in b.get("terms") or []:
                lines.append(f"- **{term.get('term') or ''}** — {term.get('definition') or ''}")
            out.append("\n".join(lines))
        elif t == "mc_question":
            lines = [b.get("question", "")]
            lines += [f"{chr(ord('a') + n)}) {c}" for n, c in enumerate(b.get("choices", []))]
            out.append("\n".join(lines))
    return "\n\n".join(s for s in out if s and s.strip())


def generate_edu_materials(
    research: EduResearch,
    suggestion: EduSuggestion,
    db: Session,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[list[EduMaterial], list[str], list[str]]:
    """Generate study guides for all grade bands of an edu suggestion.

    Pipeline per grade band: condense -> outline -> content, all on ``EDU_MODEL``.

    A band whose guide is already ``APPROVED`` is preserved (skipped, no LLM
    call) unless ``force`` is set. ``PENDING``/``REJECTED`` and missing bands
    are (re)generated. Returns ``(acted_materials, skipped_bands, failed_bands)``.

    **Each band is committed independently** (#90): a band that succeeds is
    durable even if a later band raises, and a band that fails is rolled back,
    recorded in ``failed_bands``, and skipped — the loop continues. A re-run
    therefore regenerates only the missing/PENDING/failed bands (APPROVED bands
    stay skipped), with no wasted re-spend on bands that already succeeded. The
    ``uq_edu_materials_research_type_band`` unique constraint (#97) keeps the
    per-band upsert idempotent under concurrent calls.

    The caller need not commit (this function commits per band); a trailing
    commit is harmless. ``progress(done, total, current)`` is called before each
    band starts (``current`` names it) and once more when the loop finishes.
    """
    grade_bands = suggestion.grade_bands or []

    # Defense-in-depth against unbounded LLM spend (#92): each band fans out to
    # up to 3 sequential calls. The API schema caps grade_bands at MAX_GRADE_BANDS,
    # but a suggestion persisted by another path (import, older row, future bug)
    # could still carry a pathological list — refuse it before the loop spends.
    if len(grade_bands) > MAX_GRADE_BANDS:
        raise ValueError(
            f"suggestion {suggestion.id} has {len(grade_bands)} grade_bands; "
            f"refusing to generate (max {MAX_GRADE_BANDS})"
        )

    materials: list[EduMaterial] = []
    skipped_bands: list[str] = []
    failed_bands: list[str] = []
    research_content = research.content or ""

    for index, band in enumerate(grade_bands):
        if progress is not None:
            progress(index, len(grade_bands), f"band {band}")
        logger.info("Generating edu material for band=%s suggestion=%s", band, suggestion.id)

        try:
            material = _generate_band(research, suggestion, band, research_content, db, force)
        except Exception:
            db.rollback()
            logger.exception("Failed to generate edu material for band=%s", band)
            failed_bands.append(band)
            continue

        if material is None:
            # Existing APPROVED guide preserved.
            skipped_bands.append(band)
            continue

        db.commit()
        db.refresh(material)
        materials.append(material)
        logger.info("Committed edu material id=%s for band=%s", material.id, band)

    if progress is not None:
        progress(len(grade_bands), len(grade_bands), "done")
    return materials, skipped_bands, failed_bands


def _generate_band(
    research: EduResearch,
    suggestion: EduSuggestion,
    band: str,
    research_content: str,
    db: Session,
    force: bool,
) -> EduMaterial | None:
    """Generate (or refresh) one band's study guide; flush but do not commit.

    Returns the upserted ``EduMaterial``, or ``None`` if an existing APPROVED
    guide was preserved.
    """
    existing = (
        db.query(EduMaterial)
        .filter(
            EduMaterial.research_id == research.id,
            EduMaterial.material_type == "guide",
            EduMaterial.grade_band == band,
        )
        .first()
    )
    if existing and existing.status == "APPROVED" and not force:
        logger.info(
            "Skipping APPROVED guide id=%s for band=%s (use force to overwrite)",
            existing.id,
            band,
        )
        return None

    # The per-band lens (#339): looked up once and threaded through every stage so
    # it permeates the briefing, outline, and content. None for standalone material.
    band_focus = focus_for(suggestion.excursion_key, band)

    # Excursion-linked guides are titled with the excursion's real name — never an
    # LLM invention. Standalone materials keep the LLM-proposed title.
    excursion = get_excursion(suggestion.excursion_key) if suggestion.excursion_key else None
    fixed_title = excursion.display_name if excursion and excursion.display_name else None

    # --- Phase 0: Condense research ---
    condensed = _condense_research(research_content, suggestion, band, band_focus)

    # --- Phase 1: Outline ---
    outline_prompt = build_edu_outline_prompt(
        research_content=condensed,
        suggestion_title=suggestion.title,
        description=suggestion.description,
        sub_topics=suggestion.sub_topics,
        grade_band=band,
        excursion_key=suggestion.excursion_key,
        band_focus=band_focus,
        fixed_title=fixed_title,
    )

    # Outline cap is sized so a detailed 6-section outline for a 3000-word guide
    # does not truncate (#93), with headroom for always-on adaptive thinking
    # (counts toward max_tokens; sized on Sonnet 5.5's ~30%-denser tokenizer).
    # create_message rejects any residual truncation (stop_reason ==
    # "max_tokens") before the parser papers over it.
    outline_msg = create_message(
        feature="edu_outline",
        model=EDU_MODEL,
        max_tokens=32_000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": outline_prompt}],
    )
    raw_response = first_text(outline_msg)
    title, outline = _parse_outline_response(raw_response)
    if fixed_title:
        title = fixed_title  # the prompt echoes it, but never trust the echo
    elif not title:
        title = f"{suggestion.title} — {band}"

    logger.info("Outline parsed: title=%r, outline_len=%d", title, len(outline))

    # --- Phase 2: Content blocks (#340) ---
    blocks_prompt = build_edu_blocks_prompt(
        research_content=condensed,
        outline=outline,
        title=title,
        grade_band=band,
        suggestion_title=suggestion.title,
        band_focus=band_focus,
    )

    # Blocks cap: a 9th-12th guide measured 15,217 output tokens (thinking +
    # blocks JSON) against the old 16,000 on 2026-09-22 — one richer research
    # document from truncating. Ceiling, not spend. Raised to 32,000 after the
    # Sonnet 5.5 switch: the Panama History Museum 9th-12th guide hit the 20,000
    # ceiling on 2026-10-03 (job 246) — 5.5 thinks more per turn at "high".
    blocks_msg = create_message(
        feature="edu_content",
        model=EDU_MODEL,
        max_tokens=64_000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": blocks_prompt}],
    )
    blocks = normalize_guide_blocks(parse_llm_json(blocks_msg))
    if not blocks:
        raise ValueError("Guide content generation returned no usable blocks")

    # Enforce the fixed title on the cover deterministically (#2) — the prompt asks
    # for it, but the stored blocks are what the PDF renders.
    if fixed_title:
        cover = next((b for b in blocks if b.get("type") == "cover"), None)
        if cover:
            cover["title"] = fixed_title
        else:
            blocks.insert(0, {"type": "cover", "title": fixed_title, "subtitle": None})

    for problem in validate_review_ladder(blocks, band):
        logger.warning("Review-ladder check (band=%s): %s", band, problem)

    content_md = blocks_to_markdown(blocks)

    # --- Upsert ---
    now = datetime.now(UTC)
    if existing:
        existing.research_id = research.id
        existing.title = title
        existing.content_blocks = blocks
        existing.content = content_md
        # A regenerated guide invalidates any prior Spanish translation.
        existing.content_es = None
        existing.content_blocks_es = None
        existing.status = "PENDING"
        existing.updated_at = now
        material = existing
    else:
        material = EduMaterial(
            research_id=research.id,
            material_type="guide",
            grade_band=band,
            title=title,
            content_blocks=blocks,
            content=content_md,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        db.add(material)

    db.flush()
    return material


SLIDE_TEMPLATE_VALUES = set(SlideTemplate.values())

# Defensive caps behind the prompt's HARD LIMITS block: the canvas is a fixed
# 1280x720 box with overflow:hidden, so text the model over-writes would clip.
# The render-time autofit pass is the last layer; these keep it in easy range.
SLIDE_TITLE_MAX = 80
SLIDE_LINE_MAX = 140
SLIDE_BODY_LINE_CAPS = {
    "bullets": 5,
    "two-col-image-left": 4,
    "two-col-image-right": 4,
    "timeline": 5,
    "map": 6,
    "title": 2,
    "section": 2,
    "full-image": 2,
    "closing": 2,
}


def _truncate_words(text: str, limit: int) -> str:
    """Truncate on a word boundary with an ellipsis when over ``limit`` chars."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip()
    return f"{cut}…"


def _normalize_slide(raw: dict) -> dict:
    """Validate/normalize one LLM slide object into EduSlide field values."""
    template = str(raw.get("template", "")).strip().lower()
    if template not in SLIDE_TEMPLATE_VALUES:
        template = SlideTemplate.BULLETS.value  # safe default for an unknown template
    title = str(raw.get("title") or "").strip()
    title = _truncate_words(title, SLIDE_TITLE_MAX) or None
    body_val = raw.get("body")
    if isinstance(body_val, list):
        lines = [str(x).strip() for x in body_val if str(x).strip()]
    elif body_val:
        lines = [ln.strip() for ln in str(body_val).split("\n") if ln.strip()]
    else:
        lines = []
    cap = SLIDE_BODY_LINE_CAPS.get(template)
    if cap is not None and len(lines) > cap:
        logger.warning(
            "Slide '%s' (%s): dropping %d body line(s) over the %d-line cap",
            title,
            template,
            len(lines) - cap,
            cap,
        )
        lines = lines[:cap]
    body = "\n".join(_truncate_words(ln, SLIDE_LINE_MAX) for ln in lines) or None
    image_query = str(raw.get("image_query") or "").strip()[:500] or None
    return {
        "template": template,
        "title": title,
        "body": body,
        "image_query": image_query,
        # Narration is authored in the later narration pass (two-stage flow) —
        # anything the content model volunteers here is discarded.
        "narration": None,
    }


def slides_preview_text(slides: list[dict]) -> str:
    """Readable plain-text rendering of the deck, stored on EduMaterial.content for
    the existing detail/download endpoints. The structured edu_slides rows are the
    source of truth for rendering (#321) and narration audio (#322)."""
    blocks = []
    for i, slide in enumerate(slides, start=1):
        lines = [f"## Slide {i}: {slide.get('title') or ''}".rstrip()]
        if slide.get("body"):
            lines.append(slide["body"])
        if slide.get("narration"):
            lines.append(f"_Narración:_ {slide['narration']}")
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


def generate_edu_slides(
    guide_material: EduMaterial,
    db: Session,
    force: bool = False,
    language: str = "es",
) -> tuple[EduMaterial, bool]:
    """Generate a slide deck's CONTENT from the guide's underlying research.

    Stage 1 of the two-stage deck flow (slides redesign): condense the research
    into a slides briefing (``SLIDES_CONDENSED_TARGET``, persisted on
    ``EduMaterial.source_briefing``), then one ``EDU_MODEL`` call returns a JSON array
    of 16-22 slides — template / title / body / image_query only, **no
    narration**. A human edits and approves the deck in the admin (picking real
    photos for the image_query placeholders); ``generate_edu_narration`` then
    writes Diego's script against the final slides. The approved guide remains
    the entry point (same excursion+band pairing), but the guide's text is no
    longer the fact source — it starved decks down to ~7 minutes.

    If slides for this ``(research_id, grade_band)`` already exist and are
    ``APPROVED``, they are preserved (no LLM call) unless ``force`` is set —
    ``force`` replaces every row, dropping media picks, narration, and rendered
    artifacts by design. Returns ``(material, regenerated)``. Caller commits.
    """
    research = guide_material.research
    suggestion = research.suggestion if research else None
    if suggestion is None:
        raise ValueError("Guide material has no linked suggestion via research")
    if not (research.content or "").strip():
        raise ValueError("Research has no content to generate slides from")

    existing = (
        db.query(EduMaterial)
        .filter(
            EduMaterial.research_id == guide_material.research_id,
            EduMaterial.material_type == "slides",
            EduMaterial.grade_band == guide_material.grade_band,
        )
        .first()
    )
    if existing and existing.status == "APPROVED" and not force:
        logger.info(
            "Skipping APPROVED slides id=%s for band=%s (use force to overwrite)",
            existing.id,
            guide_material.grade_band,
        )
        return existing, False

    logger.info(
        "Generating slides for guide_id=%s band=%s lang=%s (research-based)",
        guide_material.id,
        guide_material.grade_band,
        language,
    )

    band = guide_material.grade_band
    band_focus = focus_for(suggestion.excursion_key, band)
    briefing = _condense_research(
        research.content,
        suggestion,
        band,
        band_focus=band_focus,
        target_words=SLIDES_CONDENSED_TARGET.get(band, 3500),
        feature="edu_slides_condense",
    )

    prompt = build_edu_slides_prompt(
        research_content=briefing,
        suggestion_title=suggestion.title,
        description=suggestion.description,
        sub_topics=suggestion.sub_topics,
        grade_band=band,
        excursion_key=suggestion.excursion_key,
        guide_title=guide_material.title or "",
        band_focus=band_focus,
        language=language,
    )

    msg = create_message(
        feature="edu_slides",
        model=EDU_MODEL,
        max_tokens=SLIDES_MAX_TOKENS,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )
    data = parse_llm_json(msg)
    if not isinstance(data, list) or not data:
        raise ValueError("Slide generation did not return a non-empty JSON array")
    slides = [_normalize_slide(s) for s in data if isinstance(s, dict)]
    if not slides:
        raise ValueError("Slide generation returned no usable slides")

    now = datetime.now(UTC)
    title = f"{guide_material.title} — Slides"
    preview = slides_preview_text(slides)

    # Upsert the slides material row on (research_id, "slides", grade_band), then
    # replace its slides (clear old via delete-orphan before appending the new).
    if existing:
        existing.title = title
        existing.content = preview
        existing.source_briefing = briefing
        existing.status = "PENDING"
        existing.updated_at = now
        material = existing
        material.slides.clear()
    else:
        material = EduMaterial(
            research_id=guide_material.research_id,
            material_type="slides",
            grade_band=guide_material.grade_band,
            title=title,
            content=preview,
            source_briefing=briefing,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        db.add(material)
    db.flush()  # persist material (id) and delete any cleared old slides

    for position, slide in enumerate(slides, start=1):
        material.slides.append(
            EduSlide(
                position=position,
                template=slide["template"],
                title=slide["title"],
                body=slide["body"],
                image_query=slide["image_query"],
                narration=slide["narration"],
            )
        )
    db.flush()
    logger.info(
        "Created slides material id=%s with %d slides for band=%s",
        material.id,
        len(slides),
        material.grade_band,
    )
    return material, True


def regenerate_slides_subset(
    material: EduMaterial,
    positions: set[int],
    db: Session,
    language: str = "es",
) -> dict:
    """Re-roll the CONTENT of a subset of an existing deck's slides, in place.

    One ``EDU_MODEL`` call sees the full current deck (for continuity) plus the same
    condensed briefing (``EduMaterial.source_briefing``; legacy decks re-condense
    on the fly) and rewrites template/title/body/image_query for ONLY the targeted
    ``positions`` — the slide count is fixed. Targeted rows are updated in place
    (keeping their ``id``, human photo pick, narration, and audio) and their stale
    rendered frame (``image_url``) is cleared, mirroring the PUT edit's "content
    changed → frame stale" rule. Untouched slides are left entirely alone. Status
    is not changed (a re-roll is an edit, like the manual slide editor). Strict:
    the response must cover exactly the targeted positions or nothing is saved.
    Returns ``{positions, slides_regenerated}``. Caller commits.
    """
    slides = material.slides
    if not slides:
        raise ValueError("Material has no slides to regenerate")
    targets = {s for s in slides if s.position in positions}
    if not targets:
        raise ValueError("None of the requested positions match this deck's slides")

    research = material.research
    suggestion = research.suggestion if research else None
    if suggestion is None:
        raise ValueError("Slides material has no linked suggestion via research")

    band = material.grade_band
    band_focus = focus_for(suggestion.excursion_key, band)
    briefing = (material.source_briefing or "").strip()
    if not briefing:
        if not (research.content or "").strip():
            raise ValueError("No source briefing and the research has no content")
        briefing = _condense_research(
            research.content,
            suggestion,
            band,
            band_focus=band_focus,
            target_words=SLIDES_CONDENSED_TARGET.get(band, 3500),
            feature="edu_slides_condense",
        )
        material.source_briefing = briefing

    logger.info(
        "Regenerating slide content for material id=%s band=%s [subset %s]",
        material.id,
        band,
        sorted(positions),
    )
    prompt = build_edu_slides_subset_prompt(
        deck=[
            {"position": s.position, "template": s.template, "title": s.title, "body": s.body}
            for s in slides
        ],
        target_positions=sorted(positions),
        research_content=briefing,
        suggestion_title=suggestion.title,
        grade_band=band,
        excursion_key=suggestion.excursion_key,
        band_focus=band_focus,
        language=language,
    )
    # Same cap as the full-deck call: the subset prompt carries the whole deck
    # as context and thinks over the same arc, so it needs the same headroom.
    msg = create_message(
        feature="edu_slides",
        model=EDU_MODEL,
        max_tokens=SLIDES_MAX_TOKENS,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )
    data = parse_llm_json(msg)
    if not isinstance(data, list):
        raise ValueError("Slide regeneration did not return a JSON array")

    by_position: dict[int, dict] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        pos = raw.get("position")
        if isinstance(pos, int) and not isinstance(pos, bool):
            by_position[pos] = _normalize_slide(raw)

    expected = {s.position for s in targets}
    if set(by_position) != expected:
        raise ValueError(
            f"Regenerated positions mismatch: got {sorted(by_position)}, "
            f"expected {sorted(expected)} — nothing was saved"
        )

    for slide in targets:
        new = by_position[slide.position]
        slide.template = new["template"]
        slide.title = new["title"]
        slide.body = new["body"]
        slide.image_query = new["image_query"]
        # Content changed → the rendered frame is stale (same rule as the PUT
        # editor). Keep the human photo pick, narration, and audio.
        slide.image_url = None

    material.content = slides_preview_text(
        [{"title": s.title, "body": s.body, "narration": s.narration} for s in slides]
    )
    material.updated_at = datetime.now(UTC)
    return {"positions": sorted(expected), "slides_regenerated": len(targets)}


def generate_edu_narration(
    material: EduMaterial,
    db: Session,
    force: bool = False,
    language: str = "es",
    positions: set[int] | None = None,
) -> dict:
    """Write Diego's narration over an approved, human-edited slide deck.

    Stage 2 of the two-stage deck flow: one ``EDU_MODEL`` call takes the FINAL slides
    (position / template / title / body) plus the same condensed briefing the
    content was generated from (``material.source_briefing``; legacy decks
    without one re-condense on the fly) and returns one narration per slide.
    The script must walk through every on-screen line and hit a total budget
    that lands the video at 15-17 minutes.

    ``positions`` (1-based) switches to a **targeted rewrite**: the whole deck
    (with existing narration) is sent for continuity, but only those slides are
    rewritten and returned. Their ``audio_url`` / ``duration_ms`` are cleared;
    other slides are untouched. A subset rewrite is inherently intentional, so it
    ignores the ``force`` guard. Whole-deck validation stays strict: the response
    must cover exactly the expected positions (1..N, or the subset) with every
    narration non-empty — otherwise ValueError and nothing is assigned. On
    success the plain-text preview on ``EduMaterial.content`` is regenerated.
    Caller commits.
    """
    slides = material.slides
    if not slides:
        raise ValueError("Material has no slides to narrate")
    subset = positions is not None
    if subset:
        targets = {s for s in slides if s.position in positions}
        if not targets:
            raise ValueError("None of the requested positions match this deck's slides")
    else:
        targets = set(slides)
        if any(s.narration for s in slides) and not force:
            raise ValueError("Deck already has narration; pass force to regenerate")

    research = material.research
    suggestion = research.suggestion if research else None
    if suggestion is None:
        raise ValueError("Slides material has no linked suggestion via research")

    band = material.grade_band
    band_focus = focus_for(suggestion.excursion_key, band)
    briefing = (material.source_briefing or "").strip()
    if not briefing:
        # Legacy deck generated before briefings were persisted — rebuild one.
        if not (research.content or "").strip():
            raise ValueError("No source briefing and the research has no content")
        briefing = _condense_research(
            research.content,
            suggestion,
            band,
            band_focus=band_focus,
            target_words=SLIDES_CONDENSED_TARGET.get(band, 3500),
            feature="edu_slides_condense",
        )
        material.source_briefing = briefing

    logger.info(
        "Generating narration for slides material id=%s (%d slides, band=%s)%s",
        material.id,
        len(slides),
        band,
        f" [subset {sorted(positions)}]" if subset else "",
    )
    # A subset rewrite shows each slide's existing narration for continuity; the
    # whole-deck pass authors from scratch, so narration is omitted.
    slide_payload = [
        {
            "position": s.position,
            "template": s.template,
            "title": s.title,
            "body": s.body,
            **({"narration": s.narration} if subset else {}),
        }
        for s in slides
    ]
    prompt = build_edu_narration_prompt(
        slides=slide_payload,
        briefing=briefing,
        suggestion_title=suggestion.title,
        grade_band=band,
        excursion_key=suggestion.excursion_key,
        band_focus=band_focus,
        language=language,
        target_positions=sorted(positions) if subset else None,
    )
    # 16,000 truncated twice in a row on a 22-slide deck (material 32, 2026-09-24):
    # at effort "high" the adaptive thinking spent the whole budget before the
    # ~2,500-word script was out. 32,000 leaves headroom for the thinking plus
    # the JSON. The call streams at ~105 tok/s, so a worst case run nears the
    # api. vhost's 300s proxy timeout; a 504 there still commits server-side.
    msg = create_message(
        feature="edu_narration",
        model=EDU_MODEL,
        max_tokens=64_000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )
    data = parse_llm_json(msg)
    if not isinstance(data, list):
        raise ValueError("Narration generation did not return a JSON array")

    by_position: dict[int, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        pos = item.get("position")
        narration = str(item.get("narration") or "").strip()
        if isinstance(pos, int) and not isinstance(pos, bool) and narration:
            by_position[pos] = narration

    expected = {s.position for s in targets}
    if set(by_position) != expected:
        raise ValueError(
            f"Narration positions mismatch: got {sorted(by_position)}, "
            f"expected {sorted(expected)} — nothing was saved"
        )

    for slide in targets:
        slide.narration = by_position[slide.position]
        # The old audio no longer matches the script.
        slide.audio_url = None
        slide.duration_ms = None

    material.content = slides_preview_text(
        [{"title": s.title, "body": s.body, "narration": s.narration} for s in slides]
    )
    material.updated_at = datetime.now(UTC)

    total_words = sum(len(n.split()) for n in by_position.values())
    if not subset and not 1_900 <= total_words <= 3_000:
        logger.warning(
            "Narration total is %d words (target 2,300-2,600) for material id=%s",
            total_words,
            material.id,
        )
    else:
        logger.info(
            "Narration total: %d words for material id=%s%s",
            total_words,
            material.id,
            f" [subset {sorted(positions)}]" if subset else "",
        )
    return {"slides_narrated": len(targets), "total_words": total_words}


def normalize_slides_for_update(raw_slides, strict: bool = True) -> list[dict]:
    """Validate/normalize admin-edited slides (mirror of ``normalize_guide_blocks``).

    In strict mode (the PUT endpoint) a slide that would be dropped rejects the
    whole request with one ValueError naming every problem — an unknown template
    is an error here, never silently defaulted to bullets like LLM output.
    ``id`` (the existing EduSlide row id, for artifact carry-over) and
    ``narration`` pass through; ``media_id``/``media_path`` are the human photo
    pick. Non-strict mode drops bad slides silently.
    """
    if not isinstance(raw_slides, list):
        raise ValueError("slides must be a list")

    errors: list[str] = []
    out: list[dict] = []
    for i, raw in enumerate(raw_slides, start=1):
        if not isinstance(raw, dict):
            errors.append(f"slide {i}: not an object")
            continue
        template = str(raw.get("template") or "").strip().lower()
        if template not in SLIDE_TEMPLATE_VALUES:
            errors.append(f"slide {i}: unknown template '{template}'")
            continue
        title = _as_str(raw.get("title"))
        title = title[:300] if title else None
        body_val = raw.get("body")
        if isinstance(body_val, list):
            body = "\n".join(str(x).strip() for x in body_val if str(x).strip()) or None
        else:
            body = (str(body_val).strip() or None) if body_val else None
        if not title and not body:
            errors.append(f"slide {i}: needs a title or body")
            continue
        image_query = _as_str(raw.get("image_query"))
        media_path = _as_str(raw.get("media_path"))
        media_id = raw.get("media_id")
        if not isinstance(media_id, int) or isinstance(media_id, bool):
            media_id = None
        slide_id = raw.get("id")
        if not isinstance(slide_id, int) or isinstance(slide_id, bool):
            slide_id = None
        out.append(
            {
                "id": slide_id,
                "template": template,
                "title": title,
                "body": body,
                "image_query": image_query[:500] if image_query else None,
                "media_id": media_id,
                "media_path": media_path[:500] if media_path else None,
                "narration": _as_str(raw.get("narration")),
            }
        )

    if errors and strict:
        raise ValueError("; ".join(errors))
    return out
