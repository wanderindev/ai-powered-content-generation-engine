"""Article generation service — 3-phase pipeline: outline, write, tag."""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.category import Category, Taxonomy
from app.models.research import Research
from app.models.tag import Tag
from app.prompts.article_generation import (
    build_article_prompt,
    build_excerpt_prompt,
    build_focus_condense_prompt,
    build_outline_prompt,
    build_summary_prompt,
    build_tagging_prompt,
)
from app.services.llm import HAIKU_MODEL, SONNET_MODEL, create_message, first_text, parse_llm_json

logger = logging.getLogger(__name__)

# Research longer than this is condensed before being embedded in the content
# prompt (#102). Mirrors the edu pipeline's threshold: above this length the
# writer drifts off its word-count/section constraints. Outlining (phase 1) is
# NOT condensed — the split decision needs the full fact set, and the outliner
# emits JSON, not 2000 words of prose, so it does not drift.
CONDENSE_THRESHOLD = 2500


def _condense_research(
    research: Research,
    suggestion: ArticleSuggestion,
    *,
    outline: str,
    title: str,
) -> str:
    """Condense research into an outline-targeted briefing via Sonnet (#102).

    Returns the research verbatim when it is already short enough. Unlike a flat
    single-article condensation, this is keyed to the specific article's outline:
    broad context for the whole topic plus detailed retention of the facts the
    outline's sections need. That keeps a series' sibling-article facts from
    being discarded and guards against omitting facts this article actually uses.
    Runs on Sonnet — outline-keyed selective extraction is a routing-plus-fidelity
    task weaker on Haiku than flat summarization.
    """
    content = research.content or ""
    word_count = len(content.split())
    if word_count <= CONDENSE_THRESHOLD:
        logger.info(
            "Research is %d words (under %d), skipping condensation",
            word_count,
            CONDENSE_THRESHOLD,
        )
        return content

    logger.info("Condensing research from %d words for article generation", word_count)
    prompt = build_focus_condense_prompt(
        research_content=content,
        outline=outline,
        title=title,
        suggestion_title=suggestion.title,
        suggestion_main_topic=suggestion.main_topic,
        suggestion_point_of_view=suggestion.point_of_view,
    )
    # The focus briefing is ~1,800 words (~3k tokens on Sonnet 5's tokenizer),
    # but adaptive thinking also counts toward max_tokens, so the cap sits at
    # the streaming threshold and the call streams instead of risking the
    # 120s non-streamed timeout.
    msg = create_message(
        feature="article_condense",
        model=SONNET_MODEL,
        max_tokens=8000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )
    condensed = first_text(msg).strip()
    logger.info("Condensed research to %d words", len(condensed.split()))
    return condensed


# --- Phase 1: Outline Generation ---


def generate_outlines(
    research: Research,
    suggestion: ArticleSuggestion,
    category: Category,
    taxonomy: Taxonomy,
) -> list[dict]:
    """Generate article outline(s) from approved research. Returns list of outline dicts.

    The full research feeds the outliner (no condensation): the series-split
    decision needs the complete fact set, and condensing first both degrades that
    decision and suppresses the >4000-word split heuristic in build_outline_prompt.
    """
    prompt = build_outline_prompt(
        research_content=research.content or "",
        suggestion_title=suggestion.title,
        suggestion_main_topic=suggestion.main_topic,
        suggestion_point_of_view=suggestion.point_of_view,
        category_name=category.name,
        category_description=category.description,
        taxonomy_name=taxonomy.name,
    )

    message = create_message(
        feature="article_outline",
        model=SONNET_MODEL,
        max_tokens=20_000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )

    result = parse_llm_json(message, required_key="articles")
    return result["articles"]


def create_article_records_from_outlines(
    outlines: list[dict],
    research: Research,
    category_id: int,
    db: Session,
) -> list[Article]:
    """Create Article rows from outlines. Handles series parent/child logic."""
    now = datetime.now(UTC)
    articles = []

    for i, outline_data in enumerate(outlines):
        article = Article(
            research_id=research.id,
            category_id=category_id,
            title=outline_data["title"],
            outline=outline_data["outline"],
            ai_summary=outline_data.get("summary"),
            outline_status="PENDING",
            status="PENDING",
            series_order=i + 1 if len(outlines) > 1 else None,
            created_at=now,
            updated_at=now,
        )
        db.add(article)

        if i == 0 and len(outlines) > 1:
            # Flush to get the parent ID
            db.flush()
            parent_id = article.id
        elif i > 0 and len(outlines) > 1:
            article.series_parent_id = parent_id

        articles.append(article)

    db.flush()
    return articles


# --- Phase 2: Article Writing ---


def generate_article_content(
    article: Article,
    research: Research,
    suggestion: ArticleSuggestion,
    category: Category,
    taxonomy: Taxonomy,
) -> str:
    """Generate full article content from outline + research. Returns markdown."""
    series_context = None
    if article.series_order and article.series_order > 1 and article.series_parent:
        series_context = (
            f"This is Part {article.series_order} of a series. "
            f'Part 1 is titled: "{article.series_parent.title}"'
        )
    elif article.series_order == 1:
        series_context = "This is Part 1 of a multi-part series."

    research_content = _condense_research(
        research, suggestion, outline=article.outline, title=article.title
    )
    prompt = build_article_prompt(
        research_content=research_content,
        outline=article.outline,
        title=article.title,
        suggestion_point_of_view=suggestion.point_of_view,
        category_name=category.name,
        taxonomy_name=taxonomy.name,
        series_context=series_context,
    )

    message = create_message(
        feature="article_content",
        model=SONNET_MODEL,
        max_tokens=12_000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )

    return first_text(message)


def generate_excerpt_and_summary(
    title: str,
    content: str,
) -> tuple[str, str]:
    """Generate excerpt and AI summary using Haiku. Returns (excerpt, ai_summary)."""
    excerpt_msg = create_message(
        feature="article_excerpt",
        model=HAIKU_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": build_excerpt_prompt(title, content)}],
    )
    excerpt = excerpt_msg.content[0].text.strip()

    summary_msg = create_message(
        feature="article_summary",
        model=HAIKU_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": build_summary_prompt(title, content)}],
    )
    ai_summary = summary_msg.content[0].text.strip()

    return excerpt, ai_summary


# --- Phase 3: Tagging ---


def generate_tags(
    article: Article,
    db: Session,
) -> dict:
    """Assign existing tags and create new PENDING tags. Returns result dict."""
    existing_tags = db.query(Tag).all()
    tag_name_map = {t.name.lower(): t for t in existing_tags}

    prompt = build_tagging_prompt(
        title=article.title,
        content_preview=article.content or "",
        existing_tags=[t.name for t in existing_tags],
    )

    message = create_message(
        feature="article_tags",
        model=HAIKU_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    result = parse_llm_json(message)

    assigned_tag_ids = []
    suggested_new_tags = []

    # Assign existing tags
    for tag_name in result.get("assign", []):
        tag = tag_name_map.get(tag_name.lower())
        if tag and tag not in article.tags:
            article.tags.append(tag)
            assigned_tag_ids.append(tag.id)

    # Create new suggested tags as PENDING
    now = datetime.now(UTC)
    for tag_name in result.get("suggest", []):
        if tag_name.lower() not in tag_name_map:
            new_tag = Tag(
                name=tag_name,
                status="PENDING",
                created_at=now,
                updated_at=now,
            )
            db.add(new_tag)
            db.flush()
            article.tags.append(new_tag)
            assigned_tag_ids.append(new_tag.id)
            suggested_new_tags.append(tag_name)
            tag_name_map[tag_name.lower()] = new_tag

    return {
        "assigned_tag_ids": assigned_tag_ids,
        "suggested_new_tags": suggested_new_tags,
    }
