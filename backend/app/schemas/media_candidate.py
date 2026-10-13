"""Pydantic schemas for media library endpoints."""

from datetime import datetime

from pydantic import BaseModel


class MediaCandidateItem(BaseModel):
    """Summary view for listing candidates."""

    id: int
    source: str
    external_id: str
    thumbnail_url: str | None = None
    thumbnail_cdn_url: str | None = None
    title: str
    author: str | None = None
    license: str | None = None
    width: int | None = None
    height: int | None = None
    relevance_score: float | None = None
    status: str
    source_categories: list[str] | None = None
    crawled_at: datetime | None = None

    model_config = {"from_attributes": True}


class MediaCandidateDetail(BaseModel):
    """Full detail view of a single candidate."""

    id: int
    source: str
    external_id: str
    external_url: str
    thumbnail_url: str | None = None
    thumbnail_cdn_url: str | None = None
    page_url: str | None = None
    title: str
    description: str | None = None
    author: str | None = None
    tags: list[str] | None = None
    license: str | None = None
    license_url: str | None = None
    attribution: str | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    file_size: int | None = None
    relevance_score: float | None = None
    relevance_notes: str | None = None
    status: str
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    source_categories: list[str] | None = None
    crawled_at: datetime | None = None
    media_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CandidateListResponse(BaseModel):
    """Paginated list of candidates."""

    items: list[MediaCandidateItem]
    total: int
    page: int
    page_size: int


class CrawlRequest(BaseModel):
    """Request to trigger a Wikimedia crawl."""

    seed_categories: list[str] | None = None
    max_depth: int = 2
    max_per_category: int = 100


class CrawlResponse(BaseModel):
    """Result of a crawl operation."""

    categories_crawled: int
    images_found: int
    images_new: int
    images_skipped: int
    errors: list[str]


class ScoreRequest(BaseModel):
    """Request to trigger LLM relevance scoring."""

    batch_size: int = 30
    max_batches: int | None = None


class ScoreResponse(BaseModel):
    """Result of a scoring operation."""

    candidates_scored: int
    batches_processed: int
    batches_skipped: int = 0


class ReviewRequest(BaseModel):
    """Request to approve or reject a candidate."""

    notes: str | None = None


class MediaImportRequest(BaseModel):
    """Import an external image URL into the media library (picker's
    escape hatch when the crawled catalog has no good photo)."""

    url: str
    title: str | None = None


class LOCCrawlRequest(BaseModel):
    """Request to trigger a Library of Congress crawl."""

    queries: list[dict[str, str]] | None = None
    max_per_query: int = 150


class LOCCrawlResponse(BaseModel):
    """Result of a LOC crawl operation."""

    queries_executed: int
    images_found: int
    images_new: int
    images_skipped: int
    errors: list[str]


class CrawlJobResponse(BaseModel):
    """Status of an async crawl/score job (#277)."""

    id: int
    job_type: str
    status: str
    units_total: int | None = None
    units_done: int = 0
    current_unit: str | None = None
    stats: dict | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class CrawlJobListResponse(BaseModel):
    """Recent crawl/score jobs, newest first."""

    items: list[CrawlJobResponse]


class CatalogStats(BaseModel):
    """Aggregate stats about the media catalog."""

    total: int
    by_source: dict[str, int]
    by_status: dict[str, int]
    scored: int
    unscored: int
    avg_relevance: float | None = None
