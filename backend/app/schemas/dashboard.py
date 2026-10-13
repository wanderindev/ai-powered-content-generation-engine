"""Dashboard schemas for admin panel."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr


class StatusCounts(BaseModel):
    """Counts by status."""

    total: int = 0
    by_status: dict[str, int] = {}


class DashboardStats(BaseModel):
    """Aggregated stats for dashboard homepage."""

    bookings: StatusCounts
    orders: StatusCounts
    suggestions: StatusCounts
    research: StatusCounts
    articles: StatusCounts
    # Instagram posts, bucketed by *derived publication state* (DRAFT / APPROVED /
    # SCHEDULED / PUBLISHING / PUBLISHED / FAILED) — the axis the operator cares
    # about on the home page, not the raw editorial status.
    instagram: StatusCounts
    # Educational pipeline
    edu_suggestions: StatusCounts
    edu_research: StatusCounts
    edu_materials: StatusCounts  # by editorial status
    edu_materials_by_type: StatusCounts  # by material_type (guide/slides/video/…)
    # Deliverables live on the public portal (APPROVED and published_at <= now).
    edu_published_total: int = 0


class SuggestionListItem(BaseModel):
    """Article suggestion for admin table."""

    id: int
    title: str
    title_es: str | None = None
    taxonomy_name: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    status: str
    has_research: bool = False
    articles_status: str = "NONE"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SuggestionDetailAdmin(BaseModel):
    """Full suggestion with all fields for editing."""

    id: int
    title: str
    title_es: str | None = None
    main_topic: str | None = None
    sub_topics: list[str] = []
    point_of_view: str
    status: str
    category_name: str | None = None
    taxonomy_name: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SuggestionUpdateRequest(BaseModel):
    """Request to update suggestion fields."""

    title: str
    title_es: str | None = None
    main_topic: str | None = None
    sub_topics: list[str] = []
    point_of_view: str


class CategoryForGeneration(BaseModel):
    """Category available for suggestion generation."""

    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class SuggestionGenerateRequest(BaseModel):
    """Request to generate article suggestions for a category."""

    category_id: int
    num_suggestions: int = 10


class SuggestionGenerateResponse(BaseModel):
    """Response after generating article suggestions."""

    category_id: int
    category_name: str
    suggestions_created: int
    suggestion_ids: list[int]


class ResearchListItem(BaseModel):
    """Research record for admin table."""

    id: int
    suggestion_id: int
    suggestion_title: str
    category_id: int | None = None
    category_name: str | None = None
    status: str
    has_content: bool = False
    has_summary: bool = False
    articles_status: str = "NONE"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArticleListItem(BaseModel):
    """Article for admin table."""

    id: int
    suggestion_id: int | None = None
    title: str
    suggestion_title: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    status: str
    outline_status: str | None = None
    has_content: bool = False
    has_outline: bool = False
    has_spanish: bool = False
    has_tags: bool = False
    has_feature_image: bool = False
    has_series_sections: bool = False
    is_series: bool = False
    series_parent_id: int | None = None
    published_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TagListItem(BaseModel):
    """Tag for admin lists and autocomplete."""

    id: int
    name: str
    name_es: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ArticleDetailAdmin(BaseModel):
    """Full article with bilingual content for editing."""

    id: int
    title: str
    excerpt: str | None = None
    content_html: str
    outline: str | None = None
    title_es: str | None = None
    excerpt_es: str | None = None
    content_html_es: str | None = None
    status: str
    tags: list[TagListItem] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArticleUpdateRequest(BaseModel):
    """Request to update article content (bilingual)."""

    title: str
    excerpt: str | None = None
    content_html: str
    title_es: str | None = None
    excerpt_es: str | None = None
    content_html_es: str | None = None
    tag_ids: list[int] = []


class TagCreateRequest(BaseModel):
    """Request to create a new tag."""

    name: str


class ResearchDetail(BaseModel):
    """Full research record with HTML content for editing."""

    id: int
    suggestion_title: str
    content_html: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResearchUpdateRequest(BaseModel):
    """Request to update research content."""

    content_html: str


class ResearchPromptRequest(BaseModel):
    """Request to generate a research prompt for a suggestion."""

    suggestion_id: int


class ResearchPromptResponse(BaseModel):
    """Generated research prompt ready for download."""

    suggestion_id: int
    suggestion_title: str
    prompt_text: str


class ResearchUploadResponse(BaseModel):
    """Result of uploading and validating a research document."""

    suggestion_id: int
    suggestion_title: str
    research_id: int
    word_count: int
    status: str
    references_generated: bool = False


class StatusUpdateRequest(BaseModel):
    """Request to update entity status."""

    status: Literal["PENDING", "APPROVED", "REJECTED"]


class StatusUpdateResponse(BaseModel):
    """Response after status update."""

    id: int
    status: str
    updated: bool = True


# --- Settings: Admin Emails ---


class AdminEmailItem(BaseModel):
    """Admin email for settings display."""

    id: int
    email: str
    name: str | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AdminEmailCreateRequest(BaseModel):
    """Request to add a new admin email."""

    email: EmailStr
    name: str | None = None


# --- Settings: Pricing Config ---


class PricingConfigItem(BaseModel):
    """Pricing config key-value pair."""

    key: str
    value: str
    description: str | None

    model_config = ConfigDict(from_attributes=True)


class PricingConfigUpdateRequest(BaseModel):
    """Request to update a single pricing config value."""

    key: str
    value: str


# --- Article Generation Pipeline ---


class OutlineGenerateRequest(BaseModel):
    """Request to generate outlines from research."""

    research_id: int


class OutlineItem(BaseModel):
    """One article outline."""

    article_id: int
    title: str
    outline: str
    outline_status: str | None = None
    summary: str | None = None
    series_order: int | None = None
    is_series_parent: bool = False


class OutlineGenerateResponse(BaseModel):
    """Response after Phase 1 (outline generation)."""

    research_id: int
    articles_created: int
    is_series: bool
    articles: list[OutlineItem]


class FactCheckClaimItem(BaseModel):
    """One checked claim (a fact_check_claims row)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    claim_text: str
    claim_type: str
    verdict: str
    correction: str | None = None
    citation: str | None = None
    checked_via: str | None = None
    checked_at: datetime | None = None


class SubjectFactCheckResponse(BaseModel):
    """Response of the web-search fact-check tier for suggestions/research (#376)."""

    subject_type: str
    subject_id: int
    total_claims: int
    # Verdict -> count (VERIFIED / INACCURATE / DISPUTED / NEEDS_DEEP_RESEARCH /
    # PENDING for claims beyond the per-run search cap).
    counts: dict[str, int]
    claims: list[FactCheckClaimItem]


class FactCheckSummaryItem(BaseModel):
    """Aggregated fact-check state for one subject (dashboard grids, #378)."""

    subject_type: str
    subject_id: int
    counts: dict[str, int]


class CorrectionEditItem(BaseModel):
    """One previewed FIND/REPLACE correction edit for a claim (#387)."""

    claim_id: int
    language: str  # "en" | "es" (es only for ARTICLE subjects)
    find: str
    replace: str
    # ok | not_found | ambiguous | skipped | missing — only ok edits are
    # applicable; the rest explain why a claim can't be corrected yet.
    status: str
    reason: str | None = None


class CorrectionsPreviewResponse(BaseModel):
    """Proposed correction edits for a subject's INACCURATE claims (#387)."""

    subject_type: str
    subject_id: int
    correctable_claims: int
    edits: list[CorrectionEditItem]


class CorrectionEditPayload(BaseModel):
    """One previewed edit echoed back for application (#387)."""

    claim_id: int
    language: str
    find: str
    replace: str


class CorrectionsApplyRequest(BaseModel):
    """The previewed edits to apply — what you preview is what applies."""

    edits: list[CorrectionEditPayload]


class CorrectionFailure(BaseModel):
    """One edit that did not apply, and why (the claim stays INACCURATE)."""

    claim_id: int
    language: str
    reason: str


class CorrectionsApplyResponse(BaseModel):
    """Result of applying correction edits (#387)."""

    subject_type: str
    subject_id: int
    corrected_claim_ids: list[int]
    failed: list[CorrectionFailure]
    # Refreshed verdict counts for the subject, for the grid badge.
    counts: dict[str, int]


class DeepResearchPromptRequest(BaseModel):
    """Request to compile NEEDS_DEEP_RESEARCH claims into a prompt (#377)."""

    # Optional scope: SUGGESTION or RESEARCH; subject_id requires subject_type.
    subject_type: str | None = None
    subject_id: int | None = None


class DeepResearchPromptResponse(BaseModel):
    """One deep-research verification prompt batch (#377)."""

    batch_id: str
    claim_ids: list[int]
    claim_count: int
    # All pending claims matching the scope, including those beyond the
    # per-prompt batch cap — callers loop until this equals claim_count.
    pending_total: int
    prompt_text: str


class DeepResearchVerdictsRequest(BaseModel):
    """A downloaded deep-research verdict document to write back (#377)."""

    content: str
    # The batch's claim ids (echoed from the prompt response). When present,
    # verdicts outside the batch are skipped and unanswered ids reported.
    claim_ids: list[int] | None = None


class SkippedVerdict(BaseModel):
    """One verdict line that was not written, and why."""

    claim_id: int
    reason: str


class DeepResearchVerdictsResponse(BaseModel):
    """Result of processing a deep-research verdict document (#377)."""

    written: int
    skipped: list[SkippedVerdict]
    missing_claim_ids: list[int]
    kb_upserts: int


class ArticleWriteResponse(BaseModel):
    """Response after Phase 2 (article writing)."""

    article_id: int
    title: str
    word_count: int
    excerpt: str | None = None
    ai_summary: str | None = None


class ArticleTagResponse(BaseModel):
    """Response after Phase 3 (tagging)."""

    article_id: int
    assigned_tag_ids: list[int]
    suggested_new_tags: list[str]


class ResearchForOutlineItem(BaseModel):
    """Research available for outline generation."""

    id: int
    suggestion_title: str
    category_name: str | None = None


class ArticleWriteCandidate(BaseModel):
    """Article with approved outline ready for writing."""

    id: int
    title: str
    category_name: str | None = None


class OutlineStatusUpdateRequest(BaseModel):
    """Request to update outline_status."""

    status: Literal["PENDING", "APPROVED", "REJECTED"]


class PublishRequest(BaseModel):
    """Request to publish an article."""

    published_at: datetime


class TranslateResponse(BaseModel):
    """Response after translating an article."""

    article_id: int
    title_es: str
    word_count: int


class SeriesSectionsResponse(BaseModel):
    """Response after generating series sections."""

    success: bool
    message: str
    articles_updated: int


# --- Feature Image ---


class ImagePromptResponse(BaseModel):
    """Generated image prompt for Gemini."""

    article_id: int
    title: str
    prompt_text: str


class ImageUploadResponse(BaseModel):
    """Result of uploading a feature image."""

    article_id: int
    media_id: int
    file_path: str


class AssignMediaRequest(BaseModel):
    """Request to assign a media record as the feature image for an article."""

    media_id: int


# --- Editorial calendar (blog articles + Instagram posts by date) ------------


class EditorialCalendarArticle(BaseModel):
    id: int
    title: str
    published_at: datetime
    # APPROVED and past its publish date — visible on the public site now.
    is_live: bool


class EditorialCalendarInstagramPost(BaseModel):
    id: int
    post_type: str
    title: str
    # published_at when live, otherwise scheduled_at.
    when: datetime
    state: str  # published | scheduled | failed


class EditorialCalendarResponse(BaseModel):
    articles: list[EditorialCalendarArticle]
    instagram_posts: list[EditorialCalendarInstagramPost]
