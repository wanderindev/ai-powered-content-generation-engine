"""Prose traversal over a slide deck's ``edu_slides`` rows (#464).

The deck's counterpart to ``guide_blocks`` (#461), and it exists for the same
reason: ``EduMaterial.content`` is only a derived preview built by
``slides_preview_text``. The rows are the source of truth — the renderer (#321)
draws from ``title``/``body`` and the TTS (#322) speaks ``narration`` — so a
correction written to ``content`` would never reach the video and would be
clobbered on the next regeneration.

The deck is Spanish-native (#320): slides and narration are authored directly in
Spanish so they read naturally in Diego's cloned voice, and ``translate_edu_guide``
refuses decks for that reason. There is no English stage to check instead, so the
Spanish text is both what gets grounded and what a FIND snippet must anchor to.

A deck is represented as an ordered list of ``(slide_id, field, text)`` segments.
Keeping the slide id in the segment is what makes the write-back unambiguous: a
correction lands on the row it came from rather than on a whole-deck blob.
"""

from __future__ import annotations

# The three prose fields of a slide, in reading order. ``image_query`` and the
# artifact URLs are machine values — never prose, never a correction target.
SLIDE_PROSE_FIELDS = ("title", "body", "narration")

# (slide_id, field, text)
SlideSegment = tuple[int, str, str]


def slide_segments(slides) -> list[SlideSegment]:
    """Flatten position-ordered slides into prose segments.

    Empty fields are skipped: they contribute nothing to grounding and would
    only create write-back targets that can never match.
    """
    segments: list[SlideSegment] = []
    for slide in slides:
        for field in SLIDE_PROSE_FIELDS:
            text = getattr(slide, field, None)
            if text and text.strip():
                segments.append((slide.id, field, text))
    return segments


def segments_prose(segments: list[SlideSegment]) -> str:
    """The deck's prose as plain text, in slide order.

    Used both as the text claims are extracted from and as the text corrections
    are generated against, so a FIND snippet is always a verbatim substring of a
    stored field.
    """
    return "\n".join(text for _, _, text in segments)


def slides_prose(slides) -> str:
    """Convenience: :func:`segments_prose` over :func:`slide_segments`."""
    return segments_prose(slide_segments(slides))


def count_in_segments(segments: list[SlideSegment], snippet: str) -> int:
    """How many times *snippet* occurs across the deck's prose.

    Counted per segment and summed, so the "exactly once" invariant means the
    same thing here as it does for guides. A phrase repeated in both a slide's
    ``body`` and its ``narration`` therefore reads as ambiguous and fails the
    claim rather than being rewritten in two places on one edit — the operator
    is told, instead of the deck being changed twice silently.
    """
    if not snippet:
        return 0
    return sum(text.count(snippet) for _, _, text in segments)


def apply_to_segments(segments: list[SlideSegment], find: str, replace: str) -> list[SlideSegment]:
    """Return a new segment list with *find* replaced by *replace*.

    The caller is expected to have asserted exactly one occurrence via
    :func:`count_in_segments`; replacing everywhere keeps this total rather than
    silently partial if that ever changes.
    """
    return [(slide_id, field, text.replace(find, replace)) for slide_id, field, text in segments]


def write_segments(slides, segments: list[SlideSegment]) -> None:
    """Write corrected segment text back onto the slide rows it came from.

    Only fields whose text actually changed are assigned, so an unrelated slide
    is not marked dirty (and its ``updated_at`` not bumped) by a correction
    elsewhere in the deck.
    """
    by_id = {slide.id: slide for slide in slides}
    for slide_id, field, text in segments:
        slide = by_id.get(slide_id)
        if slide is not None and getattr(slide, field) != text:
            setattr(slide, field, text)
