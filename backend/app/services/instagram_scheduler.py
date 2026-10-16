"""In-app scheduler that publishes due Instagram posts.

The Graph API has no native scheduling, and until now nothing in this codebase
*acted* at a future time (blog/edu "scheduling" is passive read-time filtering
on ``published_at``). This module adds the active half for Instagram: an
asyncio loop started from the FastAPI lifespan ticks every
``instagram_scheduler_interval_seconds`` and publishes every APPROVED,
rendered post whose ``scheduled_at`` has passed — but only while
``instagram_publishing_enabled`` is on (#576, default off). With the switch
off a tick claims nothing: due rows stay SCHEDULED, exactly as with missing
credentials. Once credentials exist, a tick also runs the token-expiry check
(``debug_token``) once a day per worker, switch or no switch.

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
from datetime import UTC, datetime, time, timedelta

import sentry_sdk

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.enums import ContentStatus, InstagramPostType
from app.models.instagram_post import InstagramPost
from app.services.instagram import (
    PUBLISH_SCOPE,
    InstagramPublisher,
    publish_post,
    safe_detail,
    warn_if_token_expiring,
)

logger = logging.getLogger(__name__)

STALE_CLAIM_AFTER = timedelta(minutes=15)

# Token-expiry check cadence (#576). Per process: each uvicorn worker checks
# once a day; Sentry groups the repeats.
TOKEN_CHECK_EVERY = timedelta(days=1)
_last_token_check_at: datetime | None = None

# An article promo goes out the day after its article publishes, at noon
# Panama (17:00 UTC) — derived at schedule time from the article's actual
# ``published_at`` (#576), which the publish step picks at run time (#558).
PROMO_DAYS_AFTER_PUBLISH = 1
PROMO_PUBLISH_TIME_UTC = time(17, 0)


def promo_time_for(published_at: datetime) -> datetime:
    """The derived ``scheduled_at`` of a promo for an article published then."""
    day = published_at.astimezone(UTC).date() + timedelta(days=PROMO_DAYS_AFTER_PUBLISH)
    return datetime.combine(day, PROMO_PUBLISH_TIME_UTC, tzinfo=UTC)


def follow_article_publish_date(
    db,
    article_id: int,
    old_published_at: datetime | None,
    new_published_at: datetime | None,
) -> int:
    """Move an article's derived, still-scheduled promo with its publish date (#588).

    A promo whose ``scheduled_at`` equals the time derived from the old date is
    treated as derived — an explicit time that happens to coincide is
    indistinguishable and follows too. FAILED, claimed, published and
    explicitly-timed promos are never touched. On unpublish
    (``new_published_at`` None) the promo is unscheduled, because there is no
    date to derive from; it is not re-scheduled on a later re-publish — the
    operator re-schedules it.

    Does not commit: the caller's commit writes the article and the promo
    together. Returns the number of promos moved.
    """
    if old_published_at is None:
        return 0
    old_time = promo_time_for(old_published_at)
    new_time = promo_time_for(new_published_at) if new_published_at is not None else None
    if new_time == old_time:
        return 0
    promos = (
        db.query(InstagramPost)
        .filter(
            InstagramPost.post_type == InstagramPostType.ARTICLE_PROMO.value,
            InstagramPost.article_id == article_id,
            InstagramPost.status == ContentStatus.APPROVED.value,
            InstagramPost.scheduled_at == old_time,
            InstagramPost.published_at.is_(None),
            InstagramPost.publishing_started_at.is_(None),
            InstagramPost.publish_error.is_(None),
        )
        .all()
    )
    for post in promos:
        post.scheduled_at = new_time
    if promos:
        logger.info(
            "Article %s re-dated: moved %d derived Instagram promo(s) %s to %s",
            article_id,
            len(promos),
            [post.id for post in promos],
            new_time,
        )
    return len(promos)


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


def _maybe_check_token(publisher: InstagramPublisher) -> None:
    """Run the token-expiry check at most once per ``TOKEN_CHECK_EVERY`` per worker.

    Runs whether or not publishing is switched on: an expired token is worth
    knowing about before the day someone flips the switch. Never raises — any
    error, not just a Graph API one, is caught so it cannot stop the tick (or
    reach Sentry via the loop's ``capture_exception`` with a token in frame
    locals) — and a failed check still counts as the day's attempt, so an
    outage does not turn into a request per tick. A Sentry warning goes out
    only when the token is expiring / invalid or both checks fail.
    """
    global _last_token_check_at
    now = datetime.now(UTC)
    if _last_token_check_at is not None and now - _last_token_check_at < TOKEN_CHECK_EVERY:
        return
    _last_token_check_at = now
    try:
        info = publisher.fetch_token_info()
        warn_if_token_expiring(info, get_settings().instagram_token_warn_days)
        return
    except Exception as e:  # never let the check break the tick
        debug_error = safe_detail(e)
    # debug_token needs an app token or an app developer's token; when it is
    # refused, me/permissions still proves the token works (expiry unknown).
    try:
        granted = publisher.fetch_granted_permissions()
    except Exception as e:
        message = (
            f"Instagram token check failed: debug_token: {debug_error}; "
            f"me/permissions: {safe_detail(e)}"
        )
        logger.warning(message)
        sentry_sdk.capture_message("Instagram token check failed", level="warning")
        return
    logger.warning(
        "Instagram debug_token unavailable (%s); token works via me/permissions "
        "(%s granted: %s), expiry unknown",
        debug_error,
        PUBLISH_SCOPE,
        PUBLISH_SCOPE in granted,
    )


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
            # End the sweep's transaction so no pooled connection is held
            # over the debug_token round-trip.
            db.commit()
            _maybe_check_token(publisher)
            if not publisher.publishing_enabled:
                # Kill switch off (#576): same shape as missing credentials —
                # due rows stay SCHEDULED (never FAILED) and nothing is claimed.
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
