"""Service for generating article suggestions using Anthropic adaptive thinking."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.article_suggestion import ArticleSuggestion
from app.models.category import Category, Taxonomy
from app.prompts.suggestion_generation import build_historical_suggestion_prompt
from app.services.llm import SONNET_MODEL, create_message, parse_llm_json

# Thinking counts *toward* max_tokens, so the real output budget is
# max_tokens - THINKING_RESERVE. A fixed cap left as little as ~6k for the JSON
# and truncated near the top of the allowed num_suggestions range (#101). Size
# the cap from the request: reserve room for adaptive thinking (Sonnet 5 has no
# fixed budget — the reserve is headroom, not a limit), a fixed overhead for
# the JSON envelope, plus a per-suggestion allowance (each suggestion carries a
# title, main_topic, point_of_view, and a sub_topics array — ~400-500 tokens on
# the 4.6 tokenizer; Sonnet 5's tokenizer runs ~30% higher, hence the bumps).
THINKING_RESERVE = 10_000
BASE_OUTPUT_TOKENS = 2_000
PER_SUGGESTION_TOKENS = 800


def _max_tokens_for(num_suggestions: int) -> int:
    """Total max_tokens covering the thinking reserve plus the JSON output."""
    return THINKING_RESERVE + BASE_OUTPUT_TOKENS + num_suggestions * PER_SUGGESTION_TOKENS


def generate_historical_suggestions(
    category: Category,
    taxonomy: Taxonomy,
    num_suggestions: int,
    db: Session,
) -> list[ArticleSuggestion]:
    """Generate chronologically-ordered article suggestions for a historical category."""
    # Get existing titles for this category to avoid duplicates
    existing_titles = [
        row[0]
        for row in db.query(ArticleSuggestion.title)
        .filter(ArticleSuggestion.category_id == category.id)
        .all()
    ]

    prompt = build_historical_suggestion_prompt(
        taxonomy_name=taxonomy.name,
        taxonomy_description=taxonomy.description,
        category_name=category.name,
        category_description=category.description,
        num_suggestions=num_suggestions,
        existing_titles=existing_titles,
    )

    message = create_message(
        feature="suggestion_generation",
        model=SONNET_MODEL,
        max_tokens=_max_tokens_for(num_suggestions),
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )

    result = parse_llm_json(message, required_key="suggestions")

    now = datetime.now(UTC)
    suggestions = []

    for item in result["suggestions"]:
        # Flatten sub_topics: "Title: Description"
        sub_topics = [f"{st['title']}: {st['description']}" for st in item.get("sub_topics", [])]

        suggestion = ArticleSuggestion(
            category_id=category.id,
            title=item["title"],
            main_topic=item.get("main_topic"),
            sub_topics=sub_topics,
            point_of_view=item["point_of_view"],
            created_at=now,
            updated_at=now,
        )
        db.add(suggestion)
        suggestions.append(suggestion)

    db.flush()
    db.commit()
    for s in suggestions:
        db.refresh(s)

    return suggestions
