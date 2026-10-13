from pydantic import BaseModel


class PublicMediaItem(BaseModel):
    id: int
    title: str | None
    alt_text: str | None
    thumbnail_url: str | None
    download_url: str | None
    attribution: str | None
    license: str | None
    license_url: str | None
    source_url: str | None
    source: str
    width: int | None
    height: int | None
    relevance_score: float | None
    tags: list[str]
    description: str | None

    model_config = {"from_attributes": True}


class PublicMediaDetail(PublicMediaItem):
    search_terms: str | None


class PublicMediaListResponse(BaseModel):
    items: list[PublicMediaItem]
    total: int
    page: int
    page_size: int


class PublicMediaStats(BaseModel):
    total: int


class PublicMediaTags(BaseModel):
    tags: list[str]
