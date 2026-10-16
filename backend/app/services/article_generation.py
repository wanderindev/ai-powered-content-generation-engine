"""Article generation service — 3-phase pipeline: outline, write, tag."""

import logging
import re
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
from app.services.llm import (
    HAIKU_MODEL,
    OPUS_MODEL,
    SONNET_MODEL,
    create_message,
    first_text,
    parse_llm_json,
)
from app.utils.markdown import strip_inline_emphasis

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
    # The focus briefing is ~1,800 words (~3k tokens), but adaptive thinking also
    # counts toward max_tokens. This call carries the pipeline's largest input —
    # the FULL research (~14k tokens for a 47k-char doc) — and on Opus 5 thinking
    # scales with input: the outline stage burned 8,296 output tokens on an 18k
    # input. The old 8,000 cap sat at exactly `anthropic_stream_min_tokens` (the
    # streaming threshold), leaving only ~5k of thinking headroom — the PR #407
    # truncation class waiting to happen. 16,000 keeps the call streaming and
    # leaves ~13k of headroom; max_tokens is a ceiling, not a spend, so the
    # unused budget costs nothing.
    msg = create_message(
        feature="article_condense",
        model=OPUS_MODEL,
        max_tokens=16_000,
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

    Outlining was the first stage moved to Opus (issue #415): it fires once per
    research document, sees the most context, and its series-split judgment is
    inherited by every article written against the outline.
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
        model=OPUS_MODEL,
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

    # max_tokens stays at 12,000: measured 2026-07-26, Opus 5 wrote a full
    # 2,399-word article in 4,814 output tokens — 40% of the cap, stop_reason
    # end_turn. The outline stage's heavier thinking does not transfer here
    # because the prompt is small (~6k tokens) and the task is bounded prose
    # against a fixed outline.
    message = create_message(
        feature="article_content",
        model=OPUS_MODEL,
        max_tokens=12_000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    )

    # The prompt forbids bold/italics (#540: emphasis runs are what Grammarly
    # suggestions trip over), but a prompt rule is not a guarantee — same
    # discipline as MAX_ARTICLE_TAGS — so strip whatever the model added anyway.
    return strip_inline_emphasis(first_text(message))


def generate_excerpt_and_summary(
    title: str,
    content: str,
) -> tuple[str, str]:
    """Generate excerpt and AI summary using Haiku. Returns (excerpt, ai_summary)."""
    excerpt_msg = create_message(
        feature="article_excerpt",
        model=HAIKU_MODEL,
        # Haiku 5.5 thinks at medium effort and thinking counts toward the cap
        # (#610): a ~40-word excerpt measured 389 output tokens, past the old 256.
        max_tokens=2000,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": build_excerpt_prompt(title, content)}],
    )
    excerpt = first_text(excerpt_msg).strip()

    summary_msg = create_message(
        feature="article_summary",
        model=HAIKU_MODEL,
        max_tokens=3000,  # was 512 before thinking (#610)
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": build_summary_prompt(title, content)}],
    )
    ai_summary = first_text(summary_msg).strip()

    return excerpt, ai_summary


# --- Phase 3: Tagging ---


# Hard cap on tags per article, counting the ones it already has. Article 228
# collected 25 (mostly one-off PENDING variants of the same subjects) before
# this limit existed; the prompt asks for at most this many, and the code below
# enforces it because a prompt instruction is not a guarantee.
MAX_ARTICLE_TAGS = 5


def _tag_tokens(name: str) -> frozenset[str]:
    """Normalize a tag name to its word set for redundancy comparison."""
    return frozenset(re.findall(r"[a-z0-9]+", name.lower()))


def generate_tags(
    article: Article,
    db: Session,
) -> dict:
    """Assign existing tags and create new PENDING tags. Returns result dict.

    At most ``MAX_ARTICLE_TAGS`` tags end up on the article (counting the ones
    already attached), and overlapping variants of one subject collapse to a
    single tag: candidates are ranked existing-before-new, then shortest first,
    and a candidate whose word set contains (or is contained by) a kept tag's
    word set is dropped — so "Portobelo" wins over "Portobelo Fair" and
    "Portobelo Fair Trade Monopoly".
    """
    existing_tags = db.query(Tag).all()
    tag_name_map = {t.name.lower(): t for t in existing_tags}
    category = db.query(Category).filter(Category.id == article.category_id).first()

    prompt = build_tagging_prompt(
        title=article.title,
        content_preview=article.content or "",
        existing_tags=[t.name for t in existing_tags],
        max_tags=MAX_ARTICLE_TAGS,
        category_name=category.name if category else None,
    )

    # Sonnet, not Haiku: tagging is a judgment call (what's central, what
    # generalizes, which person-name is canonical) and Haiku produced the 25-tag
    # sprawl this pipeline is recovering from. Low effort + headroom so adaptive
    # thinking can't eat the budget on a mechanical task.
    message = create_message(
        feature="article_tags",
        model=SONNET_MODEL,
        max_tokens=2000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )

    result = parse_llm_json(message)

    assigns = [n for n in result.get("assign", []) if n.lower() in tag_name_map]
    # A suggestion is ``{"name", "name_es"}`` (the Spanish name rides along
    # because nothing downstream translates tags — the 2026-09 series shipped
    # 11 English-only tags on the Spanish site); a bare string is tolerated.
    spanish_names: dict[str, str | None] = {}
    for item in result.get("suggest", []):
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str) or not name.strip() or name.lower() in tag_name_map:
            continue
        name_es = item.get("name_es") if isinstance(item, dict) else None
        spanish_names[name] = (
            name_es.strip() if isinstance(name_es, str) and name_es.strip() else None
        )
    suggests = list(spanish_names)
    candidates = [(name, True) for name in assigns] + [(name, False) for name in suggests]
    candidates.sort(key=lambda c: (not c[1], len(_tag_tokens(c[0]))))

    kept_tokens = [_tag_tokens(t.name) for t in article.tags]
    budget = MAX_ARTICLE_TAGS - len(kept_tokens)

    assigned_tag_ids: list[int] = []
    suggested_new_tags: list[str] = []
    now = datetime.now(UTC)
    for name, is_existing in candidates:
        if budget <= 0:
            break
        tokens = _tag_tokens(name)
        if not tokens or any(tokens >= kept or tokens <= kept for kept in kept_tokens):
            continue
        if is_existing:
            tag = tag_name_map[name.lower()]
        else:
            tag = Tag(
                name=name,
                name_es=spanish_names.get(name),
                status="PENDING",
                created_at=now,
                updated_at=now,
            )
            db.add(tag)
            db.flush()
            suggested_new_tags.append(name)
            tag_name_map[name.lower()] = tag
        article.tags.append(tag)
        assigned_tag_ids.append(tag.id)
        kept_tokens.append(tokens)
        budget -= 1

    return {
        "assigned_tag_ids": assigned_tag_ids,
        "suggested_new_tags": suggested_new_tags,
    }
