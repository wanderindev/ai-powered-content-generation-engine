from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TagItem(BaseModel):
    """Tag data for article lists and details."""

    id: int
    name: str  # Will be name_es when lang=es

    model_config = ConfigDict(from_attributes=True)


class ArticleBase(BaseModel):
    id: int
    title: str
    slug: str
    excerpt: str | None = None
    published_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class RelatedArticle(ArticleBase):
    """One entry of ``GET /articles/{id}/related`` (#629), localized by ``lang``."""

    category_name: str | None = None
    feature_image_url: str | None = None
    feature_image_alt: str | None = None


class ArticleListItem(ArticleBase):
    """Article data for list views."""

    taxonomy_name: str
    category_name: str
    category_slug: str
    feature_image_url: str | None = None
    is_part_of_series: bool = False
    tags: list[TagItem] = []
    # Grouping surface for the blog page: consecutive list items sharing a
    # suggestion_id form one group headed by suggestion_title (localized).
    # The list is already sorted category -> suggestion -> research -> series,
    # so groups are contiguous by construction.
    suggestion_id: int | None = None
    suggestion_title: str | None = None
    # True only on the Spanish surface when the article has generated
    # narration (audio_url_es) — the blog card shows a listen badge, the
    # article page the player.
    has_audio: bool = False


class ArticleSeriesInfo(BaseModel):
    """Information about an article's series."""

    series_title: str
    series_slug: str
    total_articles: int
    current_position: int
    articles: list["ArticleBase"]


class ArticleDetail(ArticleBase):
    """Full article data for detail view."""

    content_html: str
    ai_summary: str | None = None
    # Diego-voice narration MP3 — populated only when the article was requested
    # in Spanish (the narration is Spanish-only by design).
    audio_url: str | None = None
    # Total narration length from the segment manifest; None for narrations
    # generated before the manifest existed (the player still shows duration
    # once metadata loads).
    audio_duration_ms: int | None = None
    category_name: str
    category_slug: str
    taxonomy_name: str
    feature_image_url: str | None = None
    feature_image_alt: str | None = None
    feature_image_caption: str | None = None
    series_info: ArticleSeriesInfo | None = None
    tags: list[TagItem] = []
    created_at: datetime
    updated_at: datetime

    # Navigation
    prev_article: ArticleBase | None = None
    next_article: ArticleBase | None = None


class ArticleSearchResult(ArticleBase):
    """Article data for search results."""

    category_name: str
    highlight: str | None = None  # Highlighted search match


class LatestSeriesResponse(BaseModel):
    """Response for latest article series endpoint."""

    series_title: str
    articles: list[ArticleListItem]


# Update forward references
ArticleSeriesInfo.model_rebuild()
