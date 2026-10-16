"""Background runner for Spanish article narration (#547).

``POST /admin/dashboard/articles/{id}/generate-audio`` used to narrate inside
the request. A large article is ~130 sequential ElevenLabs calls plus the
per-segment post-processing (~30-90 s on the droplet, measured on #547), far
past the ``api.`` vhost's 300s proxy ceiling, so the browser saw a 504 while
the server carried on. It now follows the #346 edu-job shape: the endpoint
validates synchronously (404 / 400, including "no changed segments" via
:func:`plan_narration`), creates a :class:`CrawlJob` and schedules
:func:`run_article_narration_job`, returning ``202`` + the job id. The client
polls ``GET /media-library/jobs/{id}``; on success ``job.stats`` is exactly the
``ArticleAudioResponse`` body the synchronous endpoint returned.

In-flight rule: one narration job per article. An **identical** request (same
mode) joins the running job, so re-clicking after a reload resumes polling;
a different mode while one runs is refused — two runs racing over one
article's segment objects would delete each other's "orphans".

Connection pool: the runner's session uses ``expire_on_commit=False`` and
commits on every progress tick, so no connection is pinned across the TTS and
ffmpeg work (the Connection Pool rule in CLAUDE.md).
"""

import logging
from datetime import UTC, datetime

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.schemas.dashboard import ArticleAudioResponse
from app.services.article_audio import store_article_audio, summarize_qa, synthesize_article
from app.services.crawl_jobs import STALE_AFTER

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (CrawlJobStatus.PENDING.value, CrawlJobStatus.RUNNING.value)
_MAX_ERROR_LEN = 2000


class NarrationJobInFlightError(RuntimeError):
    """A narration job with a different mode is already running for the article."""

    def __init__(self, job: CrawlJob):
        self.job = job
        mode = (job.params or {}).get("mode")
        super().__init__(
            f"Job {job.id} (mode={mode}) is already narrating this article; wait for it to finish"
        )


def active_narration_job(db: Session, article_id: int) -> CrawlJob | None:
    """The in-flight narration job for ``article_id``, or ``None``. Jobs older
    than ``STALE_AFTER`` are presumed dead (their worker was recycled)."""
    cutoff = datetime.now(UTC) - STALE_AFTER
    stmt = (
        select(CrawlJob)
        .where(
            CrawlJob.job_type == CrawlJobType.ARTICLE_NARRATION.value,
            CrawlJob.status.in_(_ACTIVE_STATUSES),
            CrawlJob.created_at >= cutoff,
        )
        .order_by(CrawlJob.created_at.desc())
    )
    for job in db.execute(stmt).scalars():
        if (job.params or {}).get("article_id") == article_id:
            return job
    return None


def create_narration_job(db: Session, article_id: int, mode: str) -> tuple[CrawlJob, bool]:
    """Create and commit a narration job, or join an identical in-flight one.

    Returns ``(job, created)``; the caller schedules the runner only when
    ``created``. Raises :class:`NarrationJobInFlightError` for a different mode.
    """
    params = {"article_id": article_id, "mode": mode}
    existing = active_narration_job(db, article_id)
    if existing is not None:
        if (existing.params or {}) == params:
            return existing, False
        raise NarrationJobInFlightError(existing)
    job = CrawlJob(job_type=CrawlJobType.ARTICLE_NARRATION.value, params=params)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True


def _narrate(db: Session, job: CrawlJob) -> dict:
    params = job.params or {}
    article_id = params["article_id"]
    article = db.get(Article, article_id)
    if article is None:
        raise ValueError(f"Article {article_id} no longer exists")
    title = article.title_es or article.title
    content_md = article.content_es or ""
    old_url = article.audio_url_es
    manifest = article.audio_manifest_es
    db.commit()  # release the connection before minutes of TTS + ffmpeg

    def progress(done: int, total: int, current: str) -> None:
        job.units_done = done
        job.units_total = total
        job.current_unit = current[:500] if current else None
        db.commit()

    result = synthesize_article(
        article_id,
        title,
        content_md,
        manifest=manifest,
        mode=params.get("mode", "all"),
        progress=progress,
    )
    url = store_article_audio(article_id, result.mp3, old_url)

    # populate_existing: with expire_on_commit=False a plain get() would hand
    # back the cached object even if the row was deleted meanwhile.
    article = db.get(Article, article_id, populate_existing=True)
    if article is None:  # deleted while narrating
        raise ValueError(f"Article {article_id} was deleted while narrating; {url} is orphaned")
    article.audio_url_es = url
    article.audio_manifest_es = result.manifest
    # Overwritten on every run (#601): it describes the narration just stored.
    article.audio_qa_es = result.qa
    db.commit()
    return ArticleAudioResponse(
        article_id=article_id,
        audio_url_es=url,
        duration_ms=result.duration_ms,
        segments=result.segments,
        synthesized=result.synthesized,
        reused=result.reused,
        voice_check=summarize_qa(result.qa),
    ).model_dump()


def run_article_narration_job(job_id: int) -> None:
    """Narrate the job's article to completion, persisting progress and result.

    Runs in a FastAPI ``BackgroundTask`` with its own DB session. Never raises —
    failures land on the job row and go to Sentry. Mirrors ``edu_jobs.run_edu_job``.
    """
    db = SessionLocal(expire_on_commit=False)
    try:
        job = db.get(CrawlJob, job_id)
        if job is None:
            logger.error("Narration job %d not found at execution time", job_id)
            return

        job.status = CrawlJobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        db.commit()

        stats = _narrate(db, job)

        job.stats = stats
        job.status = CrawlJobStatus.SUCCEEDED.value
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.info("Narration job %d succeeded: %s", job_id, stats)

    except Exception as e:
        logger.exception("Narration job %d failed", job_id)
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
            logger.exception("Failed to record narration job %d failure", job_id)
    finally:
        db.close()
