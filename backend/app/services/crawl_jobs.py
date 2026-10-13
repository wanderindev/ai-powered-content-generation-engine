"""Background runner for media-acquisition crawl jobs (#277).

The admin crawl/score endpoints create a :class:`CrawlJob` row, schedule
:func:`run_crawl_job` as a FastAPI ``BackgroundTask``, and return ``202`` + the
job id immediately. The runner opens its **own** DB session (the request's
session is closed once the response is sent), drives the relevant service with a
progress callback that writes counters back to the job row, and records the final
stats or error. A status-poll endpoint reads the row.

Concurrency: :func:`active_job` lets the endpoint refuse a second job of the same
type while one is in flight (a pragmatic stand-in for the cross-worker rate-limit
coordination deferred from #275 — the per-process rate limiter still applies
within each crawl). A job left ``RUNNING`` past :data:`STALE_AFTER` (e.g. its
worker was recycled mid-crawl) is treated as dead so it can't block kick-off
forever.
"""

import logging
from datetime import UTC, datetime, timedelta

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.services.loc import LOCService
from app.services.media_scoring import score_candidates
from app.services.wikimedia import WikimediaService

logger = logging.getLogger(__name__)

# A job still PENDING/RUNNING this long after creation is presumed dead (the
# worker that owned it was recycled). Longer than the slowest plausible crawl.
STALE_AFTER = timedelta(hours=1)

# Cap the stored error message so a giant traceback can't blow up the row.
_MAX_ERROR_LEN = 2000

# Active (not-terminal, not-stale) statuses.
_ACTIVE_STATUSES = (CrawlJobStatus.PENDING.value, CrawlJobStatus.RUNNING.value)


def active_job(db: Session, job_type: str) -> CrawlJob | None:
    """Return the in-flight job for *job_type*, or ``None``.

    Ignores jobs created more than :data:`STALE_AFTER` ago so a crashed run never
    permanently blocks new kick-offs.
    """
    cutoff = datetime.now(UTC) - STALE_AFTER
    stmt = (
        select(CrawlJob)
        .where(
            CrawlJob.job_type == job_type,
            CrawlJob.status.in_(_ACTIVE_STATUSES),
            CrawlJob.created_at >= cutoff,
        )
        .order_by(CrawlJob.created_at.desc())
    )
    return db.execute(stmt).scalars().first()


def _run_for_type(db: Session, job: CrawlJob, progress) -> dict:
    """Dispatch to the right service for the job's type and return its stats dict."""
    params = job.params or {}

    if job.job_type == CrawlJobType.WIKIMEDIA.value:
        with WikimediaService() as service:
            return service.crawl_categories(
                db=db,
                seed_categories=params.get("seed_categories"),
                max_depth=params.get("max_depth", 2),
                max_per_category=params.get("max_per_category", 100),
                progress_callback=progress,
            )

    if job.job_type == CrawlJobType.LOC.value:
        with LOCService() as service:
            return service.crawl_panama(
                db=db,
                queries=params.get("queries"),
                max_per_query=params.get("max_per_query", 150),
                progress_callback=progress,
            )

    if job.job_type == CrawlJobType.SCORE.value:
        return score_candidates(
            db=db,
            batch_size=params.get("batch_size", 30),
            max_batches=params.get("max_batches"),
            progress_callback=progress,
        )

    raise ValueError(f"Unknown crawl job type: {job.job_type!r}")


def run_crawl_job(job_id: int) -> None:
    """Execute a crawl/score job to completion, persisting progress and result.

    Runs in a FastAPI ``BackgroundTask`` with its own DB session. Never raises —
    failures are recorded on the job row and reported to Sentry.
    """
    db = SessionLocal()
    try:
        job = db.get(CrawlJob, job_id)
        if job is None:
            logger.error("Crawl job %d not found at execution time", job_id)
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
        logger.info("Crawl job %d (%s) succeeded: %s", job_id, job.job_type, stats)

    except Exception as e:
        logger.exception("Crawl job %d failed", job_id)
        sentry_sdk.capture_exception(e)
        db.rollback()
        # Re-fetch: the rollback expired the instance and the session may have
        # been in a failed transaction.
        job = db.get(CrawlJob, job_id)
        if job is not None:
            job.status = CrawlJobStatus.FAILED.value
            job.error = str(e)[:_MAX_ERROR_LEN]
            job.finished_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()
