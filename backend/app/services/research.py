"""Research service — prompt generation and document validation."""

import re

from app.models.article_suggestion import ArticleSuggestion
from app.models.category import Category, Taxonomy
from app.services import research_common
from app.services.llm import SONNET_MODEL, create_message, first_text


def generate_research_prompt(
    suggestion: ArticleSuggestion,
    category: Category,
    taxonomy: Taxonomy,
) -> str:
    """Use Sonnet to tailor a research prompt for the given suggestion."""
    sub_topics_list = "\n".join(f"- {t}" for t in (suggestion.sub_topics or []))

    template = f"""REF: suggestion_{suggestion.id}

Research the topic described below for a college-level historical
education platform about Panama.

CONTEXT:
This article is part of the "{taxonomy.name}" series, specifically
in the category "{category.name}": {category.description or ""}

RESEARCH FOCUS:
Title: {suggestion.title}

{suggestion.main_topic or ""}

Key areas to investigate:
{sub_topics_list}

Point of view: {suggestion.point_of_view}

REQUIREMENTS:
- Prioritize primary and scholarly secondary sources (academic journals,
  books by recognized historians)
- Distinguish between well-documented facts and areas of historical debate
- Include specific dates, names, and figures where available
- Provide full source citations so claims can be verified
- Include a formal "## References" or "## Sources" section at the end
  listing all works cited (this is required for automated processing)
- Target approximately 5000-8000 words of substantive, narrative prose
  at a college academic level"""

    message = create_message(
        feature="research_prompt",
        model=SONNET_MODEL,
        max_tokens=14_000,
        output_config={"effort": "high"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Below is a research prompt template for a Panama history article. "
                    "Tailor it to be more effective:\n"
                    "- Adjust source guidance to match the era (colonial, modern, canal era, etc.)\n"
                    "- Refine investigation areas based on how specific the sub-topics are\n"
                    "- Keep the REF line, CONTEXT, RESEARCH FOCUS, and REQUIREMENTS structure\n"
                    "- Keep it as a prompt (instructions for a researcher), not an article\n\n"
                    f"TEMPLATE:\n{template}"
                ),
            }
        ],
    )

    return first_text(message)


def validate_research_document(content: str, suggestion: ArticleSuggestion) -> dict:
    return research_common.validate_research_document(content, suggestion.sub_topics or [])


def extract_suggestion_title(content: str) -> str | None:
    """Extract the first H1 heading from markdown content."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None
