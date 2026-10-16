"""Background runner for the long edu pipeline steps (#346).

Guide generation, slide rendering, narration audio and video assembly used to
run as blocking requests. A full deck's ``assemble-video`` takes ~15-20
minutes and 504'd at the ``api.`` vhost's 300s proxy ceiling (the 1200s #345
raised is on the site's own ``/api/`` location, which prod browsers never use).
They now follow the #277/#384 shape: the endpoint validates synchronously
(404/400/409 still surface at once), creates a :class:`CrawlJob` row, schedules
:func:`run_edu_job` as a ``BackgroundTask`` and returns ``202`` + the job id.
The client polls ``GET /media-library/jobs/{id}``. On success ``job.stats`` holds
exactly the body the old synchronous endpoint returned, so a client can hand
the same shape to its caller.

In-flight rule: one edu job per subject (a material, or the research for guide
generation). An **identical** request — same type, same params — joins the
running job instead of failing, so re-clicking after a page reload resumes
polling rather than 409ing; any other edu job on the same subject is refused,
because two of these steps racing over one deck's slide rows (a re-render
while the video is muxing its frames) produce an inconsistent artifact.

The runner is a plain ``def`` on purpose: Starlette runs sync background tasks
in the threadpool, where there is no running event loop, which is what the
**sync** Playwright API behind ``render-slides`` requires.

Its session uses ``expire_on_commit=False`` and every ``progress`` callback
commits, so the connection goes back to the pool between slides instead of
being pinned through minutes of chromium / ElevenLabs / ffmpeg work (the
Connection Pool rule in CLAUDE.md), and each rendered frame or synthesized MP3
is durable as soon as it lands.
"""

import logging
from datetime import UTC, datetime

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import SessionLocal
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.schemas.edu import (
    EduMaterialGenerateResponse,
    EduSlidesAudioResponse,
    EduSlidesRenderResponse,
    EduVideoAssembleResponse,
)
from app.services.audio_generation import generate_slide_audio
from app.services.crawl_jobs import STALE_AFTER
from app.services.edu_material_generation import generate_edu_materials
from app.services.slide_render import render_material_slides
from app.services.video_assembly import assemble_slide_video

logger = logging.getLogger(__name__)

EDU_JOB_TYPES = (
    CrawlJobType.EDU_GENERATE_GUIDES.value,
    CrawlJobType.EDU_RENDER_SLIDES.value,
    CrawlJobType.EDU_GENERATE_AUDIO.value,
    CrawlJobType.EDU_ASSEMBLE_VIDEO.value,
)

_ACTIVE_STATUSES = (CrawlJobStatus.PENDING.value, CrawlJobStatus.RUNNING.value)
_MAX_ERROR_LEN = 2000


class EduJobInFlightError(RuntimeError):
    """A different edu job is already running for this subject.

    Carries the in-flight job so the API layer can name it in the 409.
    """

    def __init__(self, job: CrawlJob):
        self.job = job
        super().__init__(
            f"Job {job.id} ({job.job_type}) is already running for this item; wait for it to finish"
        )


def _subject_key(params: dict) -> tuple[str, int] | None:
    for key in ("material_id", "research_id"):
        if params.get(key) is not None:
            return key, params[key]
    return None


def active_edu_job(db: Session, subject: tuple[str, int]) -> CrawlJob | None:
    """The in-flight edu job for ``subject`` (``("material_id", id)`` or
    ``("research_id", id)``), or ``None``. Jobs older than ``STALE_AFTER`` are
    presumed dead (their worker was recycled) and never block."""
    cutoff = datetime.now(UTC) - STALE_AFTER
    stmt = (
        select(CrawlJob)
        .where(
            CrawlJob.job_type.in_(EDU_JOB_TYPES),
            CrawlJob.status.in_(_ACTIVE_STATUSES),
            CrawlJob.created_at >= cutoff,
        )
        .order_by(CrawlJob.created_at.desc())
    )
    for job in db.execute(stmt).scalars():
        if _subject_key(job.params or {}) == subject:
            return job
    return None


def create_edu_job(db: Session, job_type: CrawlJobType, params: dict) -> tuple[CrawlJob, bool]:
    """Create and commit an edu job row, or join an identical in-flight one.

    Returns ``(job, created)``; the caller schedules :func:`run_edu_job` only
    when ``created``. Raises :class:`EduJobInFlightError` when a *different*
    edu job holds the same subject.
    """
    subject = _subject_key(params)
    if subject is None:
        raise ValueError("edu job params need a material_id or research_id")
    existing = active_edu_job(db, subject)
    if existing is not None:
        if existing.job_type == job_type.value and (existing.params or {}) == params:
            return existing, False
        raise EduJobInFlightError(existing)
    job = CrawlJob(job_type=job_type.value, params=params)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True


def _load_deck(db: Session, material_id: int) -> EduMaterial:
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
        raise ValueError(f"Edu material {material_id} no longer exists")
    return material


def _positions(params: dict) -> set[int] | None:
    positions = params.get("positions")
    return set(positions) if positions is not None else None


def _run_for_type(db: Session, job: CrawlJob, progress) -> dict:
    """Run the job's step and return the old synchronous endpoint's body."""
    params = job.params or {}

    if job.job_type == CrawlJobType.EDU_GENERATE_GUIDES.value:
        research = (
            db.query(EduResearch)
            .options(joinedload(EduResearch.suggestion))
            .filter(EduResearch.id == params["research_id"])
            .first()
        )
        if research is None or research.suggestion is None:
            raise ValueError(f"Edu research {params['research_id']} or its suggestion is gone")
        suggestion = research.suggestion
        # Commits per band (#90); a failed band is rolled back and reported.
        materials, skipped_bands, failed_bands = generate_edu_materials(
            research, suggestion, db, force=params.get("force", False), progress=progress
        )
        return EduMaterialGenerateResponse(
            research_id=research.id,
            suggestion_title=suggestion.title,
            materials_created=len(materials),
            grade_bands=[m.grade_band for m in materials],
            material_ids=[m.id for m in materials],
            skipped_bands=skipped_bands,
            failed_bands=failed_bands,
        ).model_dump()

    material = _load_deck(db, params["material_id"])

    if job.job_type == CrawlJobType.EDU_RENDER_SLIDES.value:
        summary = render_material_slides(
            material, db, positions=_positions(params), progress=progress
        )
        db.commit()
        return EduSlidesRenderResponse(
            material_id=material.id,
            suggestion_title=material.suggestion_title,
            grade_band=material.grade_band,
            slides_rendered=summary["rendered"],
            images_resolved=summary["images_resolved"],
            images_missing=summary["images_missing"],
            slide_image_urls=[s.image_url for s in material.slides if s.image_url],
        ).model_dump()

    if job.job_type == CrawlJobType.EDU_GENERATE_AUDIO.value:
        summary = generate_slide_audio(
            material, db, positions=_positions(params), progress=progress
        )
        db.commit()
        return EduSlidesAudioResponse(
            material_id=material.id,
            suggestion_title=material.suggestion_title,
            grade_band=material.grade_band,
            slides_narrated=summary["narrated"],
            skipped_no_narration=summary["skipped_no_narration"],
            total_duration_ms=summary["total_duration_ms"],
        ).model_dump()

    if job.job_type == CrawlJobType.EDU_ASSEMBLE_VIDEO.value:
        video = assemble_slide_video(material, db, progress=progress)
        db.commit()
        return EduVideoAssembleResponse(
            slides_material_id=material.id,
            video_material_id=video.id,
            suggestion_title=material.suggestion_title,
            grade_band=material.grade_band,
            file_url=video.file_url or "",
            duration_ms=video.duration_ms or 0,
            slide_count=len(material.slides),
        ).model_dump()

    raise ValueError(f"Unknown edu job type: {job.job_type!r}")


def run_edu_job(job_id: int) -> None:
    """Execute an edu pipeline job to completion, persisting progress and result.

    Runs in a FastAPI ``BackgroundTask`` with its own DB session. Never raises —
    failures land on the job row and go to Sentry. Mirrors
    ``crawl_jobs.run_crawl_job`` / ``fact_check.run_fact_check_job``.
    """
    db = SessionLocal(expire_on_commit=False)
    try:
        job = db.get(CrawlJob, job_id)
        if job is None:
            logger.error("Edu job %d not found at execution time", job_id)
            return

        job.status = CrawlJobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        db.commit()

        def progress(done: int, total: int, current: str) -> None:
            job.units_done = done
            job.units_total = total
            job.current_unit = current[:500] if current else None
            db.commit()

        stats = _run_for_type(db, job, progress)

        job.stats = stats
        job.status = CrawlJobStatus.SUCCEEDED.value
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("Edu job %d (%s) succeeded", job_id, job.job_type)

    except Exception as e:
        logger.exception("Edu job %d failed", job_id)
        sentry_sdk.capture_exception(e)
        try:
            db.rollback()
            job = db.get(CrawlJob, job_id)
            if job is not None:
                job.status = CrawlJobStatus.FAILED.value
                job.error = str(e)[:_MAX_ERROR_LEN]
                job.finished_at = datetime.now(UTC)
                db.commit()
        except Exception:
            logger.exception("Failed to record edu job %d failure", job_id)
    finally:
        db.close()
