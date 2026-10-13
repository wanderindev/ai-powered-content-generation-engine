"""Pydantic schemas for the Instagram social pipeline admin API."""

from datetime import datetime

from pydantic import BaseModel, Field

# Instagram's hard caption limit.
CAPTION_MAX_CHARS = 2200


class InstagramPostListItem(BaseModel):
    id: int
    post_type: str
    template_variant: str | None
    title: str
    status: str
    derived_state: str
    has_image: bool
    media_path: str | None
    image_url: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    publish_error: str | None
    created_at: datetime


class InstagramPostDetail(BaseModel):
    id: int
    post_type: str
    template_variant: str | None
    status: str
    derived_state: str
    research_id: int | None
    article_id: int | None
    title: str
    fact: str | None
    polaroid_caption: str | None
    kicker: str | None
    meta_text: str | None
    caption: str | None
    image_query: str | None
    media_id: int | None
    media_path: str | None
    image_url: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    publish_error: str | None
    publish_attempts: int
    ig_media_id: str | None
    ig_permalink: str | None
    created_at: datetime
    updated_at: datetime


class InstagramPostUpdate(BaseModel):
    """Content edits from the admin drawer. Only provided fields are applied."""

    title: str | None = Field(None, max_length=255)
    fact: str | None = None
    polaroid_caption: str | None = Field(None, max_length=120)
    kicker: str | None = Field(None, max_length=120)
    meta_text: str | None = Field(None, max_length=200)
    caption: str | None = Field(None, max_length=CAPTION_MAX_CHARS)
    template_variant: str | None = None
    image_query: str | None = Field(None, max_length=255)
    media_id: int | None = None
    media_path: str | None = Field(None, max_length=500)
    # Distinguishes "not provided" from "clear the pick": the drawer sends
    # clear_media=True when the operator removes the photo.
    clear_media: bool = False


class InstagramStatusUpdateRequest(BaseModel):
    status: str


class SabiasQueGenerateRequest(BaseModel):
    research_id: int


class ArticlePromoGenerateRequest(BaseModel):
    article_id: int


class PromotableArticle(BaseModel):
    id: int
    title: str
    series_size: int
    published_at: datetime | None


class InstagramRenderResponse(BaseModel):
    id: int
    image_url: str
    # True when a sabías-que card was rendered without a picked photo (the
    # no-polaroid fallback) — the admin surfaces it as a warning, not an error.
    photo_missing: bool


class InstagramScheduleRequest(BaseModel):
    scheduled_at: datetime


class InstagramPublishStateResponse(BaseModel):
    id: int
    status: str
    derived_state: str
    scheduled_at: datetime | None
    published_at: datetime | None
    publish_error: str | None
    ig_permalink: str | None
