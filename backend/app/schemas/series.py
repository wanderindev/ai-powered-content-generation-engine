"""Schemas for article series section generation."""

from pydantic import BaseModel


class SeriesSectionStatus(BaseModel):
    """Status check for series section generation readiness."""

    series_parent_id: int
    suggestion_id: int
    suggestion_title: str
    suggestion_title_es: str | None
    total_articles: int
    translated_articles: int
    can_generate: bool
    message: str


class SeriesSectionResult(BaseModel):
    """Result of series section generation."""

    success: bool
    articles_updated: int
    message: str


class SuggestionTranslationResponse(BaseModel):
    """Response for suggestion title translation."""

    success: bool
    suggestion_id: int
    original: str
    translated: str
