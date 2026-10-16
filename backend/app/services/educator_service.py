"""
Educator access service.

Low-friction gated access to educational materials (#50/#51/#52/#53). Being in
the ``educators`` table is the entitlement — there is no email-ownership step
(no confirmation link, no 6-digit code). Access is carried by an opaque,
server-side session token bound to a 7-day window:

* ``access(email)`` — returning educator. In table & not UNSUBSCRIBED → issue a
  fresh token + 7-day window. Otherwise → not registered (frontend shows signup).
* ``signup(...)`` — new educator. Creates the row and grants access immediately.
  If the mailing-list box is ticked, the double-opt-in subscription is delegated
  to ``services.mailing_list`` (unchanged), which sends the confirmation email;
  gate access is granted regardless of that confirmation.
* ``validate_token(token)`` — resolve a Bearer token to an Educator. (It used
  to back the ``require_educator_access`` gate on ``/public-media``; that gate
  was removed and the media library is now open to everyone.)
* ``session(token)`` — refresh check for the frontend on mount.
* ``unsubscribe(token)`` — token-authenticated; revokes the caller's own access
  and opts them out (#51 — no more unauthenticated email-only revocation).

``status`` (PENDING/CONFIRMED/UNSUBSCRIBED) is owned by the mailing-list flow;
gate access is decoupled from it except that UNSUBSCRIBED revokes access.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.educator import Educator
from app.models.enums import EducatorStatus
from app.services import mailing_list

logger = logging.getLogger(__name__)

ACCESS_DURATION_DAYS = 7


def _utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime (matches DB columns)."""
    return datetime.now(UTC)


def _generate_access_token() -> str:
    """Opaque, unguessable session token. Replaces the forgeable base64 (#52)."""
    return secrets.token_urlsafe(32)


def _grant_access(educator: Educator, now: datetime) -> None:
    """Issue a fresh session token and open a 7-day access window in place."""
    educator.access_token = _generate_access_token()
    educator.access_expires_at = now + timedelta(days=ACCESS_DURATION_DAYS)
    educator.last_access_at = now


def access(db: Session, email: str, language: str = "es") -> dict:
    """
    Returning educator enters their email.

    In the table and not unsubscribed → grant a fresh 7-day window + token.
    Otherwise → ``granted=False`` (the frontend then shows the signup form).

    Enumeration trade-off (#58 — accepted, by design): the synchronous
    ``granted`` flag tells any unauthenticated caller whether an email is a
    registered educator. We accept this rather than collapsing the response to a
    constant. Registration is deliberately open and unauthenticated — anyone can
    register *any* email via ``signup`` and get a token immediately — so learning
    "is X a registered educator" has little value and is not a credential-attack
    stepping stone. The residual is a minor privacy leak, not a defect; the
    inline signup UX (``EducatorAccessGate`` branches on ``granted`` with no email
    round-trip) is worth more than constant-response purity here. The newsletter,
    which *does* need spam protection, is separately gated by double-opt-in email
    confirmation in ``services.mailing_list``.
    """
    email = email.strip().lower()
    now = _utcnow()
    educator = db.execute(select(Educator).where(Educator.email == email)).scalar_one_or_none()

    if educator is None or educator.status == EducatorStatus.UNSUBSCRIBED.value:
        logger.warning("auth: %s (%s)", "educator_access", "not_registered")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="educator_access: not registered",
            level="info",
            data={"event": "educator_access", "reason": "not_registered", "email": email},
        )
        return {"granted": False, "status": "not_registered", "token": None, "expires_at": None}

    educator.language = language
    _grant_access(educator, now)
    db.commit()
    return {
        "granted": True,
        "status": "granted",
        "token": educator.access_token,
        "expires_at": educator.access_expires_at.isoformat(),
    }


def signup(
    db: Session,
    email: str,
    language: str = "es",
    name: str | None = None,
    institution: str | None = None,
    source: str = "media_library",
    mailing_list_optin: bool = False,
) -> dict:
    """
    Register a new educator and grant access immediately (no email step).

    If ``mailing_list_optin`` is set, the double-opt-in subscription is handled
    by ``services.mailing_list.subscribe`` (it creates/updates the row, sets the
    confirm token, and sends the confirmation email). Gate access is granted on
    top regardless of whether that confirmation is ever completed.
    """
    email = email.strip().lower()
    now = _utcnow()

    if mailing_list_optin:
        # Delegate row creation + confirmation email to the mailing-list flow,
        # then re-fetch the row to layer gate access on top.
        mailing_list.subscribe(email=email, source=source, language=language, db=db)
        educator = db.execute(select(Educator).where(Educator.email == email)).scalar_one()
    else:
        educator = db.execute(select(Educator).where(Educator.email == email)).scalar_one_or_none()
        if educator is None:
            educator = Educator(
                email=email,
                status=EducatorStatus.PENDING.value,
                language=language,
                source=source,
                created_at=now,
            )
            db.add(educator)
        elif educator.status == EducatorStatus.UNSUBSCRIBED.value:
            # Re-entering after opting out: restore gate eligibility. Mailing-list
            # membership stays off unless they tick the box (handled above).
            educator.status = EducatorStatus.PENDING.value
            educator.unsubscribed_at = None

    if name:
        educator.name = name
    if institution:
        educator.institution = institution
    educator.language = language

    _grant_access(educator, now)
    db.commit()
    return {
        "success": True,
        "granted": True,
        "token": educator.access_token,
        "expires_at": educator.access_expires_at.isoformat(),
        "mailing_list_pending": mailing_list_optin,
    }


def validate_token(db: Session, token: str | None) -> Educator | None:
    """
    Resolve an opaque gate token to an Educator, or ``None`` if invalid.

    Valid means: token matches a row, the row is not UNSUBSCRIBED, and the
    7-day window has not lapsed. Read-only (no last_access bump) — this runs on
    every gated request.
    """
    if not token:
        return None
    now = _utcnow()
    educator = db.execute(
        select(Educator).where(Educator.access_token == token)
    ).scalar_one_or_none()
    if educator is None or educator.status == EducatorStatus.UNSUBSCRIBED.value:
        return None
    if not educator.access_expires_at or educator.access_expires_at < now:
        return None
    return educator


def session(db: Session, token: str | None) -> dict:
    """Validate a stored token on frontend mount; bump last_access on success."""
    educator = validate_token(db, token)
    if educator is None:
        return {"valid": False, "expires_at": None}
    educator.last_access_at = _utcnow()
    db.commit()
    return {"valid": True, "expires_at": educator.access_expires_at.isoformat()}


def unsubscribe(db: Session, token: str) -> dict:
    """
    Token-authenticated unsubscribe (#51).

    The row is derived from the caller's own session token — there is no email
    parameter, so a third party cannot revoke someone else's access. Revokes
    gate access and opts out of the mailing list.
    """
    educator = db.execute(
        select(Educator).where(Educator.access_token == token)
    ).scalar_one_or_none()

    if educator is None:
        logger.warning("auth failure: %s (%s)", "educator_unsubscribe", "invalid_token")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="auth failure: educator_unsubscribe",
            level="warning",
            data={"event": "educator_unsubscribe", "reason": "invalid_token"},
        )
        return {"success": False, "message": "Invalid or expired session."}

    if educator.status == EducatorStatus.UNSUBSCRIBED.value:
        return {"success": True, "message": "Already unsubscribed."}

    educator.status = EducatorStatus.UNSUBSCRIBED.value
    educator.access_token = None
    educator.access_expires_at = None
    educator.mailing_list = False
    educator.unsubscribed_at = _utcnow()
    db.commit()

    return {"success": True, "message": "You have been unsubscribed."}
