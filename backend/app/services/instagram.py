"""Instagram Graph API publisher for the social pipeline.

Publishing an image post is Meta's two-step dance: create a *media container*
from a publicly reachable JPEG URL (our rendered artifact on Spaces) plus the
caption, wait for the container to finish processing, then publish it. The
container expires after 24h, which is why containers are created at publish
time — never ahead of schedule.

The publisher is config-gated: without ``instagram_access_token`` /
``instagram_account_id`` it reports ``enabled = False`` and callers surface
503 (publish-now) or skip work (scheduler). All HTTP goes through the
SSRF-validating pooled client + transient-failure retry from
``services/http_client`` (epic #275 conventions).
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import sentry_sdk
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.instagram_post import InstagramPost
from app.services.http_client import make_hardened_client, run_with_retry

logger = logging.getLogger(__name__)

# Container processing is usually instant for images; poll briefly, fail loud.
_CONTAINER_POLL_INTERVAL_SECONDS = 2.0
_CONTAINER_POLL_TIMEOUT_SECONDS = 30.0


class InstagramPublishError(Exception):
    """A publish attempt failed; the message is operator-facing."""


class InstagramNotConfiguredError(InstagramPublishError):
    """Credentials are not configured; publishing is disabled."""


@dataclass(frozen=True)
class IgPublishResult:
    ig_media_id: str
    permalink: str | None


def _graph_error_detail(response: httpx.Response) -> str:
    """Extract Meta's error message from a Graph API error payload."""
    try:
        err = response.json().get("error", {})
        message = err.get("message") or response.text[:200]
        code = err.get("code")
        return f"{message} (code {code})" if code is not None else str(message)
    except Exception:
        return f"HTTP {response.status_code}: {response.text[:200]}"


class InstagramPublisher:
    """Thin Graph API client bound to the configured professional account."""

    def __init__(self):
        settings = get_settings()
        self._token = settings.instagram_access_token
        self._account_id = settings.instagram_account_id
        self._base = settings.instagram_graph_api_url.rstrip("/")
        self._client = make_hardened_client()

    def __enter__(self) -> "InstagramPublisher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._account_id)

    def _post(self, path: str, data: dict) -> dict:
        def _do():
            resp = self._client.post(
                f"{self._base}/{path}", data={**data, "access_token": self._token}
            )
            resp.raise_for_status()
            return resp

        try:
            return run_with_retry(_do).json()
        except httpx.HTTPStatusError as e:
            raise InstagramPublishError(_graph_error_detail(e.response)) from e
        except httpx.HTTPError as e:
            raise InstagramPublishError(f"Graph API request failed: {e}") from e

    def _get(self, path: str, params: dict) -> dict:
        def _do():
            resp = self._client.get(
                f"{self._base}/{path}", params={**params, "access_token": self._token}
            )
            resp.raise_for_status()
            return resp

        try:
            return run_with_retry(_do).json()
        except httpx.HTTPStatusError as e:
            raise InstagramPublishError(_graph_error_detail(e.response)) from e
        except httpx.HTTPError as e:
            raise InstagramPublishError(f"Graph API request failed: {e}") from e

    def _wait_for_container(self, container_id: str) -> None:
        """Poll the container until FINISHED (images are usually immediate)."""
        deadline = time.monotonic() + _CONTAINER_POLL_TIMEOUT_SECONDS
        while True:
            status = self._get(container_id, {"fields": "status_code"}).get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise InstagramPublishError("Media container processing failed (status ERROR)")
            if time.monotonic() >= deadline:
                raise InstagramPublishError(
                    f"Media container not ready after "
                    f"{_CONTAINER_POLL_TIMEOUT_SECONDS:.0f}s (status {status})"
                )
            time.sleep(_CONTAINER_POLL_INTERVAL_SECONDS)

    def publish_image(self, image_url: str, caption: str) -> IgPublishResult:
        """Publish one image post; returns the IG media id + permalink."""
        if not self.enabled:
            raise InstagramNotConfiguredError("Instagram publishing is not configured")

        container = self._post(
            f"{self._account_id}/media", {"image_url": image_url, "caption": caption or ""}
        )
        container_id = container.get("id")
        if not container_id:
            raise InstagramPublishError(f"Container creation returned no id: {container}")
        self._wait_for_container(container_id)

        published = self._post(f"{self._account_id}/media_publish", {"creation_id": container_id})
        media_id = published.get("id")
        if not media_id:
            raise InstagramPublishError(f"media_publish returned no id: {published}")

        # Permalink fetch is best-effort — the post is already live.
        permalink = None
        try:
            permalink = self._get(media_id, {"fields": "permalink"}).get("permalink")
        except InstagramPublishError:
            logger.warning("Published %s but could not fetch permalink", media_id, exc_info=True)

        return IgPublishResult(ig_media_id=str(media_id), permalink=permalink)


def publish_post(db: Session, post: InstagramPost, publisher: InstagramPublisher) -> bool:
    """Publish *post* and record the outcome on the row. Returns success.

    Shared by publish-now and the scheduler. On failure the row is parked
    (``publish_error`` set — the scheduler never retries it; the operator
    re-schedules or hits publish-now again, both of which clear the error).
    Never raises: the caller decides flow from the boolean.
    """
    try:
        result = publisher.publish_image(post.image_url, post.caption or "")
    except InstagramPublishError as e:
        post.publish_error = str(e)
        post.publish_attempts = (post.publish_attempts or 0) + 1
        post.publishing_started_at = None
        db.commit()
        logger.error("Instagram publish failed for post id=%s: %s", post.id, e)
        sentry_sdk.capture_exception(e)
        return False

    post.published_at = datetime.now(UTC)
    post.ig_media_id = result.ig_media_id
    post.ig_permalink = result.permalink
    post.publish_error = None
    post.publish_attempts = (post.publish_attempts or 0) + 1
    post.publishing_started_at = None
    db.commit()
    logger.info("Published instagram post id=%s as ig_media_id=%s", post.id, result.ig_media_id)
    return True
