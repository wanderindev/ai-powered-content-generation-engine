"""Schemas for educational content pipeline."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_positions(v: list[int] | None) -> list[int] | None:
    """Dedup + sort a 1-based slide-position subset; reject non-positive values.

    Shared by every selective-regeneration request. ``None`` means "whole deck"
    (the original behavior). The authoritative upper-bound check (positions must
    be ≤ the deck's slide count) happens in the endpoint, which knows N.
    """
    if v is None:
        return None
    if not v:
        raise ValueError("positions must be non-empty when provided (omit it for the whole deck)")
    if any(not isinstance(p, int) or isinstance(p, bool) or p < 1 for p in v):
        raise ValueError("positions must be integers ≥ 1")
    return sorted(set(v))


# Each grade band fans out to up to 3 sequential LLM calls (condense → outline →
# content) in one request, so the band list is a per-request cost multiplier.
# Production suggestions carry 3-4 bands; cap well above that to reject only
# pathological input (#92).
MAX_GRADE_BANDS = 6


class EduGuideTranslateResponse(BaseModel):
    """Response after translating a study guide to Spanish (DeepL, #318)."""

    material_id: int
    grade_band: str | None = None
    word_count: int


class ExcursionCoverageItem(BaseModel):
    """One (excursion × grade-band) row of the coverage matrix (#319).

    Each ``*_status`` is None when that deliverable hasn't been produced yet;
    otherwise it is the material's status (PENDING/APPROVED/REJECTED).
    ``video_status`` stays None until the ``video`` material_type lands (#323).
    """

    excursion_key: str
    public_key: str | None = None
    grade_band: str
    has_suggestion: bool = False
    has_research: bool = False
    guide_status: str | None = None
    slides_status: str | None = None
    video_status: str | None = None


class EduSuggestionListItem(BaseModel):
    """Educational suggestion for admin table."""

    id: int
    excursion_key: str | None = None
    title: str
    status: str
    grade_bands: list[str] = []
    has_research: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EduSuggestionDetail(BaseModel):
    """Full edu suggestion for editing."""

    id: int
    excursion_key: str | None = None
    title: str
    description: str | None = None
    grade_bands: list[str] = []
    sub_topics: list[str] = []
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EduResearchListItem(BaseModel):
    """Educational research for admin table."""

    id: int
    suggestion_id: int
    suggestion_title: str
    status: str
    has_content: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EduMaterialListItem(BaseModel):
    """Educational material for admin table."""

    id: int
    suggestion_title: str
    title: str
    material_type: str
    grade_band: str | None = None
    file_url: str | None = None
    thumbnail_url: str | None = None
    status: str
    published_at: datetime | None = None  # set = live on the public delivery surface (#326)
    has_spanish: bool = False  # a Spanish translation exists (hides the translate action)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EduResearchForMaterials(BaseModel):
    """Approved edu research available for material generation."""

    id: int
    suggestion_id: int
    suggestion_title: str
    grade_bands: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class EduMaterialGenerateRequest(BaseModel):
    """Request to generate study guides from research."""

    research_id: int
    force: bool = False


class EduMaterialGenerateResponse(BaseModel):
    """Response after generating edu materials."""

    research_id: int
    suggestion_title: str
    materials_created: int
    grade_bands: list[str]
    material_ids: list[int]
    skipped_bands: list[str] = []
    failed_bands: list[str] = []


class EduMaterialDetail(BaseModel):
    """Full edu material for viewing/editing in EditDrawer."""

    id: int
    suggestion_title: str
    research_id: int | None = None
    blog_research_id: int | None = None
    material_type: str
    grade_band: str | None = None
    title: str
    content_html: str
    content_es_html: str = ""  # Spanish content rendered to HTML (empty if untranslated)
    # Structured guide blocks — the canonical, editable content (None for
    # non-guide materials and legacy markdown-only guides).
    content_blocks: list[dict] | None = None
    content_blocks_es: list[dict] | None = None
    excursion_key: str | None = None
    has_spanish: bool = False
    file_url: str | None = None
    thumbnail_url: str | None = None
    status: str
    published_at: datetime | None = None  # set = live on the public delivery surface (#326)
    created_at: datetime
    # Structured slides — the canonical, editable deck (None for non-slides
    # materials). Same role content_blocks plays for guides.
    slides: list["EduSlideOut"] | None = None
    # The assembled MP4 lives on a SIBLING video-type material (assemble-video
    # upserts on (research_id, "video", grade_band)); surfaced here so the deck
    # editor can link it. None for non-slides materials or before assembly.
    video_material_id: int | None = None
    video_url: str | None = None
    video_duration_ms: int | None = None

    model_config = ConfigDict(from_attributes=True)


class EduMaterialContentUpdate(BaseModel):
    """Admin edit of a guide's title and/or structured content blocks.

    Each field is optional — omitted fields are left untouched. Blocks are
    validated strictly (a block the normalizer would drop rejects the whole
    request) so a UI bug can't silently destroy content.
    """

    title: str | None = None
    content_blocks: list[dict] | None = None
    content_blocks_es: list[dict] | None = None


class EduSuggestionForResearch(BaseModel):
    """Edu suggestion available for research prompt generation."""

    id: int
    title: str
    excursion_key: str | None = None
    grade_bands: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class EduSuggestionUpdateRequest(BaseModel):
    """Request to update an edu suggestion."""

    title: str | None = None
    description: str | None = None
    grade_bands: list[str] | None = Field(default=None, max_length=MAX_GRADE_BANDS)
    sub_topics: list[str] | None = None


class EduResearchPromptRequest(BaseModel):
    """Request to generate a research prompt."""

    suggestion_id: int


class EduResearchPromptResponse(BaseModel):
    """Response with generated research prompt."""

    suggestion_id: int
    suggestion_title: str
    prompt_text: str


class EduResearchUploadResponse(BaseModel):
    """Response after uploading and validating research."""

    suggestion_id: int
    suggestion_title: str
    research_id: int
    word_count: int
    status: str
    references_generated: bool


class EduResearchDetail(BaseModel):
    """Full edu research for viewing/editing."""

    id: int
    suggestion_title: str
    content_html: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EduGuideForSlides(BaseModel):
    """Approved study guide available for slide generation."""

    id: int
    research_id: int | None = None
    suggestion_title: str
    grade_band: str | None = None
    title: str

    model_config = ConfigDict(from_attributes=True)


class EduSlideOut(BaseModel):
    """One slide of a deck, as exposed to the admin editor."""

    id: int
    position: int
    template: str
    title: str | None = None
    body: str | None = None  # newline-separated on-screen lines
    image_query: str | None = None
    media_id: int | None = None
    media_path: str | None = None
    narration: str | None = None
    # Pipeline artifacts (read-only in the editor; server clears them when the
    # content they were built from changes).
    image_url: str | None = None
    audio_url: str | None = None
    duration_ms: int | None = None

    model_config = ConfigDict(from_attributes=True)


# EduMaterialDetail.slides forward-references EduSlideOut (defined below it) —
# resolve it now that both classes exist.
EduMaterialDetail.model_rebuild()


class EduSlideIn(BaseModel):
    """One slide in an admin deck edit (PUT …/slides).

    ``id`` identifies the existing row so its rendered/audio artifacts can be
    carried over when the relevant fields didn't change; omit it for new slides.
    Artifact fields are never accepted from the client.
    """

    id: int | None = None
    template: str
    title: str | None = None
    body: str | None = None
    image_query: str | None = None
    media_id: int | None = None
    media_path: str | None = None
    narration: str | None = None


class EduSlidesUpdate(BaseModel):
    """Admin edit of a slide deck: full ordered slide list (and optional title)."""

    title: str | None = None
    slides: list[EduSlideIn] = Field(..., min_length=1)


class EduNarrationGenerateRequest(BaseModel):
    """Request to write Diego's narration over an approved deck.

    ``positions`` (1-based) narrows the rewrite to a subset of slides; omit it to
    (re)narrate the whole deck. ``force`` still guards a whole-deck rewrite over
    existing narration; a ``positions`` subset is inherently a targeted rewrite.
    """

    force: bool = False
    positions: list[int] | None = None

    _norm_positions = field_validator("positions")(_normalize_positions)


class EduSlidesRenderRequest(BaseModel):
    """Request to render a slide deck's frames (#321). ``positions`` (1-based)
    renders only those slides; omit it to render the whole deck."""

    positions: list[int] | None = None

    _norm_positions = field_validator("positions")(_normalize_positions)


class EduSlidesAudioRequest(BaseModel):
    """Request to synthesize a slide deck's narration audio (#322). ``positions``
    (1-based) re-synthesizes only those slides (bypassing the segment cache so
    edited narration is picked up); omit it to synthesize the whole deck."""

    positions: list[int] | None = None

    _norm_positions = field_validator("positions")(_normalize_positions)


class EduSlidesSubsetRegenerateRequest(BaseModel):
    """Request to re-roll the CONTENT of a subset of an existing deck's slides.

    ``positions`` (1-based) is required and identifies which slides the LLM
    rewrites in place (template/title/body/image_query); the deck's slide count
    is fixed. Whole-deck regeneration stays the ``generate-slides?force`` path.
    """

    positions: list[int] = Field(..., min_length=1)

    _norm_positions = field_validator("positions")(_normalize_positions)


class EduSlidesSubsetRegenerateResponse(BaseModel):
    """Response after re-rolling a subset of a deck's slide content."""

    material_id: int
    suggestion_title: str
    grade_band: str | None = None
    positions: list[int]
    slides_regenerated: int


class EduNarrationGenerateResponse(BaseModel):
    """Response after generating deck narration."""

    material_id: int
    suggestion_title: str
    grade_band: str | None = None
    slides_narrated: int
    total_words: int


class EduSlidesGenerateRequest(BaseModel):
    """Request to generate slides from a study guide."""

    guide_material_id: int
    force: bool = False


class EduSlidesGenerateResponse(BaseModel):
    """Response after generating slides."""

    guide_material_id: int
    suggestion_title: str
    grade_band: str
    slides_material_id: int
    regenerated: bool = True


class EduSlidesRenderResponse(BaseModel):
    """Response after rendering a slides-material's slides to PNG frames (#321)."""

    material_id: int
    suggestion_title: str
    grade_band: str | None = None
    slides_rendered: int
    images_resolved: int
    images_missing: int
    slide_image_urls: list[str]


class EduSlidesAudioResponse(BaseModel):
    """Response after synthesizing per-slide narration audio (#322)."""

    material_id: int
    suggestion_title: str
    grade_band: str | None = None
    slides_narrated: int
    skipped_no_narration: int
    total_duration_ms: int


class EduVideoAssembleResponse(BaseModel):
    """Response after assembling a narrated slide video MP4 (#323)."""

    slides_material_id: int
    video_material_id: int
    suggestion_title: str
    grade_band: str | None = None
    file_url: str
    duration_ms: int
    slide_count: int


class EduGuidePdfResponse(BaseModel):
    """Response after rendering a study-guide PDF (#324)."""

    material_id: int
    suggestion_title: str
    grade_band: str | None = None
    language: str
    file_url: str


class BlogResearchForUpload(BaseModel):
    """Approved blog research available for material upload."""

    id: int
    suggestion_title: str

    model_config = ConfigDict(from_attributes=True)


class MaterialUploadResponse(BaseModel):
    """Response after uploading a material file."""

    id: int
    material_type: str
    suggestion_title: str
    file_url: str
    thumbnail_url: str | None = None


class EduStatusUpdateRequest(BaseModel):
    """Request to update edu entity status."""

    status: Literal["PENDING", "APPROVED", "REJECTED"]


class EduPublishRequest(BaseModel):
    """Request to publish a material (#326). Omit ``published_at`` to publish now;
    pass a future timestamp to schedule."""

    published_at: datetime | None = None


class EduPublishResponse(BaseModel):
    """Result of a publish/unpublish action."""

    id: int
    status: str
    published_at: datetime | None
