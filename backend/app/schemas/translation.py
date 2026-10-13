"""Schemas for translation API endpoints."""

from pydantic import BaseModel


class TranslationResponse(BaseModel):
    """Response for successful translation."""

    success: bool = True
    field: str  # "title", "excerpt", or "content"
    original: str
    translated: str
    article_id: int


class TranslationError(BaseModel):
    """Response for translation errors."""

    success: bool = False
    error: str
    field: str | None = None
    article_id: int | None = None


class TaxonomyTranslationResponse(BaseModel):
    """Response for taxonomy translation."""

    success: bool = True
    taxonomy_id: int
    name_original: str
    name_translated: str
    description_original: str | None = None
    description_translated: str | None = None


class CategoryTranslationResponse(BaseModel):
    """Response for category translation."""

    success: bool = True
    category_id: int
    name_original: str
    name_translated: str
    description_original: str | None = None
    description_translated: str | None = None


class BulkTranslationResponse(BaseModel):
    """Response for bulk translation operations."""

    success: bool = True
    translated_count: int
    skipped_count: int
    message: str
