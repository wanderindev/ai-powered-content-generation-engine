import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.category import EXCLUDED_TAXONOMY_ID, Category, Taxonomy
from app.models.research import Research
from app.schemas.article import (
    ArticleBase,
    ArticleDetail,
    ArticleListItem,
    ArticleSeriesInfo,
    LatestSeriesResponse,
    RelatedArticle,
    TagItem,
)
from app.services.article_audio import manifest_total_duration_ms
from app.services.knowledge.related import find_related
from app.utils.markdown import markdown_to_html, strip_bio_links
from app.utils.slug import generate_slug

router = APIRouter(prefix="/articles", tags=["articles"])


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

    # Historical chronology, not recency: categories.id follows Panama's era
    # order, and within a category the suggestion id then the research id follow
    # the order the topics were commissioned. articles.category_id is the
    # denormalized copy of the suggestion's category (verified identical in
    # prod), and research.suggestion_id makes the suggestion reachable with a
    # single outer join. Series stay grouped: parent first, then series_order.
    query = query.outerjoin(Research, Article.research_id == Research.id).order_by(
        Article.category_id.asc(),
        Research.suggestion_id.asc().nullslast(),
        Article.research_id.asc().nullslast(),
        Article.series_parent_id.nullsfirst(),
        Article.series_order,
    )

    offset = (page - 1) * limit
    articles = query.offset(offset).limit(limit).all()

    # Batch-load each article's suggestion (research -> suggestion) so the
    # blog can group items under their suggestion title without extra requests.
    research_ids = {a.research_id for a in articles if a.research_id}
    suggestion_by_research: dict[int, ArticleSuggestion] = {}
    if research_ids:
        pairs = (
            db.query(Research.id, ArticleSuggestion)
            .join(ArticleSuggestion, ArticleSuggestion.id == Research.suggestion_id)
            .filter(Research.id.in_(research_ids))
            .all()
        )
        suggestion_by_research = dict(pairs)

    use_spanish = lang == "es"

    items: list[ArticleListItem] = []
    for a in articles:
        suggestion = suggestion_by_research.get(a.research_id)
        items.append(
            ArticleListItem(
                id=a.id,
                title=a.title_es if use_spanish and a.title_es else a.title,
                slug=a.slug_es if use_spanish and a.slug_es else a.slug,
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
                suggestion_id=suggestion.id if suggestion else None,
                suggestion_title=(
                    suggestion.title_es if use_spanish and suggestion.title_es else suggestion.title
                )
                if suggestion
                else None,
                has_audio=bool(use_spanish and a.audio_url_es),
            )
        )
    return items


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
                slug=a.slug_es if use_spanish and a.slug_es else a.slug,
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


# Related articles (#629). Public and unauthenticated, and nothing rate-limits
# inbound requests, so the cost is bounded here: no paid API call (stored chunk
# vectors only), ``k`` clamped to RELATED_MAX_K, an in-process TTL cache per
# worker, and a public Cache-Control header for browsers and any edge cache.
RELATED_DEFAULT_K = 4
RELATED_MAX_K = 6
RELATED_TTL_SECONDS = 600
RELATED_CACHE_MAX_ENTRIES = 1024
_related_cache: dict[tuple[int, str, int], tuple[float, list[RelatedArticle]]] = {}
_related_cache_lock = threading.Lock()


def clear_related_cache() -> None:
    """Drop every cached related-articles list (tests)."""
    with _related_cache_lock:
        _related_cache.clear()


def _related_cache_get(key: tuple[int, str, int]) -> list[RelatedArticle] | None:
    with _related_cache_lock:
        entry = _related_cache.get(key)
        if entry is None:
            return None
        if entry[0] < time.monotonic():
            del _related_cache[key]
            return None
        return entry[1]


def _related_cache_put(key: tuple[int, str, int], value: list[RelatedArticle]) -> None:
    with _related_cache_lock:
        if key not in _related_cache and len(_related_cache) >= RELATED_CACHE_MAX_ENTRIES:
            # Oldest insertion first (dicts keep insertion order).
            del _related_cache[next(iter(_related_cache))]
        _related_cache[key] = (time.monotonic() + RELATED_TTL_SECONDS, value)


@router.get("/by-slug/{slug}", response_model=ArticleDetail)
def get_article_by_slug(
    slug: str,
    lang: str = Query("en", description="Language code (en or es)"),
    db: Session = Depends(get_db),
):
    """Get article by slug with full details."""
    # Slugs are persisted, unique columns (#474), so the match happens in SQL —
    # one indexed statement, no Python scan over every approved article. Both
    # unique constraints carry a btree index, which Postgres BitmapOrs for the
    # two-column predicate.
    article = (
        db.query(Article)
        .options(
            joinedload(Article.category).joinedload(Category.taxonomy),
            joinedload(Article.feature_image),
            joinedload(Article.series_parent),
            joinedload(Article.series_children),
            joinedload(Article.tags),
        )
        .filter(or_(Article.slug == slug, Article.slug_es == slug))
        .filter(Article.status == "APPROVED")
        .first()
    )

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    return _build_article_detail(article, db, lang)


# Registered after /by-slug/{slug} on purpose (#657): declared first, it
# captured GET /by-slug/related as article_id='by-slug' and answered 422.
@router.get("/{article_id}/related", response_model=list[RelatedArticle])
def get_related_articles(
    article_id: int,
    response: Response,
    lang: str = Query("en", description="Language code (en or es)"),
    k: int = Query(RELATED_DEFAULT_K, ge=1, description=f"Results, capped at {RELATED_MAX_K}"),
    db: Session = Depends(get_db),
):
    """Articles similar to this one, from stored chunk embeddings (#629).

    Public visibility on both ends: the source and every result must be
    APPROVED, published (``published_at <= now()``) and outside taxonomy 5.
    Any other id is a 404, so a scheduled article's existence is not revealed.
    The article's own series family is excluded (the page already links it).
    Title / slug / excerpt are in ``lang``, falling back to English.
    """
    use_spanish = lang == "es"
    k = min(k, RELATED_MAX_K)
    key = (article_id, "es" if use_spanish else "en", k)

    items = _related_cache_get(key)
    if items is None:
        hits = find_related(db, article_id, k=k, visibility="public")
        if hits is None:
            raise HTTPException(status_code=404, detail="Article not found")
        ids = [h.article_id for h in hits]
        by_id = {}
        if ids:
            rows = (
                db.query(Article)
                .options(joinedload(Article.category), joinedload(Article.feature_image))
                .filter(Article.id.in_(ids))
                .all()
            )
            by_id = {a.id: a for a in rows}
        items = []
        for article_id_ in ids:
            a = by_id.get(article_id_)
            if a is None:
                continue
            items.append(
                RelatedArticle(
                    id=a.id,
                    title=a.title_es if use_spanish and a.title_es else a.title,
                    slug=a.slug_es if use_spanish and a.slug_es else a.slug,
                    excerpt=a.excerpt_es if use_spanish and a.excerpt_es else a.excerpt,
                    published_at=a.published_at,
                    category_name=a.category.name_es
                    if use_spanish and a.category.name_es
                    else a.category.name,
                    feature_image_url=a.feature_image.file_path if a.feature_image else None,
                    feature_image_alt=a.feature_image.alt_text if a.feature_image else None,
                )
            )
        _related_cache_put(key, items)

    response.headers["Cache-Control"] = f"public, max-age={RELATED_TTL_SECONDS}"
    return items


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
    slug = article.slug_es if use_spanish and article.slug_es else article.slug
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
        parent_slug = parent.slug_es if use_spanish and parent.slug_es else parent.slug
        series_info = ArticleSeriesInfo(
            series_title=parent_title,
            series_slug=parent_slug,
            total_articles=len(all_in_series),
            current_position=current_pos,
            articles=[
                ArticleBase(
                    id=a.id,
                    title=a.title_es if use_spanish and a.title_es else a.title,
                    slug=a.slug_es if use_spanish and a.slug_es else a.slug,
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
                    slug=a.slug_es if use_spanish and a.slug_es else a.slug,
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
        audio_url=article.audio_url_es if use_spanish else None,
        audio_duration_ms=(
            manifest_total_duration_ms(article.audio_manifest_es) if use_spanish else None
        ),
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
