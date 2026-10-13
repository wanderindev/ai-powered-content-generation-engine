"""Shared API dependencies."""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import sentry_sdk
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import AdminEmail, MagicLink, MagicLinkAction
from app.models.educator import Educator
from app.services import educator_service

logger = logging.getLogger(__name__)


def _bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def require_educator_access(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Educator:
    """Gate a route behind a valid educator session token (#50).

    The frontend sends the opaque token issued by ``/educators/access`` or
    ``/educators/signup`` as ``Authorization: Bearer <token>``. The token is
    validated server-side against the ``educators`` row and its 7-day window;
    an unknown, expired, or unsubscribed token yields 401. Returns the resolved
    ``Educator`` so routes can use it if needed.
    """
    educator = educator_service.validate_token(db, _bearer_token(authorization))
    if educator is None:
        logger.warning("auth failure: %s (%s)", "educator_access_gate", "invalid_or_expired_token")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="auth failure: educator_access_gate",
            level="warning",
            data={"event": "educator_access_gate", "reason": "invalid_or_expired_token"},
        )
        raise HTTPException(status_code=401, detail="Educator access required")
    return educator


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
