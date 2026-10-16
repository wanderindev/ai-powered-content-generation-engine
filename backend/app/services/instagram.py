"""Instagram Graph API publisher for the social pipeline.

Publishing an image post is Meta's two-step dance: create a *media container*
from a publicly reachable JPEG URL (our rendered artifact on Spaces) plus the
caption, wait for the container to finish processing, then publish it. The
container expires after 24h, which is why containers are created at publish
time — never ahead of schedule.

The publisher is config-gated twice. Without ``instagram_access_token`` /
``instagram_account_id`` it reports ``enabled = False`` and callers surface
503 (publish-now) or skip work (scheduler). With credentials but
``instagram_publishing_enabled`` off (the default, #576) it reports
``publishing_enabled = False``: publish-now answers 409, the scheduler leaves
due rows queued, and ``publish_image`` itself refuses before any HTTP call —
the last line of defence for an account whose posting an agency runs.

``verify_instagram`` is the never-posting dry run: it reads the account's
username, inspects the token (expiry + scopes) and, given a rendered image,
creates a media container and polls it to ``FINISHED`` — and stops there. An
unpublished container simply expires after 24h, so this exercises the real
token, account and public image URL without posting anything. It never calls
``media_publish`` and works with publishing off; that is its point.

All HTTP goes through the SSRF-validating pooled client + transient-failure
retry from ``services/http_client`` (epic #275 conventions).
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import sentry_sdk
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import redact_secrets
from app.models.instagram_post import InstagramPost
from app.services.http_client import make_hardened_client, run_with_retry

logger = logging.getLogger(__name__)

# Container processing is usually instant for images; poll briefly, fail loud.
_CONTAINER_POLL_INTERVAL_SECONDS = 2.0
_CONTAINER_POLL_TIMEOUT_SECONDS = 30.0

# The professional account the credentials are expected to belong to; verify
# flags any other username (a token minted for the wrong account).
EXPECTED_USERNAME = "panamaincontextbydiego"

# The permission ``media_publish`` needs.
PUBLISH_SCOPE = "instagram_content_publish"


class InstagramPublishError(Exception):
    """A publish attempt failed; the message is operator-facing."""


class InstagramNotConfiguredError(InstagramPublishError):
    """Credentials are not configured; publishing is disabled."""


class InstagramPublishingDisabledError(InstagramPublishError):
    """Credentials exist but the ``instagram_publishing_enabled`` switch is off."""


@dataclass(frozen=True)
class IgPublishResult:
    ig_media_id: str
    permalink: str | None


@dataclass(frozen=True)
class IgTokenInfo:
    """What ``debug_token`` says about the configured access token."""

    is_valid: bool
    expires_at: datetime | None  # None = never expires (``expires_at`` 0)
    scopes: tuple[str, ...]

    def days_remaining(self, now: datetime | None = None) -> float | None:
        if self.expires_at is None:
            return None
        now = now or datetime.now(UTC)
        return (self.expires_at - now).total_seconds() / 86400


@dataclass
class VerifyStep:
    name: str
    ok: bool
    detail: str
    data: dict = field(default_factory=dict)


def _graph_error_detail(response: httpx.Response) -> str:
    """Extract Meta's error message from a Graph API error payload."""
    try:
        err = response.json().get("error", {})
        message = err.get("message") or response.text[:200]
        code = err.get("code")
        return f"{message} (code {code})" if code is not None else str(message)
    except Exception:
        return f"HTTP {response.status_code}: {response.text[:200]}"


def safe_detail(exc: Exception) -> str:
    """Operator-facing text for any exception, without leaking a token."""
    if isinstance(exc, InstagramPublishError):
        return redact_secrets(str(exc))
    return f"Unexpected error ({type(exc).__name__})"


class InstagramPublisher:
    """Thin Graph API client bound to the configured professional account."""

    def __init__(self):
        settings = get_settings()
        self._token = settings.instagram_access_token
        self._account_id = settings.instagram_account_id
        self._base = settings.instagram_graph_api_url.rstrip("/")
        self._publishing_enabled = bool(settings.instagram_publishing_enabled)
        self._client = make_hardened_client()

    def __enter__(self) -> "InstagramPublisher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def enabled(self) -> bool:
        """Credentials are configured (the Graph API is reachable at all)."""
        return bool(self._token and self._account_id)

    @property
    def publishing_enabled(self) -> bool:
        """Configured *and* the publishing kill switch is on (#576)."""
        return self.enabled and self._publishing_enabled

    @property
    def _auth_headers(self) -> dict:
        # The token rides in a header, never the URL: Sentry's httpx
        # integration turns every request's query string into a breadcrumb
        # (``http.query``), and a breadcrumb is attached to every later event.
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """One Graph API call; every failure becomes an ``InstagramPublishError``.

        Catches *everything* the transport can raise — not just
        ``httpx.HTTPError`` but the SSRF transport's ``SSRFError`` (a
        ``ValueError``, e.g. a DNS hiccup) and a non-JSON body — so callers
        documented as never raising really don't. The error is raised *outside*
        the ``except`` block with a message built from the exception type or
        Meta's own error payload, never ``str(e)`` (an httpx message or repr
        can carry the request URL, and ``debug_token``'s URL holds the token),
        so no token-bearing exception is chained onto it either. The kwargs
        are cleared first so this frame's locals carry no token; Sentry's
        frame-locals scrub (``app.core.observability``) is the second line.
        """

        def _do():
            resp = self._client.request(
                method, f"{self._base}/{path}", headers=self._auth_headers, **kwargs
            )
            resp.raise_for_status()
            return resp

        detail: str
        try:
            resp = run_with_retry(_do)
        except httpx.HTTPStatusError as e:
            detail = _graph_error_detail(e.response)
        except Exception as e:  # httpx.HTTPError, SSRFError, anything else
            detail = f"Graph API request failed ({type(e).__name__})"
        else:
            try:
                body = resp.json()
            except ValueError:
                body = None
                detail = f"Graph API returned a non-JSON body (HTTP {resp.status_code})"
            if isinstance(body, dict):
                return body
            if body is not None:
                detail = "Graph API returned an unexpected body"
        kwargs.clear()  # keep the token-bearing params out of this frame's locals
        raise InstagramPublishError(redact_secrets(detail))

    def _post(self, path: str, data: dict) -> dict:
        return self._request("POST", path, data=data)

    def _get(self, path: str, params: dict) -> dict:
        return self._request("GET", path, params=params)

    def fetch_username(self) -> str | None:
        """The professional account's username (``GET {account_id}?fields=username``)."""
        return self._get(self._account_id, {"fields": "username"}).get("username")

    def fetch_token_info(self) -> IgTokenInfo:
        """Inspect the configured token via ``debug_token`` (expiry + scopes).

        A user token may inspect itself, so the same token is both the
        ``input_token`` and the (header) access token. ``input_token`` has to
        be a query parameter, so this one URL does carry the token — the
        Sentry breadcrumb scrubber (``app.core.observability``) redacts it.
        """
        data = self._get("debug_token", {"input_token": self._token}).get("data") or {}
        raw_expiry = data.get("expires_at")
        # 0 / missing = a never-expiring token.
        expires_at = datetime.fromtimestamp(int(raw_expiry), tz=UTC) if raw_expiry else None
        return IgTokenInfo(
            is_valid=bool(data.get("is_valid")),
            expires_at=expires_at,
            scopes=tuple(data.get("scopes") or ()),
        )

    def fetch_granted_permissions(self) -> set[str]:
        """Granted permissions via ``GET me/permissions`` — the fallback when
        ``debug_token`` is unavailable (it needs an app token or a token from
        one of the app's developers / admins). Says nothing about expiry."""
        rows = self._get("me/permissions", {}).get("data") or []
        return {
            r.get("permission")
            for r in rows
            if isinstance(r, dict) and r.get("status") == "granted"
        }

    def create_container(self, image_url: str, caption: str) -> str:
        """Create a media container (step one of publishing); returns its id.

        On its own this posts nothing — an unpublished container expires after
        24h — which is what lets verify exercise it safely.
        """
        container = self._post(
            f"{self._account_id}/media", {"image_url": image_url, "caption": caption or ""}
        )
        container_id = container.get("id")
        if not container_id:
            raise InstagramPublishError(f"Container creation returned no id: {container}")
        return str(container_id)

    def wait_for_container(self, container_id: str) -> None:
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
        if not self.publishing_enabled:
            # Callers check first; this guard makes the switch hold for any
            # future caller too, before a single Graph API request.
            raise InstagramPublishingDisabledError(
                "Instagram publishing is turned off (instagram_publishing_enabled)"
            )

        container_id = self.create_container(image_url, caption)
        self.wait_for_container(container_id)

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


def warn_if_token_expiring(info: IgTokenInfo, warn_days: int) -> bool:
    """Sentry-warn when the token is invalid or has fewer than ``warn_days`` left.

    Long-lived tokens last about 60 days and nothing here refreshes them, so
    this is the operator's only heads-up before verify (and, once switched on,
    publishing) starts failing. Returns whether a warning was raised. The
    message carries no day count, so Sentry groups the repeats into one issue.
    """
    if not info.is_valid:
        logger.warning("Instagram access token is invalid")
        sentry_sdk.capture_message("Instagram access token is invalid", level="warning")
        return True
    days = info.days_remaining()
    if days is not None and days < warn_days:
        logger.warning("Instagram access token expires in %.1f days", days)
        sentry_sdk.capture_message(
            "Instagram access token expires soon — mint a new long-lived token",
            level="warning",
        )
        return True
    return False


def _token_step_from_permissions(publisher: InstagramPublisher, debug_error: str) -> VerifyStep:
    """Token step when ``debug_token`` failed: fall back to ``me/permissions``.

    That proves the token works and carries the publish permission, but not
    when it expires — the step still passes on scope alone and says so.
    """
    try:
        granted = publisher.fetch_granted_permissions()
    except Exception as e:
        return VerifyStep(
            name="token",
            ok=False,
            detail=f"debug_token failed: {debug_error}; me/permissions failed: {safe_detail(e)}",
        )
    has_scope = PUBLISH_SCOPE in granted
    return VerifyStep(
        name="token",
        ok=has_scope,
        detail=(
            f"debug_token unavailable ({debug_error}); expiry unknown. me/permissions: "
            + (f"{PUBLISH_SCOPE} granted" if has_scope else f"missing {PUBLISH_SCOPE}")
        ),
        data={
            "is_valid": True,
            "expires_at": None,
            "days_remaining": None,
            "has_publish_scope": has_scope,
            "expiry_known": False,
        },
    )


def verify_instagram(
    publisher: InstagramPublisher,
    warn_days: int,
    image_url: str | None = None,
    caption: str | None = None,
) -> list[VerifyStep]:
    """Exercise the live integration without posting; one result per step.

    1. ``account`` — ``GET {account_id}?fields=username`` must answer
       ``EXPECTED_USERNAME``;
    2. ``token`` — ``debug_token``: valid, carries ``instagram_content_publish``,
       and its expiry (Sentry-warned below ``warn_days``);
    3. ``container`` (only with ``image_url``) — create the media container and
       poll it to ``FINISHED``.

    **Never calls ``media_publish``.** Steps are independent, so one failure
    still reports the others; any error lands on its step, never raises. When
    ``debug_token`` is refused (it needs an app token or an app developer's
    token) the token step falls back to ``me/permissions``: scope, no expiry.
    """
    steps: list[VerifyStep] = []

    try:
        username = publisher.fetch_username()
        matches = username == EXPECTED_USERNAME
        steps.append(
            VerifyStep(
                name="account",
                ok=matches,
                detail=(
                    f"Account is @{username}"
                    if matches
                    else f"Account is @{username}, expected @{EXPECTED_USERNAME}"
                ),
                data={"username": username},
            )
        )
    except Exception as e:  # a verify step reports, it never raises
        steps.append(VerifyStep(name="account", ok=False, detail=safe_detail(e)))

    try:
        info = publisher.fetch_token_info()
        days = info.days_remaining()
        has_scope = PUBLISH_SCOPE in info.scopes
        expiring = warn_if_token_expiring(info, warn_days)
        problems = []
        if not info.is_valid:
            problems.append("token is invalid")
        elif expiring:
            problems.append(f"expires in {days:.1f} days")
        if not has_scope:
            problems.append(f"missing {PUBLISH_SCOPE}")
        expiry_text = (
            "never expires"
            if info.expires_at is None
            else f"expires {info.expires_at.isoformat()} ({days:.1f} days)"
        )
        steps.append(
            VerifyStep(
                name="token",
                ok=info.is_valid and has_scope and not expiring,
                detail=f"Token {expiry_text}" + (f": {'; '.join(problems)}" if problems else ""),
                data={
                    "is_valid": info.is_valid,
                    "expires_at": info.expires_at.isoformat() if info.expires_at else None,
                    "days_remaining": round(days, 1) if days is not None else None,
                    "has_publish_scope": has_scope,
                    "expiry_known": True,
                },
            )
        )
    except Exception as e:
        steps.append(_token_step_from_permissions(publisher, safe_detail(e)))

    if image_url:
        try:
            container_id = publisher.create_container(image_url, caption or "")
            publisher.wait_for_container(container_id)
            steps.append(
                VerifyStep(
                    name="container",
                    ok=True,
                    detail=(
                        f"Container {container_id} is FINISHED and was not published "
                        "(it expires unused after 24h)"
                    ),
                    data={"container_id": container_id},
                )
            )
        except Exception as e:
            steps.append(VerifyStep(name="container", ok=False, detail=safe_detail(e)))

    return steps
