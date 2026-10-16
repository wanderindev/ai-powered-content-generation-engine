"""Generate concise research summaries for infographic creation."""

import logging

from app.services.llm import HAIKU_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a research summarizer for a historical education platform about Panama.
Your task is to distill a long research document into a concise summary suitable for creating an infographic.

Guidelines:
- Extract the most important facts, dates, figures, and key points
- Organize information in clear, scannable sections using markdown headers
- Use bullet points for easy consumption
- Include notable quotes or statistics that would work well visually
- STRICT LENGTH LIMIT: keep the summary at or under 800 words. This is a hard
  cap, not a target — be concise, cover the key points, and stop. Do not pad or
  restate. For very long source documents, select the most important material
  rather than trying to cover everything.
- Preserve historical accuracy — do not add information not in the source
- Write in the same language as the source document"""

USER_PROMPT_TEMPLATE = """Summarize the following research document for use in creating an infographic.
Focus on the most impactful and visually representable points. Keep your summary under 800 words.

# Research Document

{content}"""

# An ~800-word summary is ~1.1k tokens (~1.4k on Haiku 5.5's tokenizer). The cap
# was 3000 — ~2.7x that, the ceiling that stops a rambling model from producing
# an "infographic summary" as long as its source. Haiku 5.5 thinks at medium
# effort and thinking counts toward the cap (#610), so it is 8000 now: the old
# 3000 for text plus thinking headroom (and, at 8000, the streamed timeout). A
# cut-off that lands while still thinking leaves no text to trim, so
# ``first_text`` raises ValueError — the headroom exists to make that rare.
SUMMARY_MAX_TOKENS = 8000


def _trim_dangling_tail(text: str) -> str:
    """Drop the partial trailing line of a response cut off at ``max_tokens``.

    The cut lands at a token boundary, so the final line is mid-sentence (or
    mid-bullet) and is dropped wholesale. A single-line response is returned
    unchanged rather than emptied — losing the whole summary is worse than
    keeping one unfinished sentence.
    """
    lines = text.rstrip().split("\n")
    if len(lines) > 1:
        lines.pop()
    return "\n".join(lines).rstrip() or text.rstrip()


def generate_research_summary(content: str) -> str:
    """Generate a concise summary from research content using Claude Haiku."""
    message = create_message(
        feature="research_summary",
        model=HAIKU_MODEL,
        max_tokens=SUMMARY_MAX_TOKENS,
        output_config={"effort": "medium"},
        # The prompt's hard 800-word limit is guidance Haiku does not reliably
        # honour on long research documents — it overran max_tokens in prod at
        # 2000 (2026-06-20) and again at 3000 (2026-07-25, research_id=32).
        # Raising the ceiling again would only buy a longer ramble, so treat a
        # stop at the cap as the length-capping outcome it is rather than a
        # broken response: keep the (reviewed, regenerable) summary and trim its
        # dangling tail instead of failing the whole admin action.
        reject_on_truncation=False,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(content=content)},
        ],
    )

    summary = first_text(message)
    if getattr(message, "stop_reason", None) == "max_tokens":
        logger.warning(
            "research summary overran the %d-token cap; storing it trimmed at the "
            "last complete line",
            SUMMARY_MAX_TOKENS,
        )
        summary = _trim_dangling_tail(summary)
    return summary
