"""Shared API dependencies."""

import logging
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace

import sentry_sdk
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import AdminEmail, MagicLink, MagicLinkAction
from app.services.agent_task_driver import is_driver_call_allowed

logger = logging.getLogger(__name__)


def _bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


_DEV_MAGIC_LINK = SimpleNamespace(
    user_id=None,
    admin_email="dev@localhost",
    action=MagicLinkAction.ADMIN_ACCESS.value,
)


def validate_admin_token(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> MagicLink:
    """FastAPI dependency that authenticates an admin request (#54).

    Apply at the router level so every route is gated by default::

        router = APIRouter(..., dependencies=[Depends(validate_admin_token)])

    Routes that need the resolved ``MagicLink`` (e.g. ``admin_email`` for audit
    logging) additionally declare
    ``magic_link: MagicLink = Depends(validate_admin_token)`` in their signature;
    FastAPI dedupes the dependency so it still runs exactly once.

    The token is read **only** from the ``Authorization: Bearer`` header. The
    transitional ``?token=`` query fallback was removed once all clients sent the
    header (epic #263) — keeping credentials out of access logs, Referer headers,
    browser history, and Sentry.

    The "dev-token" bypass is gated behind BOTH ``settings.debug`` and
    ``settings.environment == "development"`` (defense-in-depth: two
    independent flags must align). Every bypass use emits a WARN log and a
    Sentry breadcrumb so it is impossible for the bypass to fire silently in
    production.
    """
    settings = get_settings()
    effective_token = _bearer_token(authorization)

    if settings.debug and settings.environment == "development" and effective_token == "dev-token":
        path = request.url.path if request else "<unknown>"
        logger.warning("dev-token admin bypass used for path: %s", path)
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="dev-token admin bypass used",
            level="warning",
            data={"path": path},
        )
        return _DEV_MAGIC_LINK

    magic_link = None
    if effective_token:
        magic_link = (
            db.query(MagicLink)
            .join(AdminEmail, AdminEmail.email == MagicLink.admin_email)
            .filter(MagicLink.token == effective_token)
            .filter(MagicLink.action == MagicLinkAction.ADMIN_ACCESS.value)
            .filter(MagicLink.expires_at > datetime.now(UTC))
            .filter(AdminEmail.is_active.is_(True))
            .first()
        )

    if not magic_link:
        logger.warning("auth failure: %s (%s)", "admin_token_validate", "invalid_or_expired_token")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="auth failure: admin_token_validate",
            level="warning",
            data={
                "event": "admin_token_validate",
                "reason": "invalid_or_expired_token",
            },
        )
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")

    return magic_link


# The principal a driver-token request runs as. Shaped like a MagicLink for the
# handful of attributes routes read (``admin_email`` is used as ``leased_by``).
_DRIVER_PRINCIPAL = SimpleNamespace(
    user_id=None,
    admin_email="agent-driver",
    action=MagicLinkAction.ADMIN_ACCESS.value,
)


def _is_driver_token(token: str | None, settings) -> bool:
    """Constant-time check of the bearer token against the driver service token."""
    return bool(
        token
        and settings.agent_driver_token
        and secrets.compare_digest(token.encode(), settings.agent_driver_token.encode())
    )


def validate_admin_or_driver_token(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin magic-link auth, OR the long-lived weekly-driver service token (#418).

    Admin tokens expire in 7 days (see ``validate_admin_token``), which cannot serve
    an unattended weekly driver. A non-empty ``settings.agent_driver_token``
    enables a single static bearer token, compared in constant time, that
    authenticates every route on a router that opts into this dependency (the
    agent-tasks router). Empty setting = disabled: only admin tokens pass.

    The driver token is *also* accepted — but only on the exact call surface
    ``KIND_CALLS`` emits — by ``validate_admin_or_allowlisted_driver_token``,
    which gates the dashboard, instagram and admin routers (#444). Nothing else
    accepts it.

    Deliberately minimal: no DB token table, no rotation machinery. Rotating the
    token is an env-var change plus a restart.
    """
    settings = get_settings()
    token = _bearer_token(authorization)
    if _is_driver_token(token, settings):
        logger.debug("agent-driver service token authenticated")
        return _DRIVER_PRINCIPAL
    # Plain function call with the already-resolved args: one source of truth for
    # admin validation, and admin tokens keep working on these routes.
    return validate_admin_token(request, authorization, db)


def validate_admin_or_allowlisted_driver_token(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin magic-link auth, OR the driver service token on its exact call surface (#444).

    ``KIND_CALLS`` (``app/services/agent_task_driver.py``) targets endpoints on
    the dashboard, instagram and admin routers, which only accepted admin
    tokens — so the unattended driver 401'd on everything except
    ``PLAN_ARTICLES``. This dependency accepts the driver token ONLY when
    ``(method, path)`` is on the allowlist derived from that same module (bound
    to it by test, so a new kind cannot ship a call the driver cannot execute);
    everything else falls through to ``validate_admin_token`` unchanged, so a
    driver token used off-surface 401s exactly like any unknown token — no 403
    oracle distinguishing a valid driver token from garbage.

    Empty ``settings.agent_driver_token`` = disabled: only admin tokens pass,
    everywhere.
    """
    settings = get_settings()
    token = _bearer_token(authorization)
    if _is_driver_token(token, settings) and is_driver_call_allowed(
        request.method, request.url.path
    ):
        logger.debug(
            "agent-driver service token authenticated for %s %s",
            request.method,
            request.url.path,
        )
        return _DRIVER_PRINCIPAL
    return validate_admin_token(request, authorization, db)
