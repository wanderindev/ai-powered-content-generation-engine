"""Educational content admin API endpoints."""

import logging
from datetime import UTC, datetime
from typing import Literal

import sentry_sdk
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
)
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.responses import PlainTextResponse

from app.api.dependencies import validate_admin_token
from app.core.database import get_db
from app.core.excursions import EXCURSIONS
from app.models.crawl_job import CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.models.enums import FactCheckSubject, MaterialType
from app.models.research import Research
from app.schemas.dashboard import StatusUpdateResponse
from app.schemas.edu import (
    BlogResearchForUpload,
    EduGuideForSlides,
    EduGuidePdfResponse,
    EduGuideTranslateResponse,
    EduMaterialContentUpdate,
    EduMaterialDetail,
    EduMaterialGenerateRequest,
    EduMaterialListItem,
    EduNarrationGenerateRequest,
    EduNarrationGenerateResponse,
    EduPublishRequest,
    EduPublishResponse,
    EduResearchDetail,
    EduResearchForMaterials,
    EduResearchListItem,
    EduResearchPromptRequest,
    EduResearchPromptResponse,
    EduResearchUploadResponse,
    EduSlideOut,
    EduSlidesAudioRequest,
    EduSlidesGenerateRequest,
    EduSlidesGenerateResponse,
    EduSlidesRenderRequest,
    EduSlidesSubsetRegenerateRequest,
    EduSlidesSubsetRegenerateResponse,
    EduSlidesUpdate,
    EduStatusUpdateRequest,
    EduSuggestionDetail,
    EduSuggestionForResearch,
    EduSuggestionListItem,
    EduSuggestionUpdateRequest,
    ExcursionCoverageItem,
    MaterialUploadResponse,
)
from app.schemas.media_candidate import CrawlJobResponse
from app.services.edu_jobs import EduJobInFlightError, create_edu_job, run_edu_job
from app.services.edu_material_generation import (
    generate_edu_narration,
    generate_edu_slides,
    regenerate_slides_subset,
)
from app.services.edu_research import (
    generate_edu_research_prompt,
    validate_edu_research_document,
)
from app.services.fact_check import (
    GROUNDABLE_MATERIAL_TYPES,
    FactCheckJobInFlightError,
    create_fact_check_job,
    run_fact_check_job,
)
from app.services.fact_check_gate import (
    STEP_INTERNAL,
    STEP_PUBLIC,
    FactCheckGateError,
    enforce_material_gate,
    invalidate_material_claims,
)
from app.services.guide_pdf import generate_guide_pdf, guide_pdf_key
from app.services.research_common import (
    MAX_MATERIAL_UPLOAD_BYTES,
    read_upload_bytes,
    read_upload_text,
)
from app.utils.markdown import markdown_to_html

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/dashboard/edu",
    tags=["edu"],
    dependencies=[Depends(validate_admin_token)],
)


def _enforce_fact_check_gate(db: Session, material_id: int, step: str, *, action: str) -> None:
    """Refuse a gated step whose material carries blocking fact-check claims (#462).

    Translates the service-layer gate error into a 409 — a conflict with the
    material's current state, not a malformed request.
    """
    try:
        enforce_material_gate(db, material_id, step, action=action)
    except FactCheckGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _kickoff_edu_job(
    db: Session, background_tasks: BackgroundTasks, job_type: CrawlJobType, params: dict
):
    """Create (or join an identical in-flight) edu job and schedule its runner (#346).

    Returns the job row for a 202. A *different* edu job already running on the
    same subject is a 409 naming it.
    """
    try:
        job, created = create_edu_job(db, job_type, params)
    except EduJobInFlightError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created:
        background_tasks.add_task(run_edu_job, job.id)
        logger.info("Kicked off edu job %d (%s) %s", job.id, job_type.value, params)
    else:
        logger.info("Joined in-flight edu job %d (%s) %s", job.id, job_type.value, params)
    return job


VALID_TRANSITIONS = {
    "PENDING": {"APPROVED", "REJECTED"},
    "APPROVED": {"PENDING", "REJECTED"},
    "REJECTED": {"PENDING", "APPROVED"},
}


# --- Suggestions ---


@router.get("/suggestions", response_model=list[EduSuggestionListItem])
def list_edu_suggestions(
    db: Session = Depends(get_db),
):
    """List all educational suggestions."""
    rows = db.query(EduSuggestion).order_by(EduSuggestion.id).all()

    result = []
    for s in rows:
        has_research = (
            db.query(EduResearch.id).filter(EduResearch.suggestion_id == s.id).first() is not None
        )
        result.append(
            EduSuggestionListItem(
                id=s.id,
                excursion_key=s.excursion_key,
                title=s.title,
                status=s.status,
                grade_bands=s.grade_bands or [],
                has_research=has_research,
                created_at=s.created_at,
            )
        )

    return result


@router.get(
    "/suggestions/available-for-research",
    response_model=list[EduSuggestionForResearch],
)
def get_edu_suggestions_for_research(
    db: Session = Depends(get_db),
):
    """List APPROVED edu suggestions that don't yet have research."""
    research_suggestion_ids = {r.suggestion_id for r in db.query(EduResearch.suggestion_id).all()}

    suggestions = (
        db.query(EduSuggestion)
        .filter(EduSuggestion.status == "APPROVED")
        .order_by(EduSuggestion.id)
        .all()
    )

    return [
        EduSuggestionForResearch(
            id=s.id,
            title=s.title,
            excursion_key=s.excursion_key,
            grade_bands=s.grade_bands or [],
        )
        for s in suggestions
        if s.id not in research_suggestion_ids
    ]


@router.get("/suggestions/{suggestion_id}", response_model=EduSuggestionDetail)
def get_edu_suggestion(
    suggestion_id: int = Path(..., description="Edu suggestion ID"),
    db: Session = Depends(get_db),
):
    """Get a single educational suggestion with full details."""
    s = db.query(EduSuggestion).filter(EduSuggestion.id == suggestion_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Edu suggestion not found")

    return EduSuggestionDetail(
        id=s.id,
        excursion_key=s.excursion_key,
        title=s.title,
        description=s.description,
        grade_bands=s.grade_bands or [],
        sub_topics=s.sub_topics or [],
        status=s.status,
        created_at=s.created_at,
    )


@router.patch(
    "/suggestions/{suggestion_id}/status",
    response_model=StatusUpdateResponse,
)
def update_edu_suggestion_status(
    suggestion_id: int = Path(..., description="Edu suggestion ID"),
    body: EduStatusUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update the status of an educational suggestion."""
    record = db.query(EduSuggestion).filter(EduSuggestion.id == suggestion_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Edu suggestion not found")

    if body.status not in VALID_TRANSITIONS.get(record.status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition from {record.status} to {body.status}",
        )

    record.status = body.status
    db.commit()

    return StatusUpdateResponse(id=record.id, status=record.status)


@router.put("/suggestions/{suggestion_id}", response_model=EduSuggestionDetail)
def update_edu_suggestion(
    suggestion_id: int = Path(..., description="Edu suggestion ID"),
    body: EduSuggestionUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update an educational suggestion's editable fields."""
    record = db.query(EduSuggestion).filter(EduSuggestion.id == suggestion_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Edu suggestion not found")

    if body.title is not None:
        record.title = body.title
    if body.description is not None:
        record.description = body.description
    if body.grade_bands is not None:
        record.grade_bands = body.grade_bands
    if body.sub_topics is not None:
        record.sub_topics = body.sub_topics

    db.commit()
    db.refresh(record)

    return EduSuggestionDetail(
        id=record.id,
        excursion_key=record.excursion_key,
        title=record.title,
        description=record.description,
        grade_bands=record.grade_bands or [],
        sub_topics=record.sub_topics or [],
        status=record.status,
        created_at=record.created_at,
    )


# --- Research ---


@router.get("/research", response_model=list[EduResearchListItem])
def list_edu_research(
    db: Session = Depends(get_db),
):
    """List all educational research records."""
    rows = db.query(EduResearch).order_by(EduResearch.id.desc()).all()

    result = []
    for r in rows:
        result.append(
            EduResearchListItem(
                id=r.id,
                suggestion_id=r.suggestion_id,
                suggestion_title=r.suggestion.title,
                status=r.status,
                has_content=bool(r.content),
                created_at=r.created_at,
            )
        )

    return result


@router.patch(
    "/research/{research_id}/status",
    response_model=StatusUpdateResponse,
)
def update_edu_research_status(
    research_id: int = Path(..., description="Edu research ID"),
    body: EduStatusUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update the status of an educational research record."""
    record = db.query(EduResearch).filter(EduResearch.id == research_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Edu research not found")

    if body.status not in VALID_TRANSITIONS.get(record.status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition from {record.status} to {body.status}",
        )

    record.status = body.status
    db.commit()

    return StatusUpdateResponse(id=record.id, status=record.status)


@router.post(
    "/research/{research_id}/fact-check",
    response_model=CrawlJobResponse,
    status_code=202,
)
def fact_check_edu_research(
    background_tasks: BackgroundTasks,
    research_id: int = Path(..., description="Edu research ID"),
    db: Session = Depends(get_db),
):
    """External fact-check of an edu research doc as a background job (#460).

    Edu research is the root of every excursion deliverable — guides, decks,
    narration and video all derive from it — so it is verified before material
    is generated from it (epic #459).

    Same external tier as blog research: the extract-only tier (#389) extracts
    the doc's claims, inherits verdicts the verified KB already holds (#378),
    and queues the rest as NEEDS_DEEP_RESEARCH for the free deep-research loop
    (#377). Returns 202 + a job id; poll
    GET /media-library/jobs/{id}. Re-running replaces the doc's claim rows.
    """
    record = db.query(EduResearch).filter(EduResearch.id == research_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Edu research not found")
    if not record.content or not record.content.strip():
        raise HTTPException(status_code=400, detail="Edu research has no content")

    try:
        job = create_fact_check_job(db, FactCheckSubject.EDU_RESEARCH.value, record.id)
    except FactCheckJobInFlightError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    background_tasks.add_task(run_fact_check_job, job.id)
    logger.info("Kicked off fact-check job %d for edu research %d", job.id, record.id)
    return job


@router.post(
    "/materials/{material_id}/fact-check",
    response_model=CrawlJobResponse,
    status_code=202,
)
def fact_check_edu_material(
    background_tasks: BackgroundTasks,
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Ground a generated study guide or slide deck against its source research
    (#461, #464).

    The grounding tier, not the external one: the material should assert nothing
    its ``edu_research`` does not support, exactly as a blog article is checked
    against the research it was written from. Claims the research cannot settle
    come back UNVERIFIABLE rather than being escalated to a web search.

    **Checking the deck is how the video is checked** — the MP4 is a render of
    the deck's narration, so a ``video`` material has nothing of its own to
    verify and is refused. Run this before ``generate-audio`` and
    ``assemble-video``, which spend real money on whatever the script says.

    Returns 202 + a job id; poll GET /media-library/jobs/{id}. Re-running
    replaces the material's claim rows.
    """
    material = db.query(EduMaterial).filter(EduMaterial.id == material_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type not in GROUNDABLE_MATERIAL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A {material.material_type} cannot be fact-checked directly; "
                "a video is a render of its deck's narration — fact-check the deck"
            ),
        )
    if material.material_type == MaterialType.GUIDE.value and not material.content_blocks:
        raise HTTPException(
            status_code=400,
            detail="Guide has no structured content blocks; regenerate it before fact-checking",
        )
    if material.material_type == MaterialType.SLIDES.value and not material.slides:
        raise HTTPException(
            status_code=400,
            detail="Deck has no slides; generate the deck before fact-checking",
        )

    try:
        job = create_fact_check_job(db, FactCheckSubject.EDU_MATERIAL.value, material.id)
    except FactCheckJobInFlightError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    background_tasks.add_task(run_fact_check_job, job.id)
    logger.info("Kicked off fact-check job %d for edu material %d", job.id, material.id)
    return job


@router.post(
    "/research/generate-prompt",
    response_model=EduResearchPromptResponse,
)
def generate_edu_prompt(
    body: EduResearchPromptRequest,
    db: Session = Depends(get_db),
):
    """Generate a tailored research prompt for an edu suggestion via Sonnet."""
    suggestion = db.query(EduSuggestion).filter(EduSuggestion.id == body.suggestion_id).first()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Edu suggestion not found")
    if suggestion.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Suggestion must be APPROVED")

    existing = db.query(EduResearch).filter(EduResearch.suggestion_id == suggestion.id).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="This suggestion already has a research record",
        )

    prompt_text = generate_edu_research_prompt(suggestion)

    return EduResearchPromptResponse(
        suggestion_id=suggestion.id,
        suggestion_title=suggestion.title,
        prompt_text=prompt_text,
    )


@router.post(
    "/research/upload",
    response_model=EduResearchUploadResponse,
)
async def upload_edu_research(
    file: UploadFile,
    suggestion_id: int = Form(..., description="Edu suggestion this research is for"),
    force: bool = Query(False, description="Overwrite even if existing research is APPROVED"),
    db: Session = Depends(get_db),
):
    """Upload an edu research document, validate it, and store in DB."""
    content = await read_upload_text(file)

    suggestion = db.query(EduSuggestion).filter(EduSuggestion.id == suggestion_id).first()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Edu suggestion not found")

    validation = validate_edu_research_document(content, suggestion)
    if not validation["valid"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Research document failed quality checks",
                "checks": validation["checks"],
                "word_count": validation["word_count"],
            },
        )

    final_content = validation["content"]

    existing = db.query(EduResearch).filter(EduResearch.suggestion_id == suggestion.id).first()

    now = datetime.now(UTC)
    if existing:
        # Re-uploading over APPROVED research would orphan any study guides
        # already generated from the old content. Block unless forced (#88).
        if existing.status == "APPROVED" and not force:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Research for this suggestion is already APPROVED. "
                    "Re-uploading would orphan generated materials. "
                    "Pass force=true to overwrite."
                ),
            )
        existing.content = final_content
        existing.status = "PENDING"  # content changed \u2014 must re-enter review
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        research = existing
    else:
        research = EduResearch(
            suggestion_id=suggestion.id,
            content=final_content,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        db.add(research)
        db.commit()
        db.refresh(research)

    return EduResearchUploadResponse(
        suggestion_id=suggestion.id,
        suggestion_title=suggestion.title,
        research_id=research.id,
        word_count=validation["word_count"],
        status=research.status,
        references_generated=validation["content"] != content,
    )


@router.get("/research/{research_id}/detail", response_model=EduResearchDetail)
def get_edu_research_detail(
    research_id: int = Path(..., description="Edu research ID"),
    db: Session = Depends(get_db),
):
    """Get edu research with content rendered as HTML."""
    research = (
        db.query(EduResearch)
        .options(joinedload(EduResearch.suggestion))
        .filter(EduResearch.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Edu research not found")

    return EduResearchDetail(
        id=research.id,
        suggestion_title=research.suggestion.title if research.suggestion else "Unknown",
        content_html=markdown_to_html(research.content or ""),
        status=research.status,
        created_at=research.created_at,
    )


@router.get("/research/{research_id}/download")
def download_edu_research(
    research_id: int = Path(..., description="Edu research ID"),
    db: Session = Depends(get_db),
):
    """Download edu research content as markdown."""
    research = (
        db.query(EduResearch)
        .options(joinedload(EduResearch.suggestion))
        .filter(EduResearch.id == research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Edu research not found")

    from unidecode import unidecode

    suggestion = research.suggestion
    suggestion_id = suggestion.id if suggestion else 0
    title_slug = unidecode(suggestion.title if suggestion else "unknown").replace(" ", "_")
    filename = f"edu_{suggestion_id}_{title_slug}_research.md"

    return PlainTextResponse(
        content=research.content or "",
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Materials ---


@router.get("/materials", response_model=list[EduMaterialListItem])
def list_edu_materials(
    db: Session = Depends(get_db),
):
    """List all educational materials."""
    rows = (
        db.query(EduMaterial)
        .options(
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
            joinedload(EduMaterial.blog_research).joinedload(Research.suggestion),
        )
        .order_by(EduMaterial.id.desc())
        .all()
    )
    # One cheap query instead of touching m.slides per row (which would lazy-load
    # every slide of every deck just to test emptiness).
    decks_with_slides = {row[0] for row in db.query(EduSlide.material_id).distinct()}

    return [
        EduMaterialListItem(
            id=m.id,
            suggestion_title=m.suggestion_title,
            title=m.title or "",
            material_type=m.material_type,
            grade_band=m.grade_band,
            file_url=m.file_url,
            thumbnail_url=m.thumbnail_url,
            status=m.status,
            published_at=m.published_at,
            has_spanish=bool((m.content_es or "").strip()) or bool(m.content_blocks_es),
            has_blocks=bool(m.content_blocks),
            has_slides=m.id in decks_with_slides,
            created_at=m.created_at,
        )
        for m in rows
    ]


@router.get(
    "/research/available-for-materials",
    response_model=list[EduResearchForMaterials],
)
def get_edu_research_for_materials(
    db: Session = Depends(get_db),
):
    """List APPROVED edu research that still has study guides to generate.

    A research qualifies while any of its suggestion's bands lacks an APPROVED
    guide — the generate job (re)writes exactly those bands, so a run that lost
    a band (e.g. a truncated 9th-12th) can be resumed from the modal instead of
    disappearing once the first band lands. ``bands_to_generate`` names them;
    PENDING/REJECTED guides are in it because the job overwrites them. Research
    with no bands configured is listed only until it has materials, so the
    modal can still say "None configured".
    """
    guide_status = {
        (research_id, band): status
        for research_id, band, status in db.query(
            EduMaterial.research_id, EduMaterial.grade_band, EduMaterial.status
        ).filter(EduMaterial.material_type == "guide", EduMaterial.research_id.isnot(None))
    }
    research_with_materials = {
        r[0] for r in db.query(EduMaterial.research_id).distinct().all() if r[0] is not None
    }

    approved_research = (
        db.query(EduResearch)
        .options(joinedload(EduResearch.suggestion))
        .filter(EduResearch.status == "APPROVED")
        .order_by(EduResearch.id)
        .all()
    )

    available = []
    for r in approved_research:
        grade_bands = (r.suggestion.grade_bands or []) if r.suggestion else []
        to_generate = [b for b in grade_bands if guide_status.get((r.id, b)) != "APPROVED"]
        if to_generate or (not grade_bands and r.id not in research_with_materials):
            available.append(
                EduResearchForMaterials(
                    id=r.id,
                    suggestion_id=r.suggestion_id,
                    suggestion_title=r.suggestion.title if r.suggestion else "Unknown",
                    grade_bands=grade_bands,
                    bands_to_generate=to_generate,
                )
            )
    return available


@router.post(
    "/materials/generate",
    response_model=CrawlJobResponse,
    status_code=202,
)
def generate_edu_materials_endpoint(
    body: EduMaterialGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Generate study guides for all grade bands from approved research.

    Runs as a background job (#346) — a 3-band run is several minutes of LLM
    work. Returns 202 + a job id; poll GET /media-library/jobs/{id}. On success
    ``stats`` is an ``EduMaterialGenerateResponse``. Bands commit as they finish
    (#90), so the materials grid fills in while the job runs.
    """
    research = (
        db.query(EduResearch)
        .options(joinedload(EduResearch.suggestion))
        .filter(EduResearch.id == body.research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Edu research not found")
    if research.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Research must be APPROVED")
    if not research.content:
        raise HTTPException(status_code=400, detail="Research has no content")

    if research.suggestion is None:
        raise HTTPException(status_code=400, detail="Research has no linked suggestion")

    return _kickoff_edu_job(
        db,
        background_tasks,
        CrawlJobType.EDU_GENERATE_GUIDES,
        {"research_id": research.id, "force": body.force},
    )


@router.get(
    "/materials/guides-for-slides",
    response_model=list[EduGuideForSlides],
)
def get_edu_guides_for_slides(
    db: Session = Depends(get_db),
):
    """List approved study guides that don't yet have slides for their (research_id, grade_band)."""
    # Find (research_id, grade_band) combos that already have slides
    existing_slides = {
        (r.research_id, r.grade_band)
        for r in db.query(EduMaterial.research_id, EduMaterial.grade_band)
        .filter(EduMaterial.material_type == "slides")
        .all()
    }

    approved_guides = (
        db.query(EduMaterial)
        .options(
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
        )
        .filter(
            EduMaterial.material_type == "guide",
            EduMaterial.status == "APPROVED",
        )
        .order_by(EduMaterial.id)
        .all()
    )

    return [
        EduGuideForSlides(
            id=g.id,
            research_id=g.research_id,
            suggestion_title=g.suggestion_title,
            grade_band=g.grade_band,
            title=g.title or "",
        )
        for g in approved_guides
        if (g.research_id, g.grade_band) not in existing_slides
    ]


def _validate_subset_positions(
    material: EduMaterial, positions: list[int] | None
) -> set[int] | None:
    """Turn a request's 1-based ``positions`` into a validated set for the deck.

    Returns ``None`` for a whole-deck operation. Raises 422 if any position is
    out of the deck's 1..N range (the request schema already deduped/sorted and
    rejected non-positive values; N is only known here).
    """
    if positions is None:
        return None
    valid = {s.position for s in material.slides}
    out_of_range = sorted(p for p in positions if p not in valid)
    if out_of_range:
        raise HTTPException(
            status_code=422,
            detail=(
                f"positions {out_of_range} are outside this deck's slides "
                f"(1..{len(material.slides)})"
            ),
        )
    return set(positions)


@router.post(
    "/materials/generate-slides",
    response_model=EduSlidesGenerateResponse,
)
def generate_edu_slides_endpoint(
    body: EduSlidesGenerateRequest,
    db: Session = Depends(get_db),
):
    """Generate a slide deck from an approved study guide."""
    guide = (
        db.query(EduMaterial)
        .options(joinedload(EduMaterial.research).joinedload(EduResearch.suggestion))
        .filter(EduMaterial.id == body.guide_material_id)
        .first()
    )
    if guide is None:
        raise HTTPException(status_code=404, detail="Study guide not found")
    if guide.material_type != "guide":
        raise HTTPException(status_code=400, detail="Material is not a study guide")
    if guide.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Study guide must be APPROVED")
    # The deck's facts come from the research now, not the guide text.
    if guide.research is None or not (guide.research.content or "").strip():
        raise HTTPException(status_code=400, detail="Guide's research has no content")

    slides, regenerated = generate_edu_slides(guide, db, force=body.force)
    if regenerated:
        invalidate_material_claims(db, slides.id)
    db.commit()

    return EduSlidesGenerateResponse(
        guide_material_id=guide.id,
        suggestion_title=guide.suggestion_title,
        grade_band=guide.grade_band,
        slides_material_id=slides.id,
        regenerated=regenerated,
    )


@router.post(
    "/materials/{material_id}/regenerate-slides",
    response_model=EduSlidesSubsetRegenerateResponse,
)
def regenerate_edu_slides_subset_endpoint(
    body: EduSlidesSubsetRegenerateRequest,
    material_id: int = Path(..., description="Slides-material ID"),
    db: Session = Depends(get_db),
):
    """Re-roll the content of a subset of an existing deck's slides.

    One LLM call rewrites only the requested ``positions`` in place (template /
    title / body / image_query), keeping the deck's slide count and every other
    slide untouched. Each re-rolled slide's stale rendered frame is cleared; its
    human photo pick, narration, and audio are preserved. Use this to improve a
    few slides without paying to regenerate — or re-review — the whole deck.
    """
    material = (
        db.query(EduMaterial)
        .options(
            selectinload(EduMaterial.slides),
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
        )
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type != "slides":
        raise HTTPException(status_code=400, detail="Material is not a slide deck")
    if not material.slides:
        raise HTTPException(status_code=400, detail="Material has no slides; generate them first")
    positions = _validate_subset_positions(material, body.positions)

    result = regenerate_slides_subset(material, positions, db)
    invalidate_material_claims(db, material.id)
    db.commit()

    return EduSlidesSubsetRegenerateResponse(
        material_id=material.id,
        suggestion_title=material.suggestion_title,
        grade_band=material.grade_band,
        positions=result["positions"],
        slides_regenerated=result["slides_regenerated"],
    )


@router.post(
    "/materials/{material_id}/render-slides",
    response_model=CrawlJobResponse,
    status_code=202,
)
def render_edu_slides_endpoint(
    background_tasks: BackgroundTasks,
    material_id: int = Path(..., description="Slides-material ID"),
    body: EduSlidesRenderRequest = Body(default_factory=EduSlidesRenderRequest),
    db: Session = Depends(get_db),
):
    """Render a slides deck's slides into branded 1280x720 PNG frames (#321).

    Resolves each slide's image, screenshots the brand HTML template with
    headless chromium, uploads the PNG to Spaces, and stores the frame URL on
    ``EduSlide.image_url`` (the per-slide artifact the video stage muxes). Pass
    ``positions`` to re-render only a subset of slides (e.g. after editing a few).

    Runs as a background job (#346); returns 202 + a job id, poll
    GET /media-library/jobs/{id}. On success ``stats`` is an
    ``EduSlidesRenderResponse``; frames are committed as each one uploads.
    """
    material = (
        db.query(EduMaterial)
        .options(
            selectinload(EduMaterial.slides),
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
        )
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type != "slides":
        raise HTTPException(status_code=400, detail="Material is not a slide deck")
    if not material.slides:
        raise HTTPException(status_code=400, detail="Material has no slides; generate them first")
    positions = _validate_subset_positions(material, body.positions)

    return _kickoff_edu_job(
        db,
        background_tasks,
        CrawlJobType.EDU_RENDER_SLIDES,
        {
            "material_id": material.id,
            "positions": sorted(positions) if positions is not None else None,
        },
    )


@router.post(
    "/materials/{material_id}/generate-audio",
    response_model=CrawlJobResponse,
    status_code=202,
)
def generate_edu_slide_audio_endpoint(
    background_tasks: BackgroundTasks,
    material_id: int = Path(..., description="Slides-material ID"),
    body: EduSlidesAudioRequest = Body(default_factory=EduSlidesAudioRequest),
    db: Session = Depends(get_db),
):
    """Synthesize Diego-voice narration audio for a slide deck (#322).

    One MP3 per narrated slide (ElevenLabs, cached + resumable), written to
    ``EduSlide.audio_url`` with its ``duration_ms`` — the per-slide audio the
    video stage muxes with the rendered frame. Pass ``positions`` to re-synth
    only a subset of slides (e.g. after editing a few narration scripts).

    Runs as a background job (#346); returns 202 + a job id, poll
    GET /media-library/jobs/{id}. On success ``stats`` is an
    ``EduSlidesAudioResponse``; each slide's audio is committed as it uploads.
    """
    material = (
        db.query(EduMaterial)
        .options(
            selectinload(EduMaterial.slides),
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
        )
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type != "slides":
        raise HTTPException(status_code=400, detail="Material is not a slide deck")
    if not material.slides:
        raise HTTPException(status_code=400, detail="Material has no slides; generate them first")
    positions = _validate_subset_positions(material, body.positions)
    scoped = [s for s in material.slides if positions is None or s.position in positions]
    if not any((s.narration or "").strip() for s in scoped):
        raise HTTPException(status_code=400, detail="Slides have no narration to synthesize")
    # ElevenLabs bills per slide — never synthesize a script known to be wrong.
    _enforce_fact_check_gate(db, material.id, STEP_INTERNAL, action="generate narration audio")

    return _kickoff_edu_job(
        db,
        background_tasks,
        CrawlJobType.EDU_GENERATE_AUDIO,
        {
            "material_id": material.id,
            "positions": sorted(positions) if positions is not None else None,
        },
    )


@router.post(
    "/materials/{material_id}/assemble-video",
    response_model=CrawlJobResponse,
    status_code=202,
)
def assemble_edu_slide_video_endpoint(
    background_tasks: BackgroundTasks,
    material_id: int = Path(..., description="Slides-material ID"),
    db: Session = Depends(get_db),
):
    """Assemble a slide deck's frames + narration into one narrated MP4 (#323).

    Requires the deck's slides to be both rendered (#321) and narrated (#322).
    Muxes per-slide clips (hard cuts, brief hold) → one 1280x720 H.264 video,
    uploaded to Spaces as a ``video`` EduMaterial.

    Runs as a background job (#346) — a full deck takes ~15-20 minutes of
    ffmpeg. Returns 202 + a job id, poll GET /media-library/jobs/{id}. On success
    ``stats`` is an ``EduVideoAssembleResponse``.
    """
    material = (
        db.query(EduMaterial)
        .options(
            selectinload(EduMaterial.slides),
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
        )
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type != "slides":
        raise HTTPException(status_code=400, detail="Material is not a slide deck")
    if not material.slides:
        raise HTTPException(status_code=400, detail="Material has no slides; generate them first")
    incomplete = [s.position for s in material.slides if not s.image_url or not s.audio_url]
    if incomplete:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Slides missing a rendered frame or narration audio at positions "
                f"{incomplete}; run render-slides and generate-audio first"
            ),
        )
    _enforce_fact_check_gate(db, material.id, STEP_INTERNAL, action="assemble the video")

    return _kickoff_edu_job(
        db, background_tasks, CrawlJobType.EDU_ASSEMBLE_VIDEO, {"material_id": material.id}
    )


@router.post(
    "/materials/{material_id}/generate-pdf",
    response_model=EduGuidePdfResponse,
)
def generate_edu_guide_pdf_endpoint(
    material_id: int = Path(..., description="Guide-material ID"),
    language: Literal["es", "en"] = Query("es", description="Which content column to render"),
    db: Session = Depends(get_db),
):
    """Render a study guide's markdown into a branded, watermarked PDF (#324).

    Spanish (``content_es``, the launch deliverable) by default; English
    (``content``) when ``language=en``. Sets ``file_url`` on the guide row.
    """
    material = (
        db.query(EduMaterial)
        .options(joinedload(EduMaterial.research).joinedload(EduResearch.suggestion))
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type != "guide":
        raise HTTPException(status_code=400, detail="Material is not a study guide")
    _enforce_fact_check_gate(db, material.id, STEP_INTERNAL, action="generate the PDF")
    blocks = material.content_blocks_es if language == "es" else material.content_blocks
    content_md = material.content_es if language == "es" else material.content
    if not blocks and not (content_md or "").strip():
        detail = (
            "Guide has no Spanish translation yet; translate it first"
            if language == "es"
            else "Guide has no English content"
        )
        raise HTTPException(status_code=400, detail=detail)

    try:
        generate_guide_pdf(material, db, language=language)
    except RuntimeError as e:
        # Browser binary missing / chromium launch failure — env error, not input.
        sentry_sdk.capture_exception(e)
        logger.exception("Guide PDF render engine unavailable for material id=%s", material_id)
        raise HTTPException(status_code=500, detail="PDF render engine unavailable") from e

    db.commit()

    return EduGuidePdfResponse(
        material_id=material.id,
        suggestion_title=material.suggestion_title,
        grade_band=material.grade_band,
        language=language,
        file_url=material.file_url or "",
    )


@router.post(
    "/materials/{material_id}/translate",
    response_model=EduGuideTranslateResponse,
)
async def translate_edu_guide(
    material_id: int = Path(..., description="Edu material ID"),
    force: bool = Query(False, description="Overwrite an existing Spanish translation"),
    formality: Literal["default", "more", "less", "prefer_more", "prefer_less"] = Query(
        "default",
        description="DeepL formality — 'less'=tú (informal), 'more'=usted (formal)",
    ),
    db: Session = Depends(get_db),
):
    """Translate an APPROVED study guide's content to Spanish via DeepL.

    Mirrors the blog article translate flow. Only ``guide`` materials are
    translated here — slide decks and narration are authored natively in Spanish
    (#320), not translated, so they read naturally in Diego's cloned voice.
    """
    material = (
        db.query(EduMaterial)
        .options(joinedload(EduMaterial.research).joinedload(EduResearch.suggestion))
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    if material.material_type != "guide":
        raise HTTPException(status_code=400, detail="Only study guides can be translated")
    if material.status != "APPROVED":
        raise HTTPException(
            status_code=400, detail="Study guide must be APPROVED before translating"
        )
    has_content = bool(material.content_blocks) or bool((material.content or "").strip())
    if not has_content:
        raise HTTPException(status_code=400, detail="Study guide has no content to translate")
    already_es = bool(material.content_blocks_es) or bool((material.content_es or "").strip())
    if already_es and not force:
        raise HTTPException(
            status_code=400,
            detail="Study guide already has Spanish content (pass force=true to overwrite)",
        )
    # Translating a known-wrong guide pays DeepL to produce a second copy of the
    # error, and a later correction would have to be applied twice.
    _enforce_fact_check_gate(db, material.id, STEP_INTERNAL, action="translate the guide")

    from app.services.edu_material_generation import (
        apply_spanish_block_overrides,
        blocks_to_markdown,
    )
    from app.services.translation import get_translation_service

    service = get_translation_service()
    if service is None:
        raise HTTPException(
            status_code=500, detail="Translation service unavailable (DEEPL_API_KEY not set)"
        )

    # Structured guides (#340): translate block text → ES blocks + markdown projection.
    # Legacy markdown-only guides fall back to the round-trip markdown path.
    if material.content_blocks:
        es_blocks = await service.translate_blocks(material.content_blocks, formality=formality)
        suggestion = material.research.suggestion if material.research else None
        apply_spanish_block_overrides(
            material.content_blocks,
            es_blocks,
            suggestion.excursion_key if suggestion else None,
        )
        material.content_blocks_es = es_blocks
        material.content_es = blocks_to_markdown(es_blocks)
    else:
        material.content_es = await service.translate_markdown(
            material.content, formality=formality
        )

    db.commit()

    return EduGuideTranslateResponse(
        material_id=material.id,
        grade_band=material.grade_band,
        word_count=len((material.content_es or "").split()),
    )


@router.get("/coverage", response_model=list[ExcursionCoverageItem])
def excursion_coverage(db: Session = Depends(get_db)):
    """Coverage matrix — owed vs produced per excursion × grade band (#319).

    Owed combos come from the canonical taxonomy (``app/core/excursions.py``);
    produced/published status is read from ``edu_materials`` joined back through
    research → suggestion → ``excursion_key``. ``video_status`` stays None until
    the ``video`` material_type lands (#323).
    """
    # excursion_key -> [suggestion_id]
    key_to_sugg_ids: dict[str, list[int]] = {}
    for sid, ekey in db.query(EduSuggestion.id, EduSuggestion.excursion_key).all():
        if ekey:
            key_to_sugg_ids.setdefault(ekey, []).append(sid)

    # suggestion_id -> [(research_id, status)]
    sugg_to_research: dict[int, list[tuple[int, str]]] = {}
    for rid, sid, rstatus in db.query(
        EduResearch.id, EduResearch.suggestion_id, EduResearch.status
    ).all():
        sugg_to_research.setdefault(sid, []).append((rid, rstatus))

    all_research_ids = [rid for rows in sugg_to_research.values() for (rid, _) in rows]

    # (research_id, material_type, grade_band) -> status
    mat_status: dict[tuple[int, str, str | None], str] = {}
    if all_research_ids:
        for rid, mtype, band, status in (
            db.query(
                EduMaterial.research_id,
                EduMaterial.material_type,
                EduMaterial.grade_band,
                EduMaterial.status,
            )
            .filter(EduMaterial.research_id.in_(all_research_ids))
            .all()
        ):
            mat_status[(rid, mtype, band)] = status

    def first_status(research_ids: list[int], material_type: str, band: str) -> str | None:
        for rid in research_ids:
            status = mat_status.get((rid, material_type, band))
            if status:
                return status
        return None

    items: list[ExcursionCoverageItem] = []
    for exc in EXCURSIONS:
        sugg_ids = key_to_sugg_ids.get(exc.key, [])
        research = [r for sid in sugg_ids for r in sugg_to_research.get(sid, [])]
        research_ids = [rid for (rid, _) in research]
        has_research = any(status == "APPROVED" for (_, status) in research)
        for band in exc.grade_bands:
            items.append(
                ExcursionCoverageItem(
                    excursion_key=exc.key,
                    public_key=exc.public_key,
                    grade_band=band,
                    has_suggestion=bool(sugg_ids),
                    has_research=has_research,
                    guide_status=first_status(research_ids, "guide", band),
                    slides_status=first_status(research_ids, "slides", band),
                    video_status=first_status(research_ids, "video", band),
                )
            )
    return items


def _load_material_with_suggestion(material_id: int, db: Session) -> EduMaterial:
    material = (
        db.query(EduMaterial)
        .options(
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
            joinedload(EduMaterial.blog_research).joinedload(Research.suggestion),
            selectinload(EduMaterial.slides),
        )
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")
    return material


def _guide_pdf_urls(material: EduMaterial) -> dict[str, str | None] | None:
    """Both languages' PDF URLs for a guide that has rendered at least one.

    file_url only remembers the last render, so the other language is probed
    with a HEAD at its deterministic key. Storage trouble degrades to None
    (the drawer falls back to file_url) rather than failing the detail load.
    """
    if material.material_type != "guide" or not material.file_url:
        return None
    from app.services.image_storage import ImageStorageService

    try:
        with ImageStorageService() as storage:
            urls = {}
            for language in ("en", "es"):
                key = guide_pdf_key(material, language)
                urls[language] = storage.public_url(key) if storage.object_exists(key) else None
            return urls
    except Exception:
        logger.warning("Could not probe guide PDFs for material id=%s", material.id, exc_info=True)
        return None


def _material_detail(material: EduMaterial, db: Session) -> EduMaterialDetail:
    suggestion = material.research.suggestion if material.research else None
    # A deck's assembled MP4 lives on a sibling video material (assemble-video
    # upserts on (research_id, "video", grade_band)) — surface it so the deck
    # editor can link the video without the admin hunting for the sibling row.
    video = None
    if material.material_type == "slides" and material.research_id is not None:
        video = (
            db.query(EduMaterial)
            .filter(
                EduMaterial.research_id == material.research_id,
                EduMaterial.material_type == "video",
                EduMaterial.grade_band == material.grade_band,
            )
            .first()
        )
    return EduMaterialDetail(
        id=material.id,
        suggestion_title=material.suggestion_title,
        research_id=material.research_id,
        blog_research_id=material.blog_research_id,
        material_type=material.material_type,
        grade_band=material.grade_band,
        title=material.title or "",
        content_html=markdown_to_html(material.content or ""),
        content_es_html=markdown_to_html(material.content_es or ""),
        content_blocks=material.content_blocks,
        content_blocks_es=material.content_blocks_es,
        excursion_key=suggestion.excursion_key if suggestion else None,
        has_spanish=bool((material.content_es or "").strip()) or bool(material.content_blocks_es),
        file_url=material.file_url,
        pdf_urls=_guide_pdf_urls(material),
        thumbnail_url=material.thumbnail_url,
        status=material.status,
        published_at=material.published_at,
        created_at=material.created_at,
        slides=(
            [EduSlideOut.model_validate(s) for s in material.slides]
            if material.material_type == "slides"
            else None
        ),
        video_material_id=video.id if video else None,
        video_url=video.file_url if video else None,
        video_duration_ms=video.duration_ms if video else None,
    )


@router.get("/materials/{material_id}/detail", response_model=EduMaterialDetail)
def get_edu_material_detail(
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Get edu material with rendered HTML plus the editable structured blocks."""
    return _material_detail(_load_material_with_suggestion(material_id, db), db)


@router.put("/materials/{material_id}/content", response_model=EduMaterialDetail)
def update_edu_material_content(
    payload: EduMaterialContentUpdate,
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Save admin edits to a guide's title and/or structured content blocks.

    Guide-only (other material types are file uploads with no editable content).
    Blocks are validated with the strict normalizer — a block that would be
    dropped rejects the whole request with a 422 naming it, so a UI bug can't
    silently destroy content. The markdown projections (``content`` /
    ``content_es``) are regenerated from the saved blocks. The existing PDF is
    untouched — re-rendering is the explicit generate-pdf action.
    """
    from app.services.edu_material_generation import blocks_to_markdown, normalize_guide_blocks

    material = _load_material_with_suggestion(material_id, db)
    if material.material_type != "guide":
        raise HTTPException(status_code=400, detail="Only study guides have editable content")

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="title cannot be empty")
        material.title = title[:255]

    for blocks_field, md_field in (
        ("content_blocks", "content"),
        ("content_blocks_es", "content_es"),
    ):
        raw = getattr(payload, blocks_field)
        if raw is None:
            continue
        try:
            blocks = normalize_guide_blocks(raw, strict=True)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if not blocks:
            raise HTTPException(status_code=422, detail=f"{blocks_field} cannot be empty")
        setattr(material, blocks_field, blocks)
        setattr(material, md_field, blocks_to_markdown(blocks))

    # The saved text is not the text the claims describe (#462).
    invalidate_material_claims(db, material.id)
    material.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(material)
    return _material_detail(material, db)


@router.put("/materials/{material_id}/slides", response_model=EduMaterialDetail)
def update_edu_material_slides(
    payload: EduSlidesUpdate,
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Save admin edits to a slide deck: full ordered slide list (+ optional title).

    Mirror of the guide content PUT: slides are validated with the strict
    normalizer (a slide that would be dropped rejects the whole request with a
    422 naming it), the plain-text preview on ``content`` is regenerated, and
    status is left untouched. Rendered/audio artifacts are carried over from the
    existing row (matched by ``id``) only while the content they were built from
    is unchanged — an edited slide gets a fresh render/audio on the next run.
    Artifact fields are never accepted from the client.
    """
    from app.services.edu_material_generation import (
        normalize_slides_for_update,
        slides_preview_text,
    )

    material = _load_material_with_suggestion(material_id, db)
    if material.material_type != "slides":
        raise HTTPException(status_code=400, detail="Only slide decks have editable slides")

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="title cannot be empty")
        material.title = title[:255]

    try:
        slides = normalize_slides_for_update([s.model_dump() for s in payload.slides], strict=True)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not slides:
        raise HTTPException(status_code=422, detail="slides cannot be empty")

    # Snapshot the existing rows BEFORE clearing (delete-orphan) so artifact
    # carry-over compares against real values, not expired ORM objects.
    old_by_id = {
        s.id: {
            "template": s.template,
            "title": s.title,
            "body": s.body,
            "media_path": s.media_path,
            "narration": s.narration,
            "image_url": s.image_url,
            "audio_url": s.audio_url,
            "duration_ms": s.duration_ms,
        }
        for s in material.slides
    }
    material.slides.clear()
    db.flush()  # delete old rows first — reordering would collide on (material_id, position)

    for position, s in enumerate(slides, start=1):
        old = old_by_id.get(s["id"]) if s["id"] is not None else None
        image_url = audio_url = duration_ms = None
        if old is not None:
            frame_source_changed = (
                old["template"] != s["template"]
                or old["title"] != s["title"]
                or old["body"] != s["body"]
                or old["media_path"] != s["media_path"]
            )
            if not frame_source_changed:
                image_url = old["image_url"]
            if old["narration"] == s["narration"]:
                audio_url = old["audio_url"]
                duration_ms = old["duration_ms"]
        material.slides.append(
            EduSlide(
                position=position,
                template=s["template"],
                title=s["title"],
                body=s["body"],
                image_query=s["image_query"],
                media_id=s["media_id"],
                media_path=s["media_path"],
                narration=s["narration"],
                image_url=image_url,
                audio_url=audio_url,
                duration_ms=duration_ms,
            )
        )

    material.content = slides_preview_text(
        [{"title": s["title"], "body": s["body"], "narration": s["narration"]} for s in slides]
    )
    # The saved deck is not the deck the claims describe (#462).
    invalidate_material_claims(db, material.id)
    material.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(material)
    return _material_detail(material, db)


@router.post(
    "/materials/{material_id}/generate-narration",
    response_model=EduNarrationGenerateResponse,
)
def generate_edu_narration_endpoint(
    body: EduNarrationGenerateRequest,
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Write Diego's narration over an approved slide deck (stage 2 of the flow).

    Gated on the deck being APPROVED — the script is written against the FINAL
    slides, so approve the edited content first. A whole-deck rewrite that would
    replace existing narration returns 409 unless ``force`` is set (it clears the
    per-slide audio, which no longer matches the script). Pass ``positions`` to
    re-roll only a subset of slides — an inherently intentional edit that skips
    the 409 guard and touches only those slides' audio.
    """
    material = _load_material_with_suggestion(material_id, db)
    if material.material_type != "slides":
        raise HTTPException(status_code=400, detail="Material is not a slide deck")
    if not material.slides:
        raise HTTPException(status_code=400, detail="Material has no slides")
    if material.status != "APPROVED":
        raise HTTPException(
            status_code=400,
            detail="Approve the deck's content before generating narration",
        )
    positions = _validate_subset_positions(material, body.positions)
    if positions is None and any(s.narration for s in material.slides) and not body.force:
        raise HTTPException(
            status_code=409,
            detail="Deck already has narration — pass force=true to regenerate",
        )

    result = generate_edu_narration(material, db, force=body.force, positions=positions)
    invalidate_material_claims(db, material.id)
    db.commit()

    return EduNarrationGenerateResponse(
        material_id=material.id,
        suggestion_title=material.suggestion_title,
        grade_band=material.grade_band,
        slides_narrated=result["slides_narrated"],
        total_words=result["total_words"],
    )


@router.get("/materials/{material_id}/download")
def download_edu_material(
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Download edu material content as markdown."""
    material = (
        db.query(EduMaterial)
        .options(
            joinedload(EduMaterial.research).joinedload(EduResearch.suggestion),
            joinedload(EduMaterial.blog_research).joinedload(Research.suggestion),
        )
        .filter(EduMaterial.id == material_id)
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Edu material not found")

    from unidecode import unidecode

    title_slug = unidecode(material.title or "guide").replace(" ", "_")[:60]
    band_slug = (material.grade_band or "none").replace("-", "_")
    filename = f"edu_{band_slug}_{title_slug}.md"

    return PlainTextResponse(
        content=material.content or "",
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch(
    "/materials/{material_id}/status",
    response_model=StatusUpdateResponse,
)
def update_edu_material_status(
    material_id: int = Path(..., description="Edu material ID"),
    body: EduStatusUpdateRequest = ...,
    db: Session = Depends(get_db),
):
    """Update the status of an educational material."""
    record = db.query(EduMaterial).filter(EduMaterial.id == material_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Edu material not found")

    if body.status not in VALID_TRANSITIONS.get(record.status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition from {record.status} to {body.status}",
        )

    record.status = body.status
    db.commit()

    return StatusUpdateResponse(id=record.id, status=record.status)


# --- Blog Research Material Upload ---

ALLOWED_MATERIAL_TYPES = {"slides", "infographic", "podcast"}
EXT_MAP = {
    "slides": ".pdf",
    "infographic": ".png",
    "podcast": ".m4a",
}

# Magic-byte signatures used to verify the uploaded file actually matches the
# declared material_type before we watermark/store it (#94). Without this an
# operator could upload e.g. a PDF declared as an infographic, fail watermarking,
# and (previously) ship a garbage row under a .png key.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PDF_SIGNATURE = b"%PDF"


def _validate_material_bytes(material_type: str, file_bytes: bytes) -> None:
    """Reject an upload whose contents don't match the declared material_type."""
    if material_type == "infographic" and not file_bytes.startswith(_PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="infographic must be a PNG image")
    if material_type == "slides" and not file_bytes.startswith(_PDF_SIGNATURE):
        raise HTTPException(status_code=400, detail="slides must be a PDF document")
    if material_type == "podcast":
        # ISO Base Media (m4a/mp4): a 'ftyp' box at offset 4.
        if len(file_bytes) < 12 or file_bytes[4:8] != b"ftyp":
            raise HTTPException(status_code=400, detail="podcast must be an m4a/MP4 audio file")


@router.get(
    "/blog-research-for-upload",
    response_model=list[BlogResearchForUpload],
)
def get_blog_research_for_upload(
    db: Session = Depends(get_db),
):
    """List approved blog research available for material upload."""
    approved = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.status == "APPROVED")
        .order_by(Research.id)
        .all()
    )

    return [
        BlogResearchForUpload(
            id=r.id,
            suggestion_title=r.suggestion.title if r.suggestion else "Unknown",
        )
        for r in approved
    ]


@router.post(
    "/materials/upload",
    response_model=MaterialUploadResponse,
)
def upload_material(
    material_type: str = Form(..., description="slides, infographic, or podcast"),
    blog_research_id: int = Form(..., description="Blog research ID"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a material file (slides/infographic/podcast) from blog research."""
    if material_type not in ALLOWED_MATERIAL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type: {material_type}")

    research = (
        db.query(Research)
        .options(joinedload(Research.suggestion))
        .filter(Research.id == blog_research_id)
        .first()
    )
    if research is None:
        raise HTTPException(status_code=404, detail="Blog research not found")
    if research.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Research must be APPROVED")

    # Check for duplicate
    existing = (
        db.query(EduMaterial)
        .filter(
            EduMaterial.blog_research_id == blog_research_id,
            EduMaterial.material_type == material_type,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A {material_type} already exists for this research",
        )

    # Read file (size-capped; #89)
    file_bytes = read_upload_bytes(file, MAX_MATERIAL_UPLOAD_BYTES)

    # Reject files that don't match the declared type before watermarking (#94).
    _validate_material_bytes(material_type, file_bytes)

    suggestion = research.suggestion
    suggestion_id = suggestion.id if suggestion else 0

    from slugify import slugify
    from unidecode import unidecode

    title_slug = slugify(unidecode(suggestion.title if suggestion else "unknown"))[:60]
    ext = EXT_MAP[material_type]
    storage_key = f"materials/{material_type}s/{suggestion_id}_{title_slug}{ext}"

    from app.services.image_storage import (
        ImageStorageService,
        strip_nlm_watermark,
        watermark_infographic,
        watermark_slides,
    )

    storage = ImageStorageService()
    content_type = file.content_type or "application/octet-stream"

    # Infographics come from NotebookLM with a "Made with NotebookLM" watermark in
    # the bottom-right corner. Strip it before applying our own tag (#312). Like
    # watermarking, a failure aborts the upload — never ship an NLM-branded file.
    if material_type == "infographic":
        try:
            file_bytes = strip_nlm_watermark(file_bytes)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("NLM watermark removal failed")
            raise HTTPException(status_code=500, detail="Watermark removal failed") from e

    # Watermark is the project's copyright/branding marker and is mandatory for
    # slides/infographics. A failure must abort the upload — never silently ship
    # an un-watermarked file (#94). A missing watermarking dependency (ImportError)
    # is a deploy error, not a per-file content error, so surface it distinctly.
    watermarker = {
        "infographic": watermark_infographic,
        "slides": watermark_slides,
    }.get(material_type)
    if watermarker is not None:
        try:
            file_bytes = watermarker(file_bytes)
        except ImportError as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Watermarking dependency unavailable for %s", material_type)
            raise HTTPException(
                status_code=500, detail="Watermarking dependency unavailable"
            ) from e
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Watermarking failed for %s", material_type)
            raise HTTPException(status_code=500, detail="Watermarking failed") from e

    file_url = storage.upload_image(file_bytes, storage_key, content_type)

    # Generate thumbnail for slides and infographics
    thumbnail_url = None
    if material_type in ("infographic", "slides"):
        thumbnail_url = _generate_material_thumbnail(
            storage,
            file_bytes,
            storage_key,
            material_type,
        )

    now = datetime.now(UTC)
    material = EduMaterial(
        blog_research_id=blog_research_id,
        material_type=material_type,
        title=suggestion.title if suggestion else "Untitled",
        file_url=file_url,
        thumbnail_url=thumbnail_url,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    return MaterialUploadResponse(
        id=material.id,
        material_type=material_type,
        suggestion_title=suggestion.title if suggestion else "Unknown",
        file_url=file_url,
        thumbnail_url=thumbnail_url,
    )


def _generate_material_thumbnail(storage, file_bytes, storage_key, material_type):
    """Generate a thumbnail for an uploaded material."""
    if material_type == "infographic":
        return storage.generate_thumbnail(file_bytes, storage_key)

    if material_type == "slides":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc[0]
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            doc.close()
            return storage.generate_thumbnail(img_bytes, storage_key)
        except Exception as e:
            logger.warning("PDF thumbnail failed: %s", e)
            return None


# --- Publish / Unpublish (public delivery surface, #326) ---
#
# Publication is separate from approval (mirrors the blog's Article.published_at):
# the public delivery API shows a material only when it is APPROVED *and*
# published_at is set and in the past. Publish sets the timestamp (now, or a
# future value to schedule); unpublish clears it.


@router.patch("/materials/{material_id}/publish", response_model=EduPublishResponse)
def publish_edu_material(
    material_id: int = Path(..., description="Edu material ID"),
    body: EduPublishRequest | None = None,
    db: Session = Depends(get_db),
):
    """Publish an APPROVED material to the public delivery surface (#326)."""
    material = db.query(EduMaterial).filter(EduMaterial.id == material_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    if material.status != "APPROVED":
        raise HTTPException(status_code=400, detail="Only approved materials can be published")
    # The public gate is stricter: open questions block here, not just known
    # errors. Publishing a deck also makes its assembled video live (#326).
    _enforce_fact_check_gate(db, material.id, STEP_PUBLIC, action="publish this material")

    material.published_at = (body.published_at if body else None) or datetime.now(UTC)
    db.commit()
    db.refresh(material)
    return EduPublishResponse(
        id=material.id, status=material.status, published_at=material.published_at
    )


@router.patch("/materials/{material_id}/unpublish", response_model=EduPublishResponse)
def unpublish_edu_material(
    material_id: int = Path(..., description="Edu material ID"),
    db: Session = Depends(get_db),
):
    """Remove a material from the public delivery surface (clears published_at)."""
    material = db.query(EduMaterial).filter(EduMaterial.id == material_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")

    material.published_at = None
    db.commit()
    db.refresh(material)
    return EduPublishResponse(
        id=material.id, status=material.status, published_at=material.published_at
    )

    return None
