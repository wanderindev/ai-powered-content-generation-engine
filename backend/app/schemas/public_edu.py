"""Schemas for the public educational-deliverables surface (#326)."""

from pydantic import BaseModel


class PublicDeliverableVideo(BaseModel):
    """The narrated pre-visit video for one excursion × grade band."""

    url: str
    thumbnail_url: str | None = None
    duration_ms: int | None = None


class PublicDeliverableBand(BaseModel):
    """One grade band's deliverables: the video and/or the study-guide PDF."""

    grade_band: str | None
    video: PublicDeliverableVideo | None = None
    guide_pdf_url: str | None = None
    # Human-picked cover image of the study guide — shown as the card thumbnail
    # when the band has a guide but no video yet (#327).
    guide_thumbnail_url: str | None = None


class PublicDeliverableExcursion(BaseModel):
    """An excursion with its published deliverables, grouped by grade band."""

    excursion_key: str  # canonical pipeline id (hyphenated)
    public_key: str  # frontend route / i18n key (underscored)
    display_name: str
    display_name_es: str
    bands: list[PublicDeliverableBand]


class PublicDeliverablesResponse(BaseModel):
    excursions: list[PublicDeliverableExcursion]
