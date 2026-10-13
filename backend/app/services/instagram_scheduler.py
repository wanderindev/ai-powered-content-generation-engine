"""In-app scheduler that publishes due Instagram posts.

The Graph API has no native scheduling, and until now nothing in this codebase
*acted* at a future time (blog/edu "scheduling" is passive read-time filtering
on ``published_at``). This module adds the active half for Instagram: an
asyncio loop started from the FastAPI lifespan ticks every
``instagram_scheduler_interval_seconds`` and publishes every APPROVED,
rendered post whose ``scheduled_at`` has passed.

Concurrency model (multi-worker / deploy-overlap safe):

* A tick claims one due row at a time with ``FOR UPDATE SKIP LOCKED`` and
  commits ``publishing_started_at`` before publishing — a concurrent tick
  skips both the row lock and (after commit) the claim marker.
* The slow network call happens *outside* any row lock.
* FAILED rows (``publish_error`` set) are never re-picked — retry is an
  operator action. No retry storms by construction.
* A stale-claim sweep parks rows whose worker died mid-publish (claim older
  than 15 min, still unpublished). Known residual: a crash between Instagram
  accepting the post and our commit parks a row FAILED even though it is live
  on IG — rare, and reconciled manually from the permalink-less error row.
"""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import sentry_sdk

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.enums import ContentStatus
from app.models.instagram_post import InstagramPost
from app.services.instagram import InstagramPublisher, publish_post

logger = logging.getLogger(__name__)

STALE_CLAIM_AFTER = timedelta(minutes=15)


def _sweep_stale_claims(db) -> int:
    """Park rows whose publish attempt was interrupted (worker restart)."""
    cutoff = datetime.now(UTC) - STALE_CLAIM_AFTER
    stale = (
        db.query(InstagramPost)
        .filter(
            InstagramPost.publishing_started_at.isnot(None),
            InstagramPost.publishing_started_at < cutoff,
            InstagramPost.published_at.is_(None),
        )
        .all()
    )
    for post in stale:
        post.publish_error = "Publish interrupted (worker restart) — re-schedule to retry"
        post.publish_attempts = (post.publish_attempts or 0) + 1
        post.publishing_started_at = None
        logger.warning("Swept stale publish claim on instagram post id=%s", post.id)
        sentry_sdk.capture_message(
            f"Instagram post {post.id}: stale publish claim swept", level="warning"
        )
    if stale:
        db.commit()
    return len(stale)


def _claim_next_due(db) -> InstagramPost | None:
    """Claim one due post (SKIP LOCKED) and commit the claim marker."""
    post = (
        db.query(InstagramPost)
        .filter(
            InstagramPost.status == ContentStatus.APPROVED.value,
            InstagramPost.scheduled_at.isnot(None),
            InstagramPost.scheduled_at <= datetime.now(UTC),
            InstagramPost.published_at.is_(None),
            InstagramPost.publish_error.is_(None),
            InstagramPost.publishing_started_at.is_(None),
            InstagramPost.image_url.isnot(None),
        )
        .order_by(InstagramPost.scheduled_at)
        .with_for_update(skip_locked=True)
        .limit(1)
        .first()
    )
    if post is None:
        db.rollback()  # release the transaction opened by the locking select
        return None
    post.publishing_started_at = datetime.now(UTC)
    db.commit()  # claim visible to (and skipped by) concurrent tickers
    return post


def run_scheduler_tick() -> int:
    """Publish every due post; returns how many were attempted.

    Sync (called via ``asyncio.to_thread``); opens its own session like the
    crawl-job runner — never a request session.
    """
    db = SessionLocal()
    attempted = 0
    try:
        _sweep_stale_claims(db)

        publisher = InstagramPublisher()
        try:
            if not publisher.enabled:
                # Leave due rows queued; they publish once credentials land.
                return 0
            while True:
                post = _claim_next_due(db)
                if post is None:
                    break
                attempted += 1
                publish_post(db, post, publisher)
        finally:
            publisher.close()
    finally:
        db.close()
    if attempted:
        logger.info("Instagram scheduler tick attempted %d publish(es)", attempted)
    return attempted


async def instagram_scheduler_loop(stop: asyncio.Event) -> None:
    """Tick until *stop* is set; each tick runs off the event loop."""
    settings = get_settings()
    interval = settings.instagram_scheduler_interval_seconds
    logger.info("Instagram scheduler loop started (interval %ss)", interval)
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_scheduler_tick)
        except Exception as e:  # never let a bad tick kill the loop
            logger.exception("Instagram scheduler tick crashed")
            sentry_sdk.capture_exception(e)
        with suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    logger.info("Instagram scheduler loop stopped")
