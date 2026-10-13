"""Generate Instagram post drafts from pipeline content.

Two entry points, mirroring the edu-material generation conventions
(condense-first for long research, plain-text line contracts, defensive
parsers, everything PENDING for human review):

* :func:`generate_sabias_que_posts` — research document → 3 "¿Sabías que…?"
  fact-card drafts (Sonnet picks the most surprising facts).
* :func:`generate_article_promo_post` — article series → 1 "Nuevo en el blog"
  announcement draft (template data comes from the DB; the LLM writes only
  the caption).
"""

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.enums import ContentStatus, InstagramPostType, InstagramTemplateVariant
from app.models.instagram_post import InstagramPost
from app.models.research import Research
from app.prompts.instagram_generation import (
    FACT_MAX_CHARS,
    SABIAS_QUE_COUNT,
    build_article_promo_prompt,
    build_ig_condense_prompt,
    build_sabias_que_prompt,
)
from app.services.ig_html import sanitize_fact
from app.services.llm import SONNET_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

# Same threshold as the edu pipeline: past ~2,500 words models drift off format
# instructions, so long research is condensed into a briefing first.
CONDENSE_THRESHOLD = 2500
CONDENSE_MAX_TOKENS = 8_000
SABIAS_QUE_MAX_TOKENS = 8_000
PROMO_MAX_TOKENS = 4_000

# Sabías-que drafts alternate card treatments so a generated batch doesn't
# look repetitive on the grid; the operator can change any of them.
_VARIANT_CYCLE = (
    InstagramTemplateVariant.AZUL.value,
    InstagramTemplateVariant.CLARO.value,
    InstagramTemplateVariant.AZUL.value,
)

_FIELDS = ("TITLE", "FACT", "POLAROID", "IMAGE_QUERY")


def _condense_if_long(research_content: str, suggestion_title: str) -> str:
    """Condense research past the drift threshold into a facts briefing."""
    word_count = len(research_content.split())
    if word_count <= CONDENSE_THRESHOLD:
        return research_content
    logger.info("Condensing %d-word research for sabías-que generation", word_count)
    msg = create_message(
        feature="ig_condense",
        model=SONNET_MODEL,
        max_tokens=CONDENSE_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": build_ig_condense_prompt(research_content, suggestion_title),
            }
        ],
    )
    return first_text(msg).strip()


def _parse_sabias_que_response(text: str) -> list[dict]:
    """Parse the ``POST n / FIELD:`` line format into post dicts.

    Defensive: blocks are split on ``---`` lines; a block yields a post as long
    as it has a non-empty FACT. Missing optional fields fall back to sensible
    defaults; malformed blocks are skipped (the human regenerates if the batch
    comes up short — never invent content).
    """
    posts: list[dict] = []
    for block in text.split("\n---"):
        fields: dict[str, str] = {}
        caption_lines: list[str] | None = None
        for line in block.splitlines():
            stripped = line.strip()
            if caption_lines is not None:
                caption_lines.append(line.rstrip())
                continue
            if stripped.upper().startswith("CAPTION:"):
                remainder = stripped[len("CAPTION:") :].strip()
                caption_lines = [remainder] if remainder else []
                continue
            for field in _FIELDS:
                prefix = f"{field}:"
                if stripped.upper().startswith(prefix):
                    fields[field] = stripped[len(prefix) :].strip()
                    break
        fact = sanitize_fact(fields.get("FACT") or "")
        if not fact:
            continue
        caption = "\n".join(caption_lines).strip() if caption_lines else ""
        posts.append(
            {
                "title": fields.get("TITLE") or fact[:80],
                "fact": fact[: FACT_MAX_CHARS * 2],  # hard backstop; prompt caps at 320
                "polaroid_caption": (fields.get("POLAROID") or "")[:120] or None,
                "image_query": (fields.get("IMAGE_QUERY") or "")[:255] or None,
                "caption": caption or None,
            }
        )
    return posts


def generate_sabias_que_posts(db: Session, research_id: int) -> list[InstagramPost]:
    """Generate 3 sabías-que fact-card drafts from a research document."""
    research = db.query(Research).filter(Research.id == research_id).first()
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if not (research.content or "").strip():
        raise HTTPException(status_code=400, detail="Research document has no content")

    suggestion_title = research.suggestion.title if research.suggestion else "Panama history"
    briefing = _condense_if_long(research.content, suggestion_title)

    msg = create_message(
        feature="ig_sabias_que",
        model=SONNET_MODEL,
        max_tokens=SABIAS_QUE_MAX_TOKENS,
        messages=[{"role": "user", "content": build_sabias_que_prompt(briefing, suggestion_title)}],
    )
    parsed = _parse_sabias_que_response(first_text(msg))
    if not parsed:
        raise HTTPException(
            status_code=502, detail="Could not parse any posts from the LLM response"
        )

    posts = []
    for i, data in enumerate(parsed[:SABIAS_QUE_COUNT]):
        post = InstagramPost(
            post_type=InstagramPostType.SABIAS_QUE.value,
            template_variant=_VARIANT_CYCLE[i % len(_VARIANT_CYCLE)],
            status=ContentStatus.PENDING.value,
            research_id=research.id,
            **data,
        )
        db.add(post)
        posts.append(post)
    db.commit()
    for post in posts:
        db.refresh(post)
    logger.info("Generated %d sabías-que drafts from research id=%s", len(posts), research_id)
    return posts


def series_promo_fields(article: Article) -> dict:
    """Template data for a promo card, derived from the article/series row.

    Spanish titles are preferred (the card is Spanish). Series parents count
    themselves plus their children; a standalone article promotes itself.
    """
    display_title = article.title_es or article.title
    children = sorted(article.series_children or [], key=lambda a: a.series_order or 0)
    if children:
        count = 1 + len(children)
        return {
            "title": display_title,
            "kicker": f"Serie · {count} artículos",
            "meta_text": f"por Diego Raúl Feliu · {count} artículos",
            "article_count": count,
            "article_titles": [display_title] + [c.title_es or c.title for c in children],
        }
    return {
        "title": display_title,
        "kicker": "Historia",
        "meta_text": "por Diego Raúl Feliu",
        "article_count": 1,
        "article_titles": [display_title],
    }


def generate_article_promo_post(db: Session, article_id: int) -> InstagramPost:
    """Generate one "Nuevo en el blog" draft for an article series (or article).

    Only series parents and standalone articles are valid targets — a series
    *member* is promoted through its parent. Template data (title, kicker,
    meta line, background photo) comes from the DB; the LLM writes the caption.
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.series_parent_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Article is part of a series — promote the series parent instead",
        )
    if article.status != ContentStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Only approved articles can be promoted")

    fields = series_promo_fields(article)
    msg = create_message(
        feature="ig_article_promo",
        model=SONNET_MODEL,
        max_tokens=PROMO_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": build_article_promo_prompt(
                    series_title=fields["title"],
                    article_count=fields["article_count"],
                    article_titles=fields["article_titles"],
                    summary=article.excerpt_es or article.excerpt,
                ),
            }
        ],
    )
    text = first_text(msg).strip()
    caption = text.partition("CAPTION:")[2].strip() or text or None

    post = InstagramPost(
        post_type=InstagramPostType.ARTICLE_PROMO.value,
        status=ContentStatus.PENDING.value,
        article_id=article.id,
        title=fields["title"],
        kicker=fields["kicker"],
        meta_text=fields["meta_text"],
        caption=caption,
        media_id=article.feature_image_id,
        media_path=article.feature_image.file_path if article.feature_image else None,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    logger.info("Generated article-promo draft for article id=%s", article_id)
    return post
