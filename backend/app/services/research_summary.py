"""Generate concise research summaries for infographic creation."""

from app.services.llm import HAIKU_MODEL, create_message

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


def generate_research_summary(content: str) -> str:
    """Generate a concise summary from research content using Claude Haiku."""
    message = create_message(
        feature="research_summary",
        model=HAIKU_MODEL,
        # ~800-word summary is ~1.1k tokens; 3000 leaves comfortable headroom so a
        # well-behaved response never brushes the cap. Combined with the hard
        # length limit in the prompt, this keeps create_message's truncation guard
        # (reject_on_truncation, default True) from rejecting an over-long summary.
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(content=content)},
        ],
    )

    return message.content[0].text
