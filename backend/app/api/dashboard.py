"""Admin dashboard API endpoints."""

import difflib
import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path as FilePath

import sentry_sdk
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
)
from fastapi.responses import PlainTextResponse
from sqlalchemy import case, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import validate_admin_or_allowlisted_driver_token
from app.core.config import get_settings
from app.core.database import get_db
from app.models import (
    AdminEmail,
    AgentTask,
    Article,
    ArticleSuggestion,
    Booking,
    EduMaterial,
    EduResearch,
    EduSuggestion,
    Media,
    Order,
    PricingConfig,
    Research,
    Tag,
)
from app.models.category import Category, Taxonomy
from app.models.consistency_finding import ConsistencyFinding
from app.models.crawl_job import CrawlJob
from app.models.enums import (
    AgentTaskKind,
    AgentTaskStatus,
    AgentTaskSubject,
    ContentStatus,
    FactCheckVerdict,
    GrammarFixSource,
    Language,
)
from app.models.fact_check import FactCheckClaim, FactCheckSubject
from app.models.grammar_check import GrammarCheckFix
from app.models.grammar_review import GrammarReviewDiff
from app.models.instagram_post import InstagramPost
from app.schemas.consistency import (
    ConsistencyFindingItem,
    ConsistencyFindingList,
    ConsistencyResolveRequest,
)
from app.schemas.dashboard import (
    AdminEmailCreateRequest,
    AdminEmailItem,
    ArticleAudioGenerateRequest,
    ArticleAudioSegmentItem,
    ArticleAudioSegmentsResponse,
    ArticleDetailAdmin,
    ArticleListItem,
    ArticleTagResponse,
    ArticleUpdateRequest,
    ArticleWriteCandidate,
    ArticleWriteResponse,
    AssignMediaRequest,
    CategoryForGeneration,
    CorrectionEditItem,
    CorrectionFailure,
    CorrectionsApplyRequest,
    CorrectionsApplyResponse,
    CorrectionsPreviewResponse,
    DashboardStats,
    DeepResearchPromptRequest,
    DeepResearchPromptResponse,
    DeepResearchVerdictsRequest,
    DeepResearchVerdictsResponse,
    EditorialCalendarArticle,
    EditorialCalendarInstagramPost,
    EditorialCalendarResponse,
    FactCheckSummaryItem,
    ImageUploadResponse,
    OutlineGenerateRequest,
    OutlineGenerateResponse,
    OutlineItem,
    OutlineStatusUpdateRequest,
    PricingConfigItem,
    PricingConfigUpdateRequest,
    PublishRequest,
    ResearchDetail,
    ResearchForOutlineItem,
    ResearchListItem,
    ResearchPromptRequest,
    ResearchPromptResponse,
    ResearchUpdateRequest,
    ResearchUploadResponse,
    SeriesSectionsResponse,
    SkippedVerdict,
    StatusCounts,
    StatusUpdateRequest,
    StatusUpdateResponse,
    SubjectFactCheckResponse,
    SuggestionDetailAdmin,
    SuggestionGenerateRequest,
    SuggestionGenerateResponse,
    SuggestionListItem,
    SuggestionUpdateRequest,
    TagCreateRequest,
    TagListItem,
    TranslateResponse,
)
from app.schemas.grammar_check import (
    GrammarApplyRequest,
    GrammarApplyResponse,
    GrammarReviewDiffItem,
    GrammarReviewDiffList,
    GrammarSourceResponse,
)
from app.schemas.media_candidate import CrawlJobResponse
from app.services import article_publishing, suggestion_dedup
from app.services.article_audio import (
    plan_narration,
    segment_article,
    segment_hash,
)
from app.services.article_generation import (
    create_article_records_from_outlines,
    generate_article_content,
    generate_excerpt_and_summary,
    generate_outlines,
    generate_tags,
)
from app.services.article_narration_job import (
    NarrationJobInFlightError,
    create_narration_job,
    run_article_narration_job,
)
from app.services.article_slugs import SlugCollisionError, sync_article_slugs
from app.services.fact_check import (
    DeepResearchScopeError,
    FactCheckJobInFlightError,
    VerdictDocumentRejected,
    active_fact_check_job,
    create_fact_check_job,
    deep_research_batch,
    run_fact_check_job,
    submit_deep_research_verdicts,
)
from app.services.fact_check_corrections import (
    CorrectionEdit,
    apply_corrections,
    build_preview,
    correctable_claims,
    load_correction_subject,
)
from app.services.grammar_revert import count_snippet
from app.services.grammar_verdicts import run_meaning_check
from app.services.knowledge.claims import schedule_claims_reindex
from app.services.knowledge.indexer import schedule_reindex, schedule_series_reindex
from app.services.research import generate_research_prompt
from app.services.research_common import read_upload_text
from app.services.research_intake import (
    ResearchConflictError,
    SuggestionNotFoundError,
    submit_research,
)
from app.services.series_sections import ABOUT_MARKER_EN, split_series_sections
from app.services.suggestion_generation import generate_historical_suggestions
from app.utils.markdown import html_to_markdown, markdown_to_html

router = APIRouter(
    prefix="/admin/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(validate_admin_or_allowlisted_driver_token)],
)

logger = logging.getLogger(__name__)


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_db),
):
    """Get aggregated stats for dashboard homepage."""
    # Booking stats
    booking_status_counts = dict(
        db.query(Booking.status, func.count(Booking.id)).group_by(Booking.status).all()
    )
    booking_total = sum(booking_status_counts.values())

    # Order stats
    order_status_counts = dict(
        db.query(Order.status, func.count(Order.id)).group_by(Order.status).all()
    )
    order_total = sum(order_status_counts.values())

    # Suggestion stats
    suggestion_status_counts = dict(
        db.query(ArticleSuggestion.status, func.count(ArticleSuggestion.id))
        .group_by(ArticleSuggestion.status)
        .all()
    )
    suggestion_total = sum(suggestion_status_counts.values())

    # Research stats
    research_status_counts = dict(
        db.query(Research.status, func.count(Research.id)).group_by(Research.status).all()
    )
    research_total = sum(research_status_counts.values())

    # Article stats
    article_status_counts = dict(
        db.query(Article.status, func.count(Article.id)).group_by(Article.status).all()
    )
    article_total = sum(article_status_counts.values())

    # Instagram posts, bucketed by derived publication state. The CASE mirrors
    # InstagramPost.derived_state exactly (same precedence order) so the home
    # page and the grid can never disagree about what a post is.
    ig_state = case(
        (InstagramPost.published_at.isnot(None), "PUBLISHED"),
        (InstagramPost.publish_error.isnot(None), "FAILED"),
        (InstagramPost.publishing_started_at.isnot(None), "PUBLISHING"),
        (InstagramPost.status != ContentStatus.APPROVED.value, "DRAFT"),
        (InstagramPost.scheduled_at.isnot(None), "SCHEDULED"),
        else_="APPROVED",
    )
    instagram_state_counts = dict(db.query(ig_state, func.count()).group_by(ig_state).all())
    instagram_total = sum(instagram_state_counts.values())

    # Educational pipeline stats
    edu_suggestion_counts = dict(
        db.query(EduSuggestion.status, func.count(EduSuggestion.id))
        .group_by(EduSuggestion.status)
        .all()
    )
    edu_research_counts = dict(
        db.query(EduResearch.status, func.count(EduResearch.id)).group_by(EduResearch.status).all()
    )
    edu_material_counts = dict(
        db.query(EduMaterial.status, func.count(EduMaterial.id)).group_by(EduMaterial.status).all()
    )
    edu_type_counts = dict(
        db.query(EduMaterial.material_type, func.count(EduMaterial.id))
        .group_by(EduMaterial.material_type)
        .all()
    )
    edu_published_total = (
        db.query(func.count(EduMaterial.id))
        .filter(
            EduMaterial.status == ContentStatus.APPROVED.value,
            EduMaterial.published_at.isnot(None),
            EduMaterial.published_at <= func.now(),
        )
        .scalar()
    )

    return DashboardStats(
        bookings=StatusCounts(total=booking_total, by_status=booking_status_counts),
        orders=StatusCounts(total=order_total, by_status=order_status_counts),
        suggestions=StatusCounts(total=suggestion_total, by_status=suggestion_status_counts),
        research=StatusCounts(total=research_total, by_status=research_status_counts),
        articles=StatusCounts(total=article_total, by_status=article_status_counts),
        instagram=StatusCounts(total=instagram_total, by_status=instagram_state_counts),
        edu_suggestions=StatusCounts(
            total=sum(edu_suggestion_counts.values()), by_status=edu_suggestion_counts
        ),
        edu_research=StatusCounts(
            total=sum(edu_research_counts.values()), by_status=edu_research_counts
        ),
        edu_materials=StatusCounts(
            total=sum(edu_material_counts.values()), by_status=edu_material_counts
        ),
        edu_materials_by_type=StatusCounts(
            total=sum(edu_type_counts.values()), by_status=edu_type_counts
        ),
        edu_published_total=edu_published_total or 0,
    )


def _duplicate_fields(db: Session, suggestions: list[ArticleSuggestion]) -> dict[int, dict]:
    """Per-suggestion possible-duplicate fields (#630), ``{}`` for unflagged rows.

    Read with Core by ``suggestion_dedup.duplicate_fields`` (the columns are off
    the mapper), which degrades to ``{}`` on a database without migration 0045.
    """
    return suggestion_dedup.duplicate_fields(db, [s.id for s in suggestions])


@router.get("/suggestions", response_model=list[SuggestionListItem])
def list_suggestions(
    db: Session = Depends(get_db),
):
    """List article suggestions with related data."""
    suggestions = (
        db.query(ArticleSuggestion)
        .options(joinedload(ArticleSuggestion.category).joinedload(Category.taxonomy))
        .order_by(ArticleSuggestion.created_at.desc())
        .all()
    )

    # Get suggestion IDs that have research
    research_suggestion_ids = set(row[0] for row in db.query(Research.suggestion_id).all())

    # Build suggestion → articles_status mapping
    # For each suggestion, check all articles linked via its research records
    suggestion_articles_status = {}
    research_rows = db.query(Research.suggestion_id, Research.id).all()
    suggestion_research_map = {}
    for sugg_id, res_id in research_rows:
        suggestion_research_map.setdefault(sugg_id, []).append(res_id)

    # Get all articles with their research_id and published_at
    article_rows = (
        db.query(Article.research_id, Article.published_at)
        .filter(Article.research_id.isnot(None))
        .all()
    )
    research_articles = {}
    for res_id, published_at in article_rows:
        research_articles.setdefault(res_id, []).append(published_at)

    for sugg_id, res_ids in suggestion_research_map.items():
        all_articles = []
        for res_id in res_ids:
            all_articles.extend(research_articles.get(res_id, []))
        if not all_articles:
            suggestion_articles_status[sugg_id] = "NONE"
        elif all(pa is not None for pa in all_articles):
            suggestion_articles_status[sugg_id] = "ALL"
        else:
            suggestion_articles_status[sugg_id] = "SOME"

    duplicates = _duplicate_fields(db, suggestions)
    return [
        SuggestionListItem(
            id=s.id,
            title=s.title,
            title_es=s.title_es,
            taxonomy_name=(
                s.category.taxonomy.name if s.category and s.category.taxonomy else None
            ),
            category_id=s.category.id if s.category else None,
            category_name=s.category.name if s.category else None,
            status=s.status,
            has_research=s.id in research_suggestion_ids,
            articles_status=suggestion_articles_status.get(s.id, "NONE"),
            created_at=s.created_at,
            **duplicates.get(s.id, {}),
        )
        for s in suggestions
    ]


@router.get(
    "/suggestions/available-for-research",
    response_model=list[SuggestionListItem],
)
def get_suggestions_for_research(
    db: Session = Depends(get_db),
):
    """Return approved suggestions that have no research record yet."""
    research_suggestion_ids = set(row[0] for row in db.query(Research.suggestion_id).all())

    suggestions = (
        db.query(ArticleSuggestion)
        .options(joinedload(ArticleSuggestion.category).joinedload(Category.taxonomy))
        .filter(ArticleSuggestion.status == "APPROVED")
        .order_by(ArticleSuggestion.created_at.desc())
        .all()
    )

    return [
        SuggestionListItem(
            id=s.id,
            title=s.title,
            title_es=s.title_es,
            taxonomy_name=(
                s.category.taxonomy.name if s.category and s.category.taxonomy else None
            ),
            category_name=s.category.name if s.category else None,
            status=s.status,
            has_research=False,
            has_article=False,
            created_at=s.created_at,
        )
        for s in suggestions
        if s.id not in research_suggestion_ids
    ]


@router.post(
    "/research/generate-prompt",
    response_model=ResearchPromptResponse,
)
def generate_prompt(
    body: ResearchPromptRequest,
    db: Session = Depends(get_db),
):
    """Generate a tailored research prompt for a suggestion via Sonnet."""
    suggestion = (
        db.query(ArticleSuggestion)
        .options(joinedload(ArticleSuggestion.category).joinedload(Category.taxonomy))
        .filter(ArticleSuggestion.id == body.suggestion_id)
        .first()
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Suggestion must be APPROVED")

    existing = db.query(Research).filter(Research.suggestion_id == suggestion.id).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="This suggestion already has a research record",
        )

    prompt_text = generate_research_prompt(
        suggestion, suggestion.category, suggestion.category.taxonomy
    )

    return ResearchPromptResponse(
        suggestion_id=suggestion.id,
        suggestion_title=suggestion.title,
        prompt_text=prompt_text,
    )


@router.post(
    "/research/upload",
    response_model=ResearchUploadResponse,
)
async def upload_research(
    file: UploadFile,
    suggestion_id: int = Form(..., description="Suggestion this research is for"),
    force: bool = Query(False, description="Overwrite even if existing research is APPROVED"),
    db: Session = Depends(get_db),
):
    """Upload a research document, validate it, and store in DB.

    Runs the shared intake path (``services.research_intake.submit_research``,
    also behind the MCP ``submit_research`` tool, #641) with ``replace=True``
    and ``converge_identical=False``: existing research is overwritten and
    reset to PENDING, identical body or not, and APPROVED research needs
    ``force`` (#88).
    """
    content = await read_upload_text(file)

    try:
        outcome = submit_research(
            db, suggestion_id, content, replace=True, force=force, converge_identical=False
        )
    except SuggestionNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None
    except ResearchConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from None
    if not outcome.accepted:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Research document failed quality checks",
                "checks": outcome.checks,
                "word_count": outcome.word_count,
            },
        )

    return ResearchUploadResponse(
        suggestion_id=outcome.suggestion_id,
        suggestion_title=outcome.suggestion_title,
        research_id=outcome.research_id,
        word_count=outcome.word_count,
        status=outcome.status,
        references_generated=outcome.references_generated,
    )


@router.get(
    "/suggestions/available-for-generation",
    response_model=list[CategoryForGeneration],
)
def get_categories_for_generation(
    db: Session = Depends(get_db),
):
    """Get Historical Panama categories that have no suggestions yet."""
    # Subquery: category IDs that already have suggestions
    has_suggestions = db.query(ArticleSuggestion.category_id).distinct().subquery()

    categories = (
        db.query(Category)
        .join(Taxonomy, Category.taxonomy_id == Taxonomy.id)
        .filter(Taxonomy.name == "Historical Panama")
        .filter(~Category.id.in_(db.query(has_suggestions.c.category_id)))
        .order_by(Category.id)
        .all()
    )

    return [CategoryForGeneration(id=c.id, name=c.name) for c in categories]


@router.post("/suggestions/generate", response_model=SuggestionGenerateResponse)
def generate_suggestions(
    body: SuggestionGenerateRequest,
    db: Session = Depends(get_db),
):
    """Generate article suggestions for a Historical Panama category using AI."""
    if not 1 <= body.num_suggestions <= 20:
        raise HTTPException(status_code=400, detail="num_suggestions must be between 1 and 20")

    category = (
        db.query(Category)
        .options(joinedload(Category.taxonomy))
        .filter(Category.id == body.category_id)
        .first()
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    taxonomy = category.taxonomy
    if taxonomy.name != "Historical Panama":
        raise HTTPException(
            status_code=400,
            detail="Suggestion generation is only supported for Historical Panama categories",
        )

    suggestions = generate_historical_suggestions(
        category=category,
        taxonomy=taxonomy,
        num_suggestions=body.num_suggestions,
        db=db,
    )

    return SuggestionGenerateResponse(
        category_id=category.id,
        category_name=category.name,
        suggestions_created=len(suggestions),
        suggestion_ids=[s.id for s in suggestions],
    )


@router.get("/suggestions/{suggestion_id}", response_model=SuggestionDetailAdmin)
def get_suggestion_detail(
    suggestion_id: int = Path(..., description="Suggestion ID"),
    db: Session = Depends(get_db),
):
    """Get a single suggestion with all editable fields."""
    suggestion = (
        db.query(ArticleSuggestion)
        .options(joinedload(ArticleSuggestion.category).joinedload(Category.taxonomy))
        .filter(ArticleSuggestion.id == suggestion_id)
        .first()
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    return SuggestionDetailAdmin(
        id=suggestion.id,
        title=suggestion.title,
        title_es=suggestion.title_es,
        main_topic=suggestion.main_topic,
        sub_topics=suggestion.sub_topics or [],
        point_of_view=suggestion.point_of_view,
        status=suggestion.status,
        category_name=suggestion.category.name if suggestion.category else None,
        taxonomy_name=(
            suggestion.category.taxonomy.name
            if suggestion.category and suggestion.category.taxonomy
            else None
        ),
        created_at=suggestion.created_at,
        **_duplicate_fields(db, [suggestion]).get(suggestion.id, {}),
    )


@router.put("/suggestions/{suggestion_id}", response_model=SuggestionDetailAdmin)
def update_suggestion(
    suggestion_id: int = Path(..., description="Suggestion ID"),
    body: SuggestionUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update suggestion fields."""
    suggestion = (
        db.query(ArticleSuggestion)
        .options(joinedload(ArticleSuggestion.category).joinedload(Category.taxonomy))
        .filter(ArticleSuggestion.id == suggestion_id)
        .first()
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    # A changed title / main topic makes the stored dedup vector and duplicate
    # flag (#630) stale: clear both so the next flag-on run re-embeds it.
    if body.title != suggestion.title or body.main_topic != suggestion.main_topic:
        suggestion_dedup.clear_embedding(db, suggestion.id)
    suggestion.title = body.title
    suggestion.title_es = body.title_es
    suggestion.main_topic = body.main_topic
    suggestion.sub_topics = body.sub_topics
    suggestion.point_of_view = body.point_of_view
    db.commit()
    db.refresh(suggestion)

    return SuggestionDetailAdmin(
        id=suggestion.id,
        title=suggestion.title,
        title_es=suggestion.title_es,
        main_topic=suggestion.main_topic,
        sub_topics=suggestion.sub_topics or [],
        point_of_view=suggestion.point_of_view,
        status=suggestion.status,
        category_name=suggestion.category.name if suggestion.category else None,
        taxonomy_name=(
            suggestion.category.taxonomy.name
            if suggestion.category and suggestion.category.taxonomy
            else None
        ),
        created_at=suggestion.created_at,
        **_duplicate_fields(db, [suggestion]).get(suggestion.id, {}),
    )


def _kickoff_fact_check(
    db: Session,
    background_tasks: BackgroundTasks,
    subject_type: str,
    subject_id: int,
) -> CrawlJob:
    """Create a fact-check background job for one subject and schedule it (#384).

    Research-sized runs exceed the proxy timeout, so all fact-check runs execute
    off the request path (the #277 crawl_jobs pattern) and the endpoints return
    202 + the job id; poll GET /media-library/jobs/{id}. A second kickoff for
    the SAME subject while one is in flight joins the running job (202 with
    the existing id) instead of 409ing — a re-run replaces the subject's rows,
    so overlapping runs would corrupt each other, and the agent driver races
    the write endpoint's auto-kicked check (the 2026-09-02 drive burned its
    whole retry budget on back-to-back 409s within one second).

    Suggestions and research run the extract-only external tier (#389);
    articles run the grounding tier.
    """
    try:
        job = create_fact_check_job(db, subject_type, subject_id)
    except FactCheckJobInFlightError as exc:
        logger.info(
            "Fact-check for %s %d joins in-flight job %d", subject_type, subject_id, exc.job.id
        )
        return exc.job
    background_tasks.add_task(run_fact_check_job, job.id)
    logger.info("Kicked off fact-check job %d for %s %d", job.id, subject_type, subject_id)
    return job


@router.post(
    "/suggestions/{suggestion_id}/fact-check",
    response_model=CrawlJobResponse,
    status_code=202,
)
def fact_check_suggestion(
    background_tasks: BackgroundTasks,
    suggestion_id: int = Path(..., description="Suggestion ID"),
    db: Session = Depends(get_db),
):
    """External fact-check as a background job (#384).

    Extracts atomic claims from the suggestion's topic/sub-topics/point-of-view.
    The extract-only tier (#389) resolves them via the verified KB and queues
    the rest as NEEDS_DEEP_RESEARCH for the free deep-research loop (#377).
    Returns 202 + a job id; poll GET /media-library/jobs/{id}. Re-running
    replaces the previous rows.
    """
    suggestion = db.query(ArticleSuggestion).filter(ArticleSuggestion.id == suggestion_id).first()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    return _kickoff_fact_check(
        db, background_tasks, FactCheckSubject.SUGGESTION.value, suggestion.id
    )


@router.get("/research", response_model=list[ResearchListItem])
def list_research(
    db: Session = Depends(get_db),
):
    """List research records with related data."""
    research_items = (
        db.query(Research)
        .options(joinedload(Research.suggestion).joinedload(ArticleSuggestion.category))
        .order_by(Research.created_at.desc())
        .all()
    )

    # Build research → articles_status mapping
    article_rows = (
        db.query(Article.research_id, Article.published_at)
        .filter(Article.research_id.isnot(None))
        .all()
    )
    research_articles = {}
    for res_id, published_at in article_rows:
        research_articles.setdefault(res_id, []).append(published_at)

    def _articles_status(research_id):
        articles = research_articles.get(research_id, [])
        if not articles:
            return "NONE"
        if all(pa is not None for pa in articles):
            return "ALL"
        return "SOME"

    return [
        ResearchListItem(
            id=r.id,
            suggestion_id=r.suggestion_id,
            suggestion_title=r.suggestion.title if r.suggestion else "Unknown",
            category_id=r.suggestion.category.id
            if r.suggestion and r.suggestion.category
            else None,
            category_name=r.suggestion.category.name
            if r.suggestion and r.suggestion.category
            else None,
            status=r.status,
            has_content=bool(r.content and r.content.strip()),
            has_summary=bool(r.summary and r.summary.strip()),
            articles_status=_articles_status(r.id),
            created_at=r.created_at,
        )
        for r in research_items
    ]


@router.get(
    "/research/available-for-outlines",
    response_model=list[ResearchForOutlineItem],
)
def get_research_for_outlines(
    db: Session = Depends(get_db),
):
    """Return only the next approved research (by suggestion order) that has no articles yet."""
    research_ids_with_articles = set(
        row[0]
        for row in db.query(Article.research_id)
        .filter(Article.research_id.isnot(None))
        .distinct()
        .all()
    )

    # Order by suggestion_id (chronological order of suggestions)
    research_items = (
        db.query(Research)
        .options(joinedload(Research.suggestion).joinedload(ArticleSuggestion.category))
        .filter(Research.status == "APPROVED")
        .order_by(Research.suggestion_id.asc())
        .all()
    )

    # Return only the first (next) research with no articles
    result = []
    for r in research_items:
        if r.id not in research_ids_with_articles:
            result.append(r)
            break

    return [
        ResearchForOutlineItem(
            id=r.id,
            suggestion_title=r.suggestion.title if r.suggestion else "Unknown",
            category_name=(
                r.suggestion.category.name if r.suggestion and r.suggestion.category else None
            ),
        )
        for r in result
    ]


@router.get("/research/{research_id}", response_model=ResearchDetail)
def get_research_detail(
    research_id: int = Path(..., description="Research ID"),
    db: Session = Depends(get_db),
):
    """Get a single research record with content converted to HTML."""
    research = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")

    return ResearchDetail(
        id=research.id,
        suggestion_title=research.suggestion.title if research.suggestion else "Unknown",
        content_html=markdown_to_html(research.content or ""),
        status=research.status,
        created_at=research.created_at,
    )


@router.put("/research/{research_id}", response_model=ResearchDetail)
def update_research(
    research_id: int = Path(..., description="Research ID"),
    body: ResearchUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update research content (HTML in, stored as Markdown)."""
    research = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")

    research.content = html_to_markdown(body.content_html)
    db.commit()
    db.refresh(research)

    return ResearchDetail(
        id=research.id,
        suggestion_title=research.suggestion.title if research.suggestion else "Unknown",
        content_html=markdown_to_html(research.content or ""),
        status=research.status,
        created_at=research.created_at,
    )


@router.post(
    "/research/{research_id}/fact-check",
    response_model=CrawlJobResponse,
    status_code=202,
)
def fact_check_research(
    background_tasks: BackgroundTasks,
    research_id: int = Path(..., description="Research ID"),
    db: Session = Depends(get_db),
):
    """External fact-check as a background job (#384).

    The extract-only tier (#389) extracts the doc's claims, resolves what the
    verified KB already knows, and queues the rest as NEEDS_DEEP_RESEARCH for
    the free deep-research loop (#377). Returns 202 + a job id; poll
    GET /media-library/jobs/{id}.
    """
    research = db.query(Research).filter(Research.id == research_id).first()
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if not research.content or not research.content.strip():
        raise HTTPException(status_code=400, detail="Research has no content")

    return _kickoff_fact_check(db, background_tasks, FactCheckSubject.RESEARCH.value, research.id)


@router.get("/research/{research_id}/download")
def download_research(
    research_id: int = Path(..., description="Research ID"),
    db: Session = Depends(get_db),
):
    """Download research content as markdown."""
    research = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")

    from unidecode import unidecode

    suggestion = research.suggestion
    suggestion_id = suggestion.id if suggestion else 0
    title_slug = unidecode(suggestion.title if suggestion else "unknown").replace(" ", "_")
    filename = f"{suggestion_id}_{title_slug}_research.md"

    return PlainTextResponse(
        content=research.content or "",
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/research/{research_id}/generate-summary")
def generate_summary(
    research_id: int = Path(..., description="Research ID"),
    db: Session = Depends(get_db),
):
    """Generate an infographic-friendly summary from research content."""
    research = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if research.status != "APPROVED":
        raise HTTPException(
            status_code=400, detail="Research must be approved before generating summary"
        )
    if not research.content or not research.content.strip():
        raise HTTPException(status_code=400, detail="Research has no content")

    from app.services.research_summary import generate_research_summary

    try:
        summary = generate_research_summary(research.content)
    except Exception as exc:
        # This is a caught-and-rethrown HTTPException, so Sentry's FastAPI
        # integration won't report it on its own — capture explicitly. Without
        # this, a recurring LLM failure (e.g. truncation) is invisible: the only
        # signal is an admin-side error toast the operator may never see.
        logger.exception("Research summary generation failed for research_id=%s", research_id)
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=502, detail=f"Summary generation failed: {exc}")

    research.summary = summary
    db.commit()

    return {"id": research.id, "summary_length": len(summary)}


@router.get("/research/{research_id}/download-summary")
def download_research_summary(
    research_id: int = Path(..., description="Research ID"),
    db: Session = Depends(get_db),
):
    """Download research summary as markdown."""
    research = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if not research.summary:
        raise HTTPException(status_code=404, detail="No summary available")

    from unidecode import unidecode

    suggestion = research.suggestion
    suggestion_id = suggestion.id if suggestion else 0
    title_slug = unidecode(suggestion.title if suggestion else "unknown").replace(" ", "_")
    filename = f"{suggestion_id}_{title_slug}_summary.md"

    return PlainTextResponse(
        content=research.summary,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Article Generation Pipeline ---


@router.post(
    "/articles/generate-outlines",
    response_model=OutlineGenerateResponse,
)
def generate_article_outlines(
    body: OutlineGenerateRequest,
    db: Session = Depends(get_db),
):
    """Phase 1: Generate article outline(s) from approved research.

    Idempotent: when articles already exist for the research, they are
    returned with ``articles_created=0`` instead of a 400. The agent driver
    can lose the response after the server has committed (the 2026-09-01
    drive timed out client-side mid-call), and the replayed task must
    converge rather than park the whole week as FAILED.
    """
    research = (
        db.query(Research)
        .options(
            joinedload(Research.suggestion)
            .joinedload(ArticleSuggestion.category)
            .joinedload(Category.taxonomy)
        )
        .filter(Research.id == body.research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if research.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Research must be APPROVED")

    existing_articles = (
        db.query(Article).filter(Article.research_id == research.id).order_by(Article.id).all()
    )
    if existing_articles:
        is_series = len(existing_articles) > 1
        return OutlineGenerateResponse(
            research_id=research.id,
            articles_created=0,
            is_series=is_series,
            articles=[
                OutlineItem(
                    article_id=a.id,
                    title=a.title,
                    outline=a.outline or "",
                    outline_status=a.outline_status,
                    summary=a.ai_summary,
                    series_order=a.series_order,
                    is_series_parent=(is_series and a.series_parent_id is None),
                )
                for a in existing_articles
            ],
        )

    suggestion = research.suggestion
    category = suggestion.category
    taxonomy = category.taxonomy

    outlines = generate_outlines(research, suggestion, category, taxonomy)
    articles = create_article_records_from_outlines(outlines, research, category.id, db)
    db.commit()

    is_series = len(articles) > 1
    return OutlineGenerateResponse(
        research_id=research.id,
        articles_created=len(articles),
        is_series=is_series,
        articles=[
            OutlineItem(
                article_id=a.id,
                title=a.title,
                outline=a.outline,
                outline_status=a.outline_status,
                summary=a.ai_summary,
                series_order=a.series_order,
                is_series_parent=(is_series and a.series_parent_id is None),
            )
            for a in articles
        ],
    )


@router.post(
    "/articles/{article_id}/write",
    response_model=ArticleWriteResponse,
)
def write_article(
    background_tasks: BackgroundTasks,
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Phase 2: Generate full article content from outline."""
    article = (
        db.query(Article)
        .options(joinedload(Article.series_parent))
        .filter(Article.id == article_id)
        .first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.outline:
        raise HTTPException(status_code=400, detail="Article has no outline")
    if article.outline_status != "APPROVED":
        raise HTTPException(
            status_code=400, detail="Article outline must be APPROVED before writing"
        )
    if article.content and article.content.strip():
        raise HTTPException(status_code=400, detail="Article already has content")

    # Load the research -> suggestion -> category -> taxonomy chain
    research = db.query(Research).filter(Research.id == article.research_id).first()
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")

    suggestion = (
        db.query(ArticleSuggestion)
        .options(joinedload(ArticleSuggestion.category).joinedload(Category.taxonomy))
        .filter(ArticleSuggestion.id == research.suggestion_id)
        .first()
    )
    category = suggestion.category
    taxonomy = category.taxonomy

    content = generate_article_content(article, research, suggestion, category, taxonomy)
    excerpt, ai_summary = generate_excerpt_and_summary(article.title, content)

    article.content = content
    article.excerpt = excerpt
    article.ai_summary = ai_summary
    db.commit()

    # Enqueue the grounding check (#375) as a background job (#384) — off the
    # request path, non-fatal: the write must succeed even if kickoff fails.
    try:
        _kickoff_fact_check(db, background_tasks, FactCheckSubject.ARTICLE.value, article.id)
    except HTTPException:
        pass  # a fact-check for this article is already running
    except Exception:
        logger.exception("Failed to enqueue grounding fact-check for article %d", article.id)
        db.rollback()

    # After the kickoff: BackgroundTasks run in order, so grounding must not wait on embedding (#649).
    schedule_reindex(background_tasks, article.id)

    return ArticleWriteResponse(
        article_id=article.id,
        title=article.title,
        word_count=len(content.split()),
        excerpt=excerpt,
        ai_summary=ai_summary,
    )


@router.get("/fact-check/summary", response_model=list[FactCheckSummaryItem])
def fact_check_summary(
    db: Session = Depends(get_db),
):
    """Aggregated fact-check state per subject, for the dashboard grids (#378).

    One item per (subject_type, subject_id) that has claims, with per-verdict
    counts. Grids join this onto their rows to render the status indicator.
    """
    rows = (
        db.query(
            FactCheckClaim.subject_type,
            FactCheckClaim.subject_id,
            FactCheckClaim.verdict,
            func.count(FactCheckClaim.id),
        )
        .group_by(FactCheckClaim.subject_type, FactCheckClaim.subject_id, FactCheckClaim.verdict)
        .all()
    )
    aggregated: dict[tuple[str, int], dict[str, int]] = {}
    for subject_type, subject_id, verdict, count in rows:
        aggregated.setdefault((subject_type, subject_id), {})[verdict] = count
    return [
        FactCheckSummaryItem(subject_type=key[0], subject_id=key[1], counts=counts)
        for key, counts in sorted(aggregated.items())
    ]


@router.get(
    "/fact-check/{subject_type}/{subject_id}",
    response_model=SubjectFactCheckResponse,
)
def get_fact_check_claims(
    subject_type: str = Path(..., description="A FactCheckSubject value"),
    subject_id: int = Path(..., description="Subject ID"),
    db: Session = Depends(get_db),
):
    """Claim-level drill-in for one subject (#378): every checked claim with
    its verdict, correction, and citation. Subject-agnostic — it accepts any
    ``FactCheckSubject`` value, including the edu subjects (#460)."""
    normalized = subject_type.upper()
    if normalized not in FactCheckSubject.values():
        raise HTTPException(
            status_code=400,
            detail=f"subject_type must be one of {', '.join(FactCheckSubject.values())}",
        )

    claims = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == normalized,
            FactCheckClaim.subject_id == subject_id,
        )
        .order_by(FactCheckClaim.id)
        .all()
    )
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim.verdict] = counts.get(claim.verdict, 0) + 1

    return SubjectFactCheckResponse(
        subject_type=normalized,
        subject_id=subject_id,
        total_claims=len(claims),
        counts=counts,
        claims=claims,
    )


def _subject_verdict_counts(db: Session, normalized: str, subject_id: int) -> dict[str, int]:
    """Refreshed per-verdict claim counts for a subject (the grid badge)."""
    return {
        verdict: count
        for verdict, count in (
            db.query(FactCheckClaim.verdict, func.count(FactCheckClaim.id))
            .filter(
                FactCheckClaim.subject_type == normalized,
                FactCheckClaim.subject_id == subject_id,
            )
            .group_by(FactCheckClaim.verdict)
            .all()
        )
    }


def _correction_subject_or_error(subject_type: str, subject_id: int, db: Session):
    """Shared preview/apply guards (#387): valid subject_type, subject exists,
    no fact-check job in flight (a run wholesale-replaces the claim rows)."""
    normalized = subject_type.upper()
    if normalized not in FactCheckSubject.values():
        raise HTTPException(
            status_code=400,
            detail=f"subject_type must be one of {', '.join(FactCheckSubject.values())}",
        )
    subject = load_correction_subject(normalized, subject_id, db)
    if subject is None:
        raise HTTPException(status_code=404, detail=f"{normalized.title()} not found")
    if active_fact_check_job(db, normalized, subject_id) is not None:
        raise HTTPException(status_code=409, detail="A fact-check job is running for this item")
    return normalized, subject


@router.post(
    "/fact-check/{subject_type}/{subject_id}/corrections/preview",
    response_model=CorrectionsPreviewResponse,
)
def preview_fact_check_corrections(
    subject_type: str = Path(..., description="SUGGESTION, RESEARCH, or ARTICLE"),
    subject_id: int = Path(..., description="Subject ID"),
    db: Session = Depends(get_db),
):
    """Generate targeted FIND/REPLACE edits for the subject's INACCURATE
    claims (#387). Stateless — nothing is persisted; pass the returned edits
    verbatim to the apply endpoint. For articles, English and Spanish edit
    sets are generated in the same preview.
    """
    normalized, subject = _correction_subject_or_error(subject_type, subject_id, db)
    claims = correctable_claims(normalized, subject_id, db)
    if not claims:
        # Verdicts still in flight is a real "try later / park for a human"
        # failure. A resolved-clean check is a SUCCESS with nothing to apply,
        # and this is the only place that clean case is decided: the planner's
        # plan-time "no correctable claims" skip (#481) is retired (articles in
        # #555, research in #554), so every corrections row is planned and
        # reaches this preview. (The 2026-09-03 drive burned its retry budget
        # when this path still 400ed a clean article.)
        unresolved = (
            db.query(FactCheckClaim.id)
            .filter(
                FactCheckClaim.subject_type == normalized,
                FactCheckClaim.subject_id == subject_id,
                FactCheckClaim.verdict.in_(
                    [
                        FactCheckVerdict.PENDING.value,
                        FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
                    ]
                ),
            )
            .count()
        )
        if unresolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Fact-check verdicts still pending ({unresolved} unresolved claims); "
                    "nothing to apply yet"
                ),
            )
        checked = (
            db.query(FactCheckClaim.id)
            .filter(
                FactCheckClaim.subject_type == normalized,
                FactCheckClaim.subject_id == subject_id,
            )
            .count()
        )
        if not checked:
            raise HTTPException(
                status_code=400,
                detail="No fact-check claims for this item — run a fact-check first",
            )
        return CorrectionsPreviewResponse(
            subject_type=normalized,
            subject_id=subject_id,
            correctable_claims=0,
            edits=[],
        )

    edits = build_preview(normalized, subject, claims)
    return CorrectionsPreviewResponse(
        subject_type=normalized,
        subject_id=subject_id,
        correctable_claims=len(claims),
        edits=[
            CorrectionEditItem(
                claim_id=e.claim_id,
                language=e.language,
                find=e.find,
                replace=e.replace,
                status=e.status,
                reason=e.reason,
            )
            for e in edits
        ],
    )


@router.post(
    "/fact-check/{subject_type}/{subject_id}/corrections/apply",
    response_model=CorrectionsApplyResponse,
)
def apply_fact_check_corrections(
    payload: CorrectionsApplyRequest,
    background_tasks: BackgroundTasks,
    subject_type: str = Path(..., description="SUGGESTION, RESEARCH, or ARTICLE"),
    subject_id: int = Path(..., description="Subject ID"),
    db: Session = Depends(get_db),
):
    """Apply previewed correction edits to the content (#387). Deterministic —
    no LLM call: each FIND is re-validated to still occur exactly once, edits
    are applied per claim atomically, and fully-corrected claims flip to
    CORRECTED. Echoed "skipped" entries (content never asserted the
    inaccuracy) also flip to CORRECTED without touching the content.
    Failures stay INACCURATE and are reported.
    """
    normalized, subject = _correction_subject_or_error(subject_type, subject_id, db)
    if not payload.edits:
        # Mirror of the preview's clean no-op: the driver pipes the previewed
        # edits verbatim, so an empty preview arrives here as an empty apply.
        return CorrectionsApplyResponse(
            subject_type=normalized,
            subject_id=subject_id,
            corrected_claim_ids=[],
            failed=[],
            counts=_subject_verdict_counts(db, normalized, subject_id),
        )

    result = apply_corrections(
        normalized,
        subject,
        [
            CorrectionEdit(e.claim_id, e.language, e.find, e.replace, e.status)
            for e in payload.edits
        ],
        db,
    )
    db.commit()
    if normalized == FactCheckSubject.ARTICLE.value:
        schedule_reindex(background_tasks, subject_id)
        # CORRECTED claims are indexed by their correction text (#625).
        schedule_claims_reindex(background_tasks, [subject_id])

    return CorrectionsApplyResponse(
        subject_type=normalized,
        subject_id=subject_id,
        corrected_claim_ids=result["corrected_claim_ids"],
        already_correct_claim_ids=result["already_correct_claim_ids"],
        failed=[
            CorrectionFailure(claim_id=cid, language=lang, reason=reason)
            for cid, lang, reason in result["failed"]
        ],
        counts=_subject_verdict_counts(db, normalized, subject_id),
    )


@router.post(
    "/fact-check/deep-research/generate-prompt",
    response_model=DeepResearchPromptResponse,
)
def generate_deep_research_fact_check_prompt(
    payload: DeepResearchPromptRequest,
    db: Session = Depends(get_db),
):
    """Compile pending NEEDS_DEEP_RESEARCH claims into a verification prompt
    (#377). API surface of the /fact-check-prompt skill, so the deep-research
    loop can be driven by automation (pic-extension) instead of psql.

    Batches at most DEEP_RESEARCH_BATCH_LIMIT (25) claims per prompt; call
    again after uploading verdicts while pending_total exceeds claim_count.
    Stateless — nothing is persisted.
    """
    try:
        batch = deep_research_batch(db, payload.subject_type, payload.subject_id)
    except DeepResearchScopeError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    if batch is None:
        raise HTTPException(status_code=404, detail="No claims pending deep research")

    return DeepResearchPromptResponse(
        batch_id=batch["batch_id"],
        claim_ids=batch["claim_ids"],
        claim_count=len(batch["claim_ids"]),
        pending_total=batch["pending_total"],
        prompt_text=batch["prompt_text"],
    )


@router.post(
    "/fact-check/deep-research/verdicts",
    response_model=DeepResearchVerdictsResponse,
)
def upload_deep_research_verdicts(
    payload: DeepResearchVerdictsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Write a deep-research verdict document back to fact_check_claims
    (#377). API surface of the /fact-check-process skill.

    Validation mirrors the skill: off-vocabulary or uncited definitive
    verdicts are skipped, rows must still be NEEDS_DEEP_RESEARCH (idempotent /
    stale-safe), and with claim_ids provided, out-of-batch verdicts are
    skipped and unanswered ids reported as missing. A document whose every
    verdict is an uncited UNVERIFIABLE is rejected as a failed run (400) so
    the extension's drain chain stops instead of recording bogus checks.
    """
    try:
        result = submit_deep_research_verdicts(db, payload.content, payload.claim_ids)
    except VerdictDocumentRejected as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    # Claim chunks (#625): resolved ARTICLE claims may now be facts, and the KB
    # may have new or changed rows. After the response, never fails it.
    schedule_claims_reindex(
        background_tasks, result["article_ids"], verified=result["kb_upserts"] > 0
    )

    return DeepResearchVerdictsResponse(
        written=result["written"],
        skipped=[SkippedVerdict(claim_id=cid, reason=reason) for cid, reason in result["skipped"]],
        missing_claim_ids=result["missing_claim_ids"],
        kb_upserts=result["kb_upserts"],
    )


@router.post(
    "/articles/{article_id}/fact-check",
    response_model=CrawlJobResponse,
    status_code=202,
)
def fact_check_article(
    background_tasks: BackgroundTasks,
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Grounding check (#375) as a background job (#384): judge the article's
    claims against its research document. Returns 202 + a job id; poll
    GET /media-library/jobs/{id}. Re-running replaces the previous rows.
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.content or not article.content.strip():
        raise HTTPException(status_code=400, detail="Article has no content")

    research = db.query(Research).filter(Research.id == article.research_id).first()
    if research is None:
        raise HTTPException(status_code=404, detail="Research not found")
    if not research.content or not research.content.strip():
        raise HTTPException(status_code=400, detail="Research has no content")

    return _kickoff_fact_check(db, background_tasks, FactCheckSubject.ARTICLE.value, article.id)


@router.post(
    "/articles/{article_id}/generate-tags",
    response_model=ArticleTagResponse,
)
def generate_article_tags(
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Phase 3: Auto-tag article using AI."""
    article = (
        db.query(Article).options(joinedload(Article.tags)).filter(Article.id == article_id).first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.content or not article.content.strip():
        raise HTTPException(status_code=400, detail="Article has no content")

    result = generate_tags(article, db)
    db.commit()

    return ArticleTagResponse(
        article_id=article.id,
        assigned_tag_ids=result["assigned_tag_ids"],
        suggested_new_tags=result["suggested_new_tags"],
    )


@router.post(
    "/articles/{article_id}/generate-audio",
    response_model=CrawlJobResponse,
    status_code=202,
)
def generate_article_audio(
    background_tasks: BackgroundTasks,
    payload: ArticleAudioGenerateRequest | None = None,
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Narrate the Spanish article in Diego's voice (ElevenLabs) and store it.

    ``mode="all"`` (the default; also what a body-less POST gets) regenerates
    every segment from the current ``content_es``; ``mode="changed"`` reuses
    the stored per-segment MP3s and pays ElevenLabs only for segments whose
    text changed since the manifest was written.

    Runs as a background job (#547): a large article is minutes of TTS and
    post-processing, past the 300s proxy ceiling. Returns 202 + a job id;
    poll GET /media-library/jobs/{id}. On success ``stats`` is an
    ``ArticleAudioResponse``. Nothing-to-narrate and no-changed-segments are
    still refused here with a 400, before any job is queued; re-posting the
    same mode while it runs joins the job, a different mode is a 409.
    """
    mode = payload.mode if payload else "all"
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.content_es or not article.content_es.strip():
        raise HTTPException(status_code=400, detail="Article has no Spanish content")
    try:
        plan_narration(
            article.title_es or article.title,
            article.content_es,
            article.audio_manifest_es,
            mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        job, created = create_narration_job(db, article_id, mode)
    except NarrationJobInFlightError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created:
        background_tasks.add_task(run_article_narration_job, job.id)
        # Mode next to the stored manifest size (#594): a surprise "all" run over
        # an article that already has N narrated segments stands out in the logs.
        logger.info(
            "Kicked off narration job %d for article %d (%s, stored manifest: %d segments)",
            job.id,
            article_id,
            mode,
            len(article.audio_manifest_es or []),
        )
    else:
        logger.info("Joined narration job %d for article %d (%s)", job.id, article_id, mode)
    return job


@router.get(
    "/articles/{article_id}/audio-segments",
    response_model=ArticleAudioSegmentsResponse,
)
def get_article_audio_segments(
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Diff the current Spanish text against the stored narration manifest.

    Drives the admin drawer's regenerate choice: which segments would a
    ``mode="changed"`` run actually pay for. No manifest (audio generated
    before selective regeneration existed, or never) marks every segment
    changed — the first full run backfills it.
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.content_es or not article.content_es.strip():
        raise HTTPException(status_code=400, detail="Article has no Spanish content")

    stored_hashes = {entry["hash"] for entry in (article.audio_manifest_es or [])}
    items = [
        ArticleAudioSegmentItem(
            position=i + 1,
            is_heading=is_heading,
            preview=text if len(text) <= 90 else text[:90] + "…",
            changed=segment_hash(text) not in stored_hashes,
        )
        for i, (text, is_heading) in enumerate(
            segment_article(article.title_es or article.title, article.content_es)
        )
    ]
    return ArticleAudioSegmentsResponse(
        article_id=article_id,
        has_audio=bool(article.audio_url_es),
        has_manifest=article.audio_manifest_es is not None,
        segments=items,
        changed_positions=[s.position for s in items if s.changed],
    )


def _grammar_wait_states(db: Session) -> dict[tuple[int, str], set[str]]:
    """``(article_id, "en"|"es") -> {status, ...}`` of its grammar wait tasks (#556).

    One query over every goal. An article absent from the map has no wait task
    (not driven by the pipeline, or planned before #556).
    """
    kinds = {
        AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value: "en",
        AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value: "es",
    }
    states: dict[tuple[int, str], set[str]] = {}
    for subject_id, kind, status in db.query(
        AgentTask.subject_id, AgentTask.kind, AgentTask.status
    ).filter(
        AgentTask.subject_type == AgentTaskSubject.ARTICLE.value,
        AgentTask.kind.in_(kinds),
    ):
        states.setdefault((subject_id, kinds[kind]), set()).add(status)
    return states


# A grammar wait in one of these states is the driver's go-ahead for Grammarly:
# everything before it in the chain (fact-check corrections, translation) is done.
_GRAMMAR_WAIT_OPEN = {AgentTaskStatus.READY.value, AgentTaskStatus.WAITING.value}


@router.get("/articles", response_model=list[ArticleListItem])
def list_articles(
    db: Session = Depends(get_db),
):
    """List articles for admin view."""
    articles = (
        db.query(Article)
        .options(
            joinedload(Article.tags),
            joinedload(Article.series_children),
        )
        .order_by(Article.created_at.desc())
        .all()
    )

    # Get research_id -> (suggestion_id, suggestion_title, category_id, category_name) mapping
    research_suggestion_map = {}
    for row in (
        db.query(
            Research.id, ArticleSuggestion.id, ArticleSuggestion.title, Category.id, Category.name
        )
        .join(ArticleSuggestion, Research.suggestion_id == ArticleSuggestion.id)
        .join(Category, ArticleSuggestion.category_id == Category.id)
        .all()
    ):
        research_suggestion_map[row[0]] = (row[1], row[2], row[3], row[4])

    def _get_mapped(research_id, idx):
        if research_id and research_id in research_suggestion_map:
            return research_suggestion_map[research_id][idx]
        return None

    # Unresolved meaning-check diffs per article (#518), one grouped query.
    review_counts = dict(
        db.query(GrammarReviewDiff.article_id, func.count(GrammarReviewDiff.id))
        .filter(GrammarReviewDiff.resolved_at.is_(None))
        .group_by(GrammarReviewDiff.article_id)
        .all()
    )

    consistency_counts = _open_consistency_counts(db)

    grammar_waits = _grammar_wait_states(db)

    def _ready_for_grammar(article: Article, language: str) -> bool:
        body = article.content if language == "en" else article.content_es
        stamp = article.grammar_checked_en_at if language == "en" else article.grammar_checked_es_at
        if not (body and body.strip()) or stamp is not None:
            return False
        states = grammar_waits.get((article.id, language))
        # Not driven by the pipeline: today's rule (content, no stamp).
        return states is None or bool(states & _GRAMMAR_WAIT_OPEN)

    return [
        ArticleListItem(
            id=a.id,
            suggestion_id=_get_mapped(a.research_id, 0),
            title=a.title,
            suggestion_title=_get_mapped(a.research_id, 1),
            category_id=_get_mapped(a.research_id, 2),
            category_name=_get_mapped(a.research_id, 3),
            status=a.status,
            outline_status=a.outline_status,
            has_content=bool(a.content and a.content.strip()),
            has_outline=bool(a.outline and a.outline.strip()),
            has_spanish=bool(a.content_es and a.content_es.strip()),
            has_tags=len(a.tags) > 0,
            has_feature_image=a.feature_image_id is not None,
            has_series_sections=bool(a.content and ABOUT_MARKER_EN in a.content),
            is_series=a.series_parent_id is not None or len(a.series_children) > 0,
            series_parent_id=a.series_parent_id,
            published_at=a.published_at,
            grammar_checked_en_at=a.grammar_checked_en_at,
            grammar_checked_es_at=a.grammar_checked_es_at,
            ready_for_grammar_en=_ready_for_grammar(a, "en"),
            ready_for_grammar_es=_ready_for_grammar(a, "es"),
            grammar_review_count=review_counts.get(a.id, 0),
            consistency_open_count=consistency_counts.get(a.id, 0),
            audio_qa_es=a.audio_qa_es,
            created_at=a.created_at,
        )
        for a in articles
    ]


@router.get("/tags", response_model=list[TagListItem])
def search_tags(
    q: str = Query("", description="Search query"),
    db: Session = Depends(get_db),
):
    """Search tags by name, or return all if no query."""
    query = db.query(Tag).order_by(Tag.name)
    if q:
        query = query.filter(Tag.name.ilike(f"%{q}%"))
    return query.limit(20).all()


@router.post("/tags", response_model=TagListItem, status_code=201)
def create_tag(
    body: TagCreateRequest,
    db: Session = Depends(get_db),
):
    """Create a new tag."""
    existing = db.query(Tag).filter(func.lower(Tag.name) == body.name.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Tag already exists")

    now = datetime.now(UTC)
    tag = Tag(name=body.name, status="PENDING", created_at=now, updated_at=now)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.get(
    "/articles/available-for-writing",
    response_model=list[ArticleWriteCandidate],
)
def get_articles_for_writing(
    db: Session = Depends(get_db),
):
    """Return articles with approved outline and no content."""
    articles = (
        db.query(Article)
        .filter(
            Article.outline_status == "APPROVED",
            (Article.content.is_(None)) | (Article.content == ""),
        )
        .order_by(Article.created_at.desc())
        .all()
    )

    # Get category names via research -> suggestion -> category
    research_ids = {a.research_id for a in articles if a.research_id}
    category_map = {}
    if research_ids:
        for row in (
            db.query(Research.id, Category.name)
            .join(ArticleSuggestion, Research.suggestion_id == ArticleSuggestion.id)
            .join(Category, ArticleSuggestion.category_id == Category.id)
            .filter(Research.id.in_(research_ids))
            .all()
        ):
            category_map[row[0]] = row[1]

    return [
        ArticleWriteCandidate(
            id=a.id,
            title=a.title,
            category_name=category_map.get(a.research_id),
        )
        for a in articles
    ]


@router.patch(
    "/articles/{article_id}/outline-status",
    response_model=StatusUpdateResponse,
)
def update_outline_status(
    article_id: int = Path(..., description="Article ID"),
    body: OutlineStatusUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update outline_status for an article."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.outline:
        raise HTTPException(status_code=400, detail="Article has no outline")

    article.outline_status = body.status
    db.commit()

    return StatusUpdateResponse(id=article.id, status=body.status)


@router.post(
    "/articles/{article_id}/translate",
    response_model=TranslateResponse,
)
async def translate_article(
    background_tasks: BackgroundTasks,
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Translate article title, excerpt, and content to Spanish."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.content or not article.content.strip():
        raise HTTPException(status_code=400, detail="Article has no content to translate")
    if article.content_es and article.content_es.strip():
        raise HTTPException(status_code=400, detail="Article already has Spanish content")

    from app.services.translation import get_translation_service

    service = get_translation_service()
    if service is None:
        raise HTTPException(
            status_code=500, detail="Translation service unavailable (DEEPL_API_KEY not set)"
        )

    title_es = await service.translate_text(article.title) if article.title else None
    excerpt_es = await service.translate_text(article.excerpt) if article.excerpt else None
    content_es = await service.translate_markdown(article.content)

    article.title_es = title_es
    article.excerpt_es = excerpt_es
    article.content_es = content_es
    try:
        sync_article_slugs(db, article)
    except SlugCollisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    schedule_reindex(background_tasks, article.id)

    return TranslateResponse(
        article_id=article.id,
        title_es=title_es or "",
        word_count=len(content_es.split()) if content_es else 0,
    )


@router.post(
    "/articles/{article_id}/series-sections",
    response_model=SeriesSectionsResponse,
)
def generate_series_sections(
    background_tasks: BackgroundTasks,
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Generate series navigation sections for all articles in a series."""
    article = (
        db.query(Article)
        .options(joinedload(Article.series_children))
        .filter(Article.id == article_id)
        .first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    # Determine the series parent ID
    if article.series_parent_id is None and len(article.series_children) > 0:
        parent_id = article.id
    elif article.series_parent_id is not None:
        parent_id = article.series_parent_id
    else:
        raise HTTPException(status_code=400, detail="Article is not part of a series")

    # Ensure the suggestion has title_es (use series parent article's title_es as fallback)
    parent_article = db.query(Article).filter(Article.id == parent_id).first()
    if parent_article and parent_article.research_id:
        research = db.query(Research).filter(Research.id == parent_article.research_id).first()
        if research:
            suggestion = (
                db.query(ArticleSuggestion)
                .filter(ArticleSuggestion.id == research.suggestion_id)
                .first()
            )
            if suggestion and not suggestion.title_es and parent_article.title_es:
                suggestion.title_es = parent_article.title_es
                db.flush()

    from app.services.series_sections import SeriesSectionGenerator

    generator = SeriesSectionGenerator(db)
    success, message, articles_updated = generator.generate_sections_for_series(parent_id)
    if success:
        db.commit()
        schedule_series_reindex(db, background_tasks, parent_id)

    return SeriesSectionsResponse(
        success=success,
        message=message,
        articles_updated=articles_updated,
    )


@router.patch(
    "/articles/{article_id}/publish",
    response_model=StatusUpdateResponse,
)
def publish_article(
    article_id: int = Path(..., description="Article ID"),
    body: PublishRequest | None = None,
    db: Session = Depends(get_db),
):
    """Set published_at on an APPROVED article that meets every precondition.

    With no ``published_at`` the server picks it (#558): the article's own
    existing date, else a series sibling's, else the next free 12:00 UTC day
    (one series per day) — see ``app/services/publish_slot.py``. That is the
    driver's path, and it converges on replay. An explicit date (the admin UI)
    is applied as given. A change of date moves the article's derived,
    still-scheduled Instagram promo with it (#588). The rules live in
    ``article_publishing.publish_article``, shared with the MCP approval
    executor (#642).
    """
    requested = body.published_at if body is not None else None
    try:
        article = article_publishing.publish_article(db, article_id, requested)
    except article_publishing.ArticleNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None
    except article_publishing.PublishPreconditionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    db.commit()

    return StatusUpdateResponse(id=article.id, status=article.status)


@router.patch(
    "/articles/{article_id}/unpublish",
    response_model=StatusUpdateResponse,
)
def unpublish_article(
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Remove published_at from an article.

    Also unschedules the article's derived, still-scheduled Instagram promo
    (#588). Shared with the MCP approval executor (#642).
    """
    try:
        article = article_publishing.unpublish_article(db, article_id)
    except article_publishing.ArticleNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None
    db.commit()

    return StatusUpdateResponse(id=article.id, status=article.status)


# --- Grammarly proofread flow (PIC Companion extension) ---------------------
# The extension pastes ``body_html`` into Grammarly's web editor, accepts
# suggestions, reads the corrected HTML back and posts it here. The contract:
# the client never sees markdown and never holds the About / Continue Reading
# series sections — this side splits, converts, re-attaches and stamps.

_DIFF_PREVIEW_MAX_LINES = 80


def _content_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _markdown_blocks(markdown_text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", markdown_text.strip()) if b.strip()]


def _normalize_markdown(markdown_text: str) -> str:
    return "\n\n".join(_markdown_blocks(markdown_text))


def _diff_preview(old: str, new: str) -> list[str]:
    lines = difflib.unified_diff(
        _markdown_blocks(old), _markdown_blocks(new), "stored", "corrected", n=0, lineterm=""
    )
    return list(lines)[:_DIFF_PREVIEW_MAX_LINES]


def _language_content(article: Article, language: Language) -> str:
    content = article.content if language == Language.EN else article.content_es
    if not content or not content.strip():
        raise HTTPException(
            status_code=400, detail=f"Article has no {language.value} content to proofread"
        )
    return content


@router.get("/articles/{article_id}/grammar-source", response_model=GrammarSourceResponse)
def get_grammar_source(
    article_id: int = Path(..., description="Article ID"),
    language: Language = Query(..., description="Which language of the article to proofread"),
    db: Session = Depends(get_db),
):
    """Return one language's article *body* as HTML for an external proofread.

    Series sections are stripped; the response carries a fingerprint of the
    stored column and the body's paragraph count, both checked again on apply.
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    content = _language_content(article, language)
    about, body, continue_reading = split_series_sections(content)
    title = article.title if language == Language.EN else (article.title_es or article.title)
    return GrammarSourceResponse(
        article_id=article.id,
        language=language.value,
        title=title,
        body_html=markdown_to_html(body),
        has_about=bool(about),
        has_continue=bool(continue_reading),
        fingerprint=_content_fingerprint(content),
        block_count=len(_markdown_blocks(body)),
    )


@router.post("/articles/{article_id}/grammar-apply", response_model=GrammarApplyResponse)
def apply_grammar_result(
    background_tasks: BackgroundTasks,
    article_id: int = Path(..., description="Article ID"),
    language: Language = Query(..., description="Which language of the article was proofread"),
    body: GrammarApplyRequest = ...,
    db: Session = Depends(get_db),
):
    """Write a proofread body back, re-attaching the series sections untouched.

    Rules, in order: fingerprint mismatch → 409 (the article changed since the
    source was fetched); paragraph-count mismatch → 400 (a lossy read-back or a
    suggestion that merged paragraphs — refused with a diff preview);
    ``dry_run`` → report only; empty ``accepted`` → stamp
    ``grammar_checked_<lang>_at`` without rewriting the content (so a clean
    article never churns through the normalising round trip); otherwise run
    the meaning check (#518 — Sonnet reviews each changed paragraph and reverts
    edits that changed meaning; the rest it cannot vouch for are written but
    filed as ``GrammarReviewDiff`` rows), write the body, stamp, and record one
    ``GrammarCheckFix`` row per accepted item — reverted ones as
    ``applied=False``. ``stamp=False`` skips the stamp only: the body is still
    written, so a low-scoring article keeps its corrections and stays queued.
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    content = _language_content(article, language)
    if _content_fingerprint(content) != body.fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Article content changed since grammar-source was fetched; fetch it again",
        )

    about, old_body, continue_reading = split_series_sections(content)
    new_body = html_to_markdown(body.body_html)
    if not new_body.strip():
        raise HTTPException(status_code=400, detail="body_html converted to empty markdown")

    old_blocks = _markdown_blocks(old_body)
    new_blocks = _markdown_blocks(new_body)
    if len(old_blocks) != len(new_blocks):
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    f"Paragraph count changed ({len(old_blocks)} stored, "
                    f"{len(new_blocks)} corrected); refusing to apply"
                ),
                "diff_preview": _diff_preview(old_body, new_body),
            },
        )

    changed = _normalize_markdown(old_body) != _normalize_markdown(new_body)
    diff_preview = _diff_preview(old_body, new_body) if changed else []

    if body.dry_run:
        return GrammarApplyResponse(
            article_id=article.id,
            language=language.value,
            changed=changed,
            written=False,
            accepted_count=len(body.accepted),
            block_count=len(new_blocks),
            checked_at=None,
            diff_preview=diff_preview,
        )

    written = False
    meaning = None
    if body.accepted:
        meaning = run_meaning_check(old_body, new_body)
        new_body = meaning.body
        # Keep the body's original leading/trailing whitespace so the joins
        # with the (untouched) sections stay exactly as they were.
        leading = old_body[: len(old_body) - len(old_body.lstrip())]
        trailing = old_body[len(old_body.rstrip()) :]
        new_content = about + leading + new_body + trailing + continue_reading
        if language == Language.EN:
            article.content = new_content
        else:
            article.content_es = new_content
        for item in body.accepted:
            reverted_reason = _reverted_reason_for(item.after, new_body, meaning)
            db.add(
                GrammarCheckFix(
                    article_id=article.id,
                    language=language.value,
                    find_text=item.before,
                    replace_text=item.after,
                    applied=reverted_reason is None,
                    skipped_reason=reverted_reason,
                    source=GrammarFixSource.GRAMMARLY.value,
                )
            )
        for pair, reason in meaning.review:
            db.add(
                GrammarReviewDiff(
                    article_id=article.id,
                    language=language.value,
                    block_index=pair.index,
                    before_text=pair.before,
                    after_text=pair.after,
                    reason=reason,
                )
            )
        written = True
        if meaning.called:
            diff_preview = _diff_preview(old_body, new_body)

    checked_at = datetime.now(UTC) if body.stamp else None
    if body.stamp:
        if language == Language.EN:
            article.grammar_checked_en_at = checked_at
        else:
            article.grammar_checked_es_at = checked_at
    db.commit()
    schedule_reindex(background_tasks, article.id)

    logger.info(
        "grammar-apply article=%s lang=%s tool=%s accepted=%s written=%s changed=%s stamped=%s score=%s",
        article.id,
        language.value,
        body.tool,
        len(body.accepted),
        written,
        changed,
        body.stamp,
        body.score,
    )
    return GrammarApplyResponse(
        article_id=article.id,
        language=language.value,
        changed=changed,
        written=written,
        accepted_count=len(body.accepted),
        block_count=len(new_blocks),
        checked_at=checked_at,
        diff_preview=diff_preview,
        stamped=body.stamp,
        meaning_checked=bool(meaning and meaning.called),
        reverted_count=len(meaning.reverted) if meaning else 0,
        review_count=len(meaning.review) if meaning else 0,
    )


def _reverted_reason_for(after: str, final_body: str, meaning) -> str | None:
    """Why an accepted item did not survive the meaning check, or None.

    An item is reverted when its paragraph was reverted *and* its ``after``
    snippet no longer occurs in the final body (emphasis-tolerant, since the
    snippet is plain text and the body is markdown). Items whose paragraph the
    check left alone stay applied.
    """
    if meaning is None or not meaning.reverted or not after.strip():
        return None
    for pair, reason in meaning.reverted:
        if count_snippet(pair.after, after) and not count_snippet(final_body, after):
            return f"reverted by meaning check: {reason}"
    return None


def _open_consistency_counts(db: Session) -> dict[int, int]:
    """Open cross-article consistency findings per article (#631).

    In a SAVEPOINT: on a database that has not run migration 0046 yet the
    grid gets ``{}`` (no pill) and the caller's transaction stays usable, so
    the articles listing never fails on the deploy window.
    """
    try:
        with db.begin_nested():
            return dict(
                db.query(ConsistencyFinding.article_id, func.count(ConsistencyFinding.id))
                .filter(ConsistencyFinding.resolved_at.is_(None))
                .group_by(ConsistencyFinding.article_id)
                .all()
            )
    except DBAPIError:
        logger.warning("consistency findings count unavailable", exc_info=True)
        return {}


def _consistency_item(row: ConsistencyFinding, other: Article | None) -> ConsistencyFindingItem:
    return ConsistencyFindingItem(
        id=row.id,
        article_id=row.article_id,
        claim_id=row.claim_id,
        claim_text=row.claim_text,
        other_source_type=row.other_source_type,
        other_source_id=row.other_source_id,
        other_article_id=row.other_article_id,
        other_article_title=other.title if other else None,
        other_article_slug=other.slug if other else None,
        other_text=row.other_text,
        verdict=row.verdict,
        explanation=row.explanation,
        similarity=row.similarity,
        resolved_at=row.resolved_at,
        resolution=row.resolution,
        created_at=row.created_at,
    )


@router.get(
    "/articles/{article_id}/consistency-findings",
    response_model=ConsistencyFindingList,
)
def list_consistency_findings(
    article_id: int = Path(..., description="Article ID"),
    include_resolved: bool = Query(False, description="Also return resolved findings"),
    db: Session = Depends(get_db),
):
    """Cross-article contradictions found for an article (#631, shadow mode).

    Records only: nothing here changes a fact-check verdict or gates publishing.
    """
    if db.get(Article, article_id) is None:
        raise HTTPException(status_code=404, detail="Article not found")
    query = db.query(ConsistencyFinding).filter(ConsistencyFinding.article_id == article_id)
    if not include_resolved:
        query = query.filter(ConsistencyFinding.resolved_at.is_(None))
    rows = query.order_by(ConsistencyFinding.id).all()
    other_ids = {r.other_article_id for r in rows if r.other_article_id is not None}
    others = (
        {a.id: a for a in db.query(Article).filter(Article.id.in_(other_ids)).all()}
        if other_ids
        else {}
    )
    return ConsistencyFindingList(
        article_id=article_id,
        open_count=sum(1 for r in rows if r.resolved_at is None),
        findings=[_consistency_item(r, others.get(r.other_article_id)) for r in rows],
    )


@router.post(
    "/articles/{article_id}/consistency-findings/{finding_id}/resolve",
    response_model=ConsistencyFindingItem,
)
def resolve_consistency_finding(
    body: ConsistencyResolveRequest,
    article_id: int = Path(..., description="Article ID"),
    finding_id: int = Path(..., description="Consistency finding ID"),
    db: Session = Depends(get_db),
):
    """Dismiss a finding or mark it resolved. Bookkeeping only; idempotent (an
    already-closed finding keeps its first resolution)."""
    row = db.get(ConsistencyFinding, finding_id)
    if row is None or row.article_id != article_id:
        raise HTTPException(status_code=404, detail="Consistency finding not found")
    if row.resolved_at is None:
        row.resolved_at = datetime.now(UTC)
        row.resolution = body.resolution
        db.commit()
        db.refresh(row)
    other = db.get(Article, row.other_article_id) if row.other_article_id else None
    return _consistency_item(row, other)


@router.get("/articles/{article_id}/grammar-review", response_model=GrammarReviewDiffList)
def list_grammar_review_diffs(
    article_id: int = Path(..., description="Article ID"),
    include_resolved: bool = Query(False, description="Also return resolved rows"),
    db: Session = Depends(get_db),
):
    """Paragraphs the meaning check flagged for a human (#518), newest first."""
    if db.get(Article, article_id) is None:
        raise HTTPException(status_code=404, detail="Article not found")
    query = db.query(GrammarReviewDiff).filter(GrammarReviewDiff.article_id == article_id)
    if not include_resolved:
        query = query.filter(GrammarReviewDiff.resolved_at.is_(None))
    rows = query.order_by(GrammarReviewDiff.id.desc()).all()
    pending = sum(1 for r in rows if r.resolved_at is None)
    return GrammarReviewDiffList(
        article_id=article_id,
        pending_count=pending,
        diffs=[GrammarReviewDiffItem.model_validate(r, from_attributes=True) for r in rows],
    )


@router.post(
    "/articles/{article_id}/grammar-review/{diff_id}/resolve",
    response_model=GrammarReviewDiffItem,
)
def resolve_grammar_review_diff(
    article_id: int = Path(..., description="Article ID"),
    diff_id: int = Path(..., description="Review diff ID"),
    db: Session = Depends(get_db),
):
    """Mark one flagged paragraph as seen. Idempotent; the text itself is
    edited in the drawer, not here."""
    row = db.get(GrammarReviewDiff, diff_id)
    if row is None or row.article_id != article_id:
        raise HTTPException(status_code=404, detail="Review diff not found")
    if row.resolved_at is None:
        row.resolved_at = datetime.now(UTC)
        db.commit()
        db.refresh(row)
    return GrammarReviewDiffItem.model_validate(row, from_attributes=True)


@router.get("/articles/{article_id}", response_model=ArticleDetailAdmin)
def get_article_detail(
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Get a single article with bilingual content converted to HTML."""
    article = (
        db.query(Article).options(joinedload(Article.tags)).filter(Article.id == article_id).first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    return ArticleDetailAdmin(
        id=article.id,
        title=article.title,
        excerpt=article.excerpt,
        content_html=markdown_to_html(article.content or ""),
        outline=article.outline,
        title_es=article.title_es,
        excerpt_es=article.excerpt_es,
        content_html_es=markdown_to_html(article.content_es) if article.content_es else None,
        audio_url_es=article.audio_url_es,
        audio_qa_es=article.audio_qa_es,
        status=article.status,
        tags=[TagListItem.model_validate(t) for t in article.tags],
        created_at=article.created_at,
    )


@router.put("/articles/{article_id}", response_model=ArticleDetailAdmin)
def update_article(
    background_tasks: BackgroundTasks,
    article_id: int = Path(..., description="Article ID"),
    body: ArticleUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update article content (bilingual HTML in, stored as Markdown)."""
    article = (
        db.query(Article).options(joinedload(Article.tags)).filter(Article.id == article_id).first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    article.title = body.title
    article.excerpt = body.excerpt
    article.content = html_to_markdown(body.content_html)
    article.title_es = body.title_es
    article.excerpt_es = body.excerpt_es
    article.content_es = html_to_markdown(body.content_html_es) if body.content_html_es else None

    try:
        sync_article_slugs(db, article)
    except SlugCollisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Sync tags
    if body.tag_ids:
        tags = db.query(Tag).filter(Tag.id.in_(body.tag_ids)).all()
        article.tags = tags
    else:
        article.tags = []

    db.commit()
    db.refresh(article)
    schedule_reindex(background_tasks, article.id)

    return ArticleDetailAdmin(
        id=article.id,
        title=article.title,
        excerpt=article.excerpt,
        content_html=markdown_to_html(article.content or ""),
        title_es=article.title_es,
        excerpt_es=article.excerpt_es,
        content_html_es=markdown_to_html(article.content_es) if article.content_es else None,
        audio_url_es=article.audio_url_es,
        audio_qa_es=article.audio_qa_es,
        status=article.status,
        tags=[TagListItem.model_validate(t) for t in article.tags],
        created_at=article.created_at,
    )


@router.get("/articles/{article_id}/download")
def download_article(
    article_id: int = Path(..., description="Article ID"),
    lang: str = Query("en", description="Language: en or es"),
    db: Session = Depends(get_db),
):
    """Download article content as markdown in the specified language."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    # Get suggestion ID via research
    suggestion_id = 0
    if article.research_id:
        research = db.query(Research).filter(Research.id == article.research_id).first()
        if research:
            suggestion_id = research.suggestion_id

    if lang == "es":
        content = article.content_es or ""
        title = article.title_es or article.title
        if not content:
            raise HTTPException(status_code=404, detail="No Spanish content available")
    else:
        content = article.content or ""
        title = article.title

    # Transliterate title for safe filename
    from unidecode import unidecode

    title_slug = unidecode(title).replace(" ", "_")
    filename = f"{suggestion_id}_{title_slug}_article.md"

    return PlainTextResponse(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Feature Image ---

IMAGES_DIR = FilePath("/app/static/images/articles")
FEATURE_IMAGE_URL_PREFIX = "/static/images/articles/"


def _is_own_feature_image_file(media: Media, article_id: int) -> bool:
    """True when *media* is this article's own generated file on the static volume.

    Only such a row is reused (and only its file is ever deleted) on
    regeneration. A catalog / CDN media row assigned through ``assign-media``
    is someone else's record: it is left untouched and a new row is created.
    """
    return bool(
        media.file_path
        and media.file_path.startswith(FEATURE_IMAGE_URL_PREFIX)
        and media.filename
        and re.fullmatch(rf"article_{article_id}(_\d+)?\.(jpg|png|webp)", media.filename)
    )


def _persist_feature_image(
    db: Session,
    article: Article,
    jpeg_bytes: bytes,
    original_filename: str,
) -> Media:
    """Write *jpeg_bytes* to the static images dir and (re)point the feature image.

    Every write gets a fresh, timestamped ``article_{id}_{ts}.jpg`` (#580): nginx
    serves ``/static/`` with a one-day ``max-age``, so overwriting a fixed name
    would keep showing the superseded image in the admin and on the public site.
    The superseded file is deleted only after the new file and the DB row are
    committed, only when it was this article's own generated file, and a failed
    delete only logs.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    filename = f"article_{article.id}_{stamp}.jpg"
    file_path = IMAGES_DIR / filename
    url_path = f"{FEATURE_IMAGE_URL_PREFIX}{filename}"

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(jpeg_bytes)

    now = datetime.now(UTC)
    existing_media = None
    if article.feature_image_id:
        existing_media = db.query(Media).filter(Media.id == article.feature_image_id).first()

    superseded: str | None = None
    try:
        if existing_media is not None and _is_own_feature_image_file(existing_media, article.id):
            if existing_media.filename != filename:
                superseded = existing_media.filename
            existing_media.filename = filename
            existing_media.original_filename = original_filename
            existing_media.file_path = url_path
            existing_media.file_size = len(jpeg_bytes)
            existing_media.mime_type = "image/jpeg"
            existing_media.updated_at = now
            media = existing_media
        else:
            media = Media(
                filename=filename,
                original_filename=original_filename,
                file_path=url_path,
                file_size=len(jpeg_bytes),
                mime_type="image/jpeg",
                media_type="IMAGE",
                source="GENERATED",
                title=f"Feature image for: {article.title}",
                created_at=now,
                updated_at=now,
            )
            db.add(media)
            db.flush()

        article.feature_image_id = media.id
        db.commit()
    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise

    if superseded:
        try:
            (IMAGES_DIR / superseded).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not delete superseded feature image %s for article %s: %s",
                superseded,
                article.id,
                exc,
            )

    db.refresh(media)
    return media


@router.post(
    "/articles/{article_id}/generate-image",
    response_model=ImageUploadResponse,
)
def generate_article_feature_image(
    article_id: int = Path(..., description="Article ID"),
    db: Session = Depends(get_db),
):
    """Generate the article's feature image with Gemini and store it (#419).

    The only way a feature image is made (#580 removed the manual
    prompt-copy / upload loop): the watercolor prompt is generated fresh
    (Sonnet), sent to the image API, and the JPEG stored under a new
    timestamped filename. It always regenerates — the agent driver's
    ``GENERATE_FEATURE_IMAGE`` step and the admin grid's Generate / Regenerate
    action call it identically, so a replay simply converges on a new image.

    Both model calls are external I/O measured in tens of seconds, so the DB
    session is closed between the reads and the write (Connection Pool rule):
    SQLAlchemy checks a connection back out when the article is re-read.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Feature-image generation is not configured")

    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.content or not article.content.strip():
        raise HTTPException(status_code=400, detail="Article has no content")

    # The prompt service only reads title + content, both already loaded; the
    # detached instance keeps them after close() (close does not expire).
    db.close()  # release the pooled connection during the Sonnet + Gemini calls

    from app.services.image_prompt import generate_image_prompt

    prompt_text = generate_image_prompt(article)

    from app.services.gemini_image import GeminiImageError, generate_feature_image

    try:
        jpeg_bytes = generate_feature_image(prompt_text)
    except GeminiImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:  # deleted while generating
        raise HTTPException(status_code=404, detail="Article not found")

    media = _persist_feature_image(db, article, jpeg_bytes, f"gemini_article_{article_id}.jpg")
    logger.info(
        "Generated feature image for article %s (media %s, %d bytes)",
        article_id,
        media.id,
        len(jpeg_bytes),
    )

    return ImageUploadResponse(
        article_id=article_id,
        media_id=media.id,
        file_path=media.file_path,
    )


@router.post(
    "/articles/{article_id}/assign-media",
    response_model=ImageUploadResponse,
)
def assign_media_to_article(
    article_id: int = Path(..., description="Article ID"),
    request: AssignMediaRequest = ...,
    db: Session = Depends(get_db),
):
    """Assign an existing media record as the feature image for an article."""
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    media = db.query(Media).filter(Media.id == request.media_id).first()
    if media is None:
        raise HTTPException(status_code=404, detail="Media record not found")

    article.feature_image_id = media.id
    db.commit()

    return ImageUploadResponse(
        article_id=article.id,
        media_id=media.id,
        file_path=media.file_path,
    )


ENTITY_MODEL_MAP = {
    "suggestions": ArticleSuggestion,
    "research": Research,
    "articles": Article,
}


@router.patch(
    "/{entity}/{entity_id}/status",
    response_model=StatusUpdateResponse,
)
def update_entity_status(
    entity: str = Path(..., description="Entity type: suggestions, research, or articles"),
    entity_id: int = Path(..., description="Entity ID"),
    body: StatusUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update the status of a suggestion, research, or article."""
    model = ENTITY_MODEL_MAP.get(entity)
    if model is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity type: {entity}. Must be one of: {', '.join(ENTITY_MODEL_MAP.keys())}",
        )

    record = db.query(model).filter(model.id == entity_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"{entity} with id {entity_id} not found")

    try:
        article_publishing.transition_status(record, body.status)
    except article_publishing.InvalidStatusTransitionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None

    db.commit()

    return StatusUpdateResponse(id=record.id, status=record.status)


# --- Settings: Admin Emails ---


@router.get("/settings/admin-emails", response_model=list[AdminEmailItem])
def list_admin_emails(
    db: Session = Depends(get_db),
):
    """List all admin emails."""
    return db.query(AdminEmail).order_by(AdminEmail.id).all()


@router.post(
    "/settings/admin-emails",
    response_model=AdminEmailItem,
    status_code=201,
)
def add_admin_email(
    body: AdminEmailCreateRequest,
    db: Session = Depends(get_db),
):
    """Add a new admin email."""
    existing = (
        db.query(AdminEmail).filter(func.lower(AdminEmail.email) == body.email.lower()).first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    admin_email = AdminEmail(email=body.email, name=body.name, is_active=True)
    db.add(admin_email)
    db.commit()
    db.refresh(admin_email)
    return admin_email


@router.delete("/settings/admin-emails/{email_id}")
def delete_admin_email(
    email_id: int = Path(..., description="Admin email ID"),
    db: Session = Depends(get_db),
):
    """Delete an admin email."""
    admin_email = db.query(AdminEmail).filter(AdminEmail.id == email_id).first()
    if not admin_email:
        raise HTTPException(status_code=404, detail="Admin email not found")

    db.delete(admin_email)
    db.commit()
    return {"ok": True}


# --- Settings: Pricing Config ---

PRICING_KEYS = [
    "base_fee",
    "hourly_rate_pax_1",
    "hourly_rate_pax_2_3",
    "hourly_rate_pax_4_5",
    "hourly_rate_pax_6_7",
    "hourly_rate_pax_8_9",
    "hourly_rate_pax_10_15",
    "senior_discount_percent",
    "transport_hourly_rate_5_8",
    "transport_hourly_rate_9_15",
    "transport_extra_hours",
]


@router.get("/settings/pricing", response_model=list[PricingConfigItem])
def get_pricing_config(
    db: Session = Depends(get_db),
):
    """Get pricing-related configuration values."""
    return db.query(PricingConfig).filter(PricingConfig.key.in_(PRICING_KEYS)).all()


@router.put("/settings/pricing", response_model=list[PricingConfigItem])
def update_pricing_config(
    items: list[PricingConfigUpdateRequest],
    db: Session = Depends(get_db),
):
    """Update pricing configuration values."""
    for item in items:
        if item.key not in PRICING_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid pricing key: {item.key}",
            )
        row = db.query(PricingConfig).filter(PricingConfig.key == item.key).first()
        if row:
            row.value = item.value
        else:
            db.add(PricingConfig(key=item.key, value=item.value))

    db.commit()

    return db.query(PricingConfig).filter(PricingConfig.key.in_(PRICING_KEYS)).all()


# --- Editorial calendar (blog articles + Instagram posts by date) ------------


@router.get("/editorial-calendar", response_model=EditorialCalendarResponse)
def get_editorial_calendar(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """One month of publications: blog articles (by ``published_at``) and
    Instagram posts (by ``published_at``/``scheduled_at``).

    The admin calendar paints these as pills — orange for a future publish,
    green for live, red for a failed Instagram publish — and opens the item's
    edit drawer on click.
    """
    window_start = datetime(year, month, 1, tzinfo=UTC)
    window_end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=UTC)
    )
    now = datetime.now(UTC)

    articles = (
        db.query(Article)
        .filter(Article.published_at >= window_start, Article.published_at < window_end)
        .order_by(Article.published_at)
        .all()
    )
    ig_when = func.coalesce(InstagramPost.published_at, InstagramPost.scheduled_at)
    ig_posts = (
        db.query(InstagramPost)
        .filter(ig_when >= window_start, ig_when < window_end)
        .order_by(ig_when)
        .all()
    )

    def _ig_state(p: InstagramPost) -> str:
        if p.published_at is not None:
            return "published"
        if p.publish_error:
            return "failed"
        return "scheduled"

    return EditorialCalendarResponse(
        articles=[
            EditorialCalendarArticle(
                id=a.id,
                title=a.title_es or a.title,
                published_at=a.published_at,
                is_live=a.status == "APPROVED" and a.published_at <= now,
            )
            for a in articles
        ],
        instagram_posts=[
            EditorialCalendarInstagramPost(
                id=p.id,
                post_type=p.post_type,
                title=p.title,
                when=p.published_at or p.scheduled_at,
                state=_ig_state(p),
            )
            for p in ig_posts
        ],
    )
