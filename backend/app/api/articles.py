from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models.article import Article
from app.models.category import Category, Taxonomy
from app.schemas.article import (
    ArticleBase,
    ArticleDetail,
    ArticleListItem,
    ArticleSeriesInfo,
    LatestSeriesResponse,
    TagItem,
)
from app.utils.markdown import markdown_to_html, strip_bio_links
from app.utils.slug import generate_slug

router = APIRouter(prefix="/articles", tags=["articles"])

# Taxonomy ID for Notable Figures (bio articles to exclude)
EXCLUDED_TAXONOMY_ID = 5


@router.get("", response_model=list[ArticleListItem])
def list_articles(
    category: str | None = None,
    taxonomy: str | None = None,
    search: str | None = Query(None, min_length=2, description="Search term to filter articles"),
    lang: str = Query("en", description="Language code (en or es)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    List approved articles with pagination.

    - Excludes bio articles (Notable Figures taxonomy)
    - Can filter by category or taxonomy slug
    - Supports language parameter for bilingual content
    """
    query = (
        db.query(Article)
        .join(Category)
        .join(Taxonomy)
        .options(
            joinedload(Article.category).joinedload(Category.taxonomy),
            joinedload(Article.feature_image),
            joinedload(Article.tags),
        )
        .filter(Article.status == "APPROVED")
        .filter(Article.published_at.isnot(None))
        .filter(Article.published_at <= func.now())
        .filter(Taxonomy.id != EXCLUDED_TAXONOMY_ID)
    )

    if category:
        query = query.filter(Category.name.ilike(f"%{category}%"))

    if taxonomy:
        query = query.filter(Taxonomy.name.ilike(f"%{taxonomy}%"))

    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(
                Article.title.ilike(term),
                Article.title_es.ilike(term),
                Article.excerpt.ilike(term),
                Article.excerpt_es.ilike(term),
                Article.content.ilike(term),
                Article.content_es.ilike(term),
            )
        )

    # Order by published date, then by series order
    query = query.order_by(
        Article.published_at.desc().nullsfirst(),
        Article.series_parent_id.nullsfirst(),
        Article.series_order,
    )

    offset = (page - 1) * limit
    articles = query.offset(offset).limit(limit).all()

    use_spanish = lang == "es"

    return [
        ArticleListItem(
            id=a.id,
            title=a.title_es if use_spanish and a.title_es else a.title,
            slug=generate_slug(a.title_es) if use_spanish and a.title_es else a.slug,
            excerpt=a.excerpt_es if use_spanish and a.excerpt_es else a.excerpt,
            published_at=a.published_at,
            taxonomy_name=a.category.taxonomy.name_es
            if use_spanish and a.category.taxonomy.name_es
            else a.category.taxonomy.name,
            category_name=a.category.name_es
            if use_spanish and a.category.name_es
            else a.category.name,
            category_slug=generate_slug(a.category.name),
            feature_image_url=a.feature_image.file_path if a.feature_image else None,
            is_part_of_series=a.is_part_of_series,
            tags=[
                TagItem(id=t.id, name=t.name_es if use_spanish and t.name_es else t.name)
                for t in a.tags
            ],
        )
        for a in articles
    ]


@router.get("/latest-series", response_model=LatestSeriesResponse)
def get_latest_series(
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """
    Get the latest article series (most recent by published_at).

    Returns the series title and all articles in that series.
    Used for homepage featured articles section.
    """
    # Find the most recently published article that is part of a series
    latest_article = (
        db.query(Article)
        .join(Category)
        .join(Taxonomy)
        .filter(Article.status == "APPROVED")
        .filter(Taxonomy.id != EXCLUDED_TAXONOMY_ID)
        .filter(Article.published_at.isnot(None))
        .filter(
            (Article.series_parent_id.isnot(None))
            | (
                Article.id.in_(
                    db.query(Article.series_parent_id)
                    .filter(Article.series_parent_id.isnot(None))
                    .distinct()
                )
            )
        )
        .order_by(Article.published_at.desc())
        .first()
    )

    if not latest_article:
        # Fallback: return empty response
        return LatestSeriesResponse(series_title="", articles=[])

    # Determine the series parent
    if latest_article.series_parent_id:
        series_parent = latest_article.series_parent
    else:
        series_parent = latest_article

    # Get all articles in this series
    series_articles = (
        db.query(Article)
        .join(Category)
        .join(Taxonomy)
        .options(
            joinedload(Article.category).joinedload(Category.taxonomy),
            joinedload(Article.feature_image),
            joinedload(Article.tags),
        )
        .filter(Article.status == "APPROVED")
        .filter((Article.id == series_parent.id) | (Article.series_parent_id == series_parent.id))
        .order_by(Article.series_order.nullsfirst())
        .all()
    )

    # Use Spanish titles/excerpts if language is Spanish
    use_spanish = lang == "es"

    # Build series title (use suggestion title if available, otherwise parent title)
    if use_spanish and series_parent.title_es:
        series_title = series_parent.title_es
    else:
        series_title = series_parent.title

    return LatestSeriesResponse(
        series_title=series_title,
        articles=[
            ArticleListItem(
                id=a.id,
                title=a.title_es if use_spanish and a.title_es else a.title,
                slug=generate_slug(a.title_es) if use_spanish and a.title_es else a.slug,
                excerpt=a.excerpt_es if use_spanish and a.excerpt_es else a.excerpt,
                published_at=a.published_at,
                taxonomy_name=a.category.taxonomy.name_es
                if use_spanish and a.category.taxonomy.name_es
                else a.category.taxonomy.name,
                category_name=a.category.name_es
                if use_spanish and a.category.name_es
                else a.category.name,
                category_slug=generate_slug(a.category.name),
                feature_image_url=a.feature_image.file_path if a.feature_image else None,
                is_part_of_series=a.is_part_of_series,
                tags=[
                    TagItem(id=t.id, name=t.name_es if use_spanish and t.name_es else t.name)
                    for t in a.tags
                ],
            )
            for a in series_articles
        ],
    )


@router.get("/{article_id}", response_model=ArticleDetail)
def get_article_by_id(
    article_id: int,
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """Get article by ID with full details."""
    article = (
        db.query(Article)
        .options(
            joinedload(Article.category).joinedload(Category.taxonomy),
            joinedload(Article.feature_image),
            joinedload(Article.series_parent),
            joinedload(Article.series_children),
            joinedload(Article.tags),
        )
        .filter(Article.id == article_id)
        .filter(Article.status == "APPROVED")
        .first()
    )

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    return _build_article_detail(article, db, lang)


@router.get("/by-slug/{slug}", response_model=ArticleDetail)
def get_article_by_slug(
    slug: str,
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """Get article by slug with full details."""
    # We need to match the slug against titles
    articles = (
        db.query(Article)
        .options(
            joinedload(Article.category).joinedload(Category.taxonomy),
            joinedload(Article.feature_image),
            joinedload(Article.series_parent),
            joinedload(Article.series_children),
            joinedload(Article.tags),
        )
        .filter(Article.status == "APPROVED")
        .all()
    )

    # Find matching article by computed slug (check both English and Spanish slugs)
    article = None
    for a in articles:
        if a.slug == slug:
            article = a
            break
        # Also check Spanish slug if title_es exists
        if a.title_es and generate_slug(a.title_es) == slug:
            article = a
            break

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    return _build_article_detail(article, db, lang)


def _build_article_detail(article: Article, db: Session, lang: str = "en") -> ArticleDetail:
    """Build ArticleDetail response from Article model."""
    use_spanish = lang == "es"

    # Select content based on language
    if use_spanish and article.content_es:
        content = strip_bio_links(article.content_es)
    else:
        content = strip_bio_links(article.content or "")

    content_html = markdown_to_html(content)

    # Select title/excerpt based on language
    title = article.title_es if use_spanish and article.title_es else article.title
    slug = generate_slug(article.title_es) if use_spanish and article.title_es else article.slug
    excerpt = article.excerpt_es if use_spanish and article.excerpt_es else article.excerpt

    # Build series info if applicable
    series_info = None
    if article.series_parent_id:
        # This article is part of a series
        parent = article.series_parent
        siblings = (
            db.query(Article)
            .filter(Article.series_parent_id == parent.id)
            .filter(Article.status == "APPROVED")
            .order_by(Article.series_order)
            .all()
        )
        all_in_series = [parent] + siblings
        current_pos = next((i + 1 for i, a in enumerate(all_in_series) if a.id == article.id), 1)
        parent_title = parent.title_es if use_spanish and parent.title_es else parent.title
        parent_slug = (
            generate_slug(parent.title_es) if use_spanish and parent.title_es else parent.slug
        )
        series_info = ArticleSeriesInfo(
            series_title=parent_title,
            series_slug=parent_slug,
            total_articles=len(all_in_series),
            current_position=current_pos,
            articles=[
                ArticleBase(
                    id=a.id,
                    title=a.title_es if use_spanish and a.title_es else a.title,
                    slug=generate_slug(a.title_es) if use_spanish and a.title_es else a.slug,
                    excerpt=a.excerpt_es if use_spanish and a.excerpt_es else a.excerpt,
                    published_at=a.published_at,
                )
                for a in all_in_series
            ],
        )
    elif hasattr(article, "series_children") and article.series_children:
        # This article is a series parent
        children = sorted(article.series_children, key=lambda x: x.series_order or 0)
        all_in_series = [article] + children
        series_info = ArticleSeriesInfo(
            series_title=title,
            series_slug=slug,
            total_articles=len(all_in_series),
            current_position=1,
            articles=[
                ArticleBase(
                    id=a.id,
                    title=a.title_es if use_spanish and a.title_es else a.title,
                    slug=generate_slug(a.title_es) if use_spanish and a.title_es else a.slug,
                    excerpt=a.excerpt_es if use_spanish and a.excerpt_es else a.excerpt,
                    published_at=a.published_at,
                )
                for a in all_in_series
            ],
        )

    # Get prev/next articles for navigation
    prev_article = None
    next_article = None
    if series_info:
        articles_list = series_info.articles
        current_idx = series_info.current_position - 1
        if current_idx > 0:
            prev_article = articles_list[current_idx - 1]
        if current_idx < len(articles_list) - 1:
            next_article = articles_list[current_idx + 1]

    # Build tags with bilingual names
    tags = [
        TagItem(id=t.id, name=t.name_es if use_spanish and t.name_es else t.name)
        for t in article.tags
    ]

    return ArticleDetail(
        id=article.id,
        title=title,
        slug=slug,
        excerpt=excerpt,
        published_at=article.published_at,
        content_html=content_html,
        ai_summary=article.ai_summary,
        category_name=article.category.name_es
        if use_spanish and article.category.name_es
        else article.category.name,
        category_slug=generate_slug(article.category.name),
        taxonomy_name=article.category.taxonomy.name_es
        if use_spanish and article.category.taxonomy.name_es
        else article.category.taxonomy.name,
        feature_image_url=article.feature_image.file_path if article.feature_image else None,
        feature_image_alt=article.feature_image.alt_text if article.feature_image else None,
        feature_image_caption=article.feature_image.caption if article.feature_image else None,
        series_info=series_info,
        tags=tags,
        created_at=article.created_at,
        updated_at=article.updated_at,
        prev_article=prev_article,
        next_article=next_article,
    )
