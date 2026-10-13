"""
Mailing list service.

Manages mailing-list subscriptions against the `educators` DB table (the
same table the educator-access flow at /api/v1/educators/* uses). The
`mailing_list: bool` column flags marketing-email opt-in; the `source`
column records the entry CTA (e.g., "MailingListBlog",
"MailingListEducators"). Migrated from the Composio Google Sheets path
in session 05; the previous Sheets-based implementation suffered from a
check-then-act race (#75) that this migration resolves at the database
level via the table's `UNIQUE(email)` constraint.
"""

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.educator import Educator
from app.services.email_sender import EmailDeliveryError, send_email

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)


def _utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime (matches DB columns)."""
    return datetime.now(UTC)


def _send_confirmation_email(email: str, token: str, language: str) -> None:
    """
    Send a confirmation email with a link to confirm the subscription.

    Propagates EmailDeliveryError on send failure — caller decides how to
    surface to the user.
    """
    from app.core.config import get_settings

    settings = get_settings()
    confirm_url = f"{settings.frontend_url}/confirm-subscription?token={token}&lang={language}"

    if language == "es":
        subject = "Confirma tu suscripción a Panama In Context"
    else:
        subject = "Confirm your Panama In Context subscription"

    body = _render(f"subscribe_confirm.{language}.html", confirm_url=confirm_url)

    send_email(email, subject, body)


def subscribe(email: str, source: str, language: str, db: Session) -> dict:
    """
    Subscribe an email to the mailing list.

    Returns a dict with `success`, `message`, and (on success) `status`.

    Concurrency: SELECT-first handles the common no-race case; the
    `educators.email` UNIQUE constraint backstops the genuine race window
    (between the SELECT and the INSERT). On IntegrityError the loser
    re-fetches the now-existing row and routes through the existing-row
    branch. This is the #75 resolution.
    """
    email = email.strip().lower()
    existing = db.query(Educator).filter(Educator.email == email).first()
    if existing is not None:
        return _subscribe_existing(existing, source, language, db)

    token = str(uuid.uuid4())
    educator = Educator(
        email=email,
        source=source,
        language=language,
        mailing_list=True,
        status="PENDING",
        confirm_token=token,
        created_at=_utcnow(),
    )
    db.add(educator)
    try:
        db.commit()
    except IntegrityError:
        # Race window: a concurrent subscribe inserted between our SELECT
        # and our INSERT. UNIQUE(email) caught it; recover gracefully.
        db.rollback()
        existing = db.query(Educator).filter(Educator.email == email).one()
        return _subscribe_existing(existing, source, language, db)

    try:
        _send_confirmation_email(email, token, language)
    except EmailDeliveryError:
        return {
            "success": False,
            "message": "We saved your subscription but couldn't send the confirmation email. Please try again.",
        }
    return {
        "success": True,
        "message": "Check your email to confirm your subscription.",
        "status": "pending",
    }


def _subscribe_existing(educator: Educator, source: str, language: str, db: Session) -> dict:
    """Branch on the existing row's state."""
    email = educator.email

    if educator.status == "CONFIRMED":
        # Existing confirmed row (mailing-list or gate-only). Flip the
        # mailing_list flag if it wasn't already set; no new confirmation
        # email — they already proved they own the address.
        if not educator.mailing_list:
            educator.mailing_list = True
            educator.source = source
            db.commit()
        return {
            "success": True,
            "message": "This email is already subscribed.",
            "status": "already_subscribed",
        }

    if educator.status == "PENDING":
        # Mid-confirmation. Reuse the existing confirm_token (don't disturb
        # any in-flight gate confirmation email). Flip mailing_list if not
        # already set, then re-send a mailing-list-flavored confirmation.
        if not educator.mailing_list:
            educator.mailing_list = True
            educator.source = source
            db.commit()
        try:
            _send_confirmation_email(email, educator.confirm_token, language)
        except EmailDeliveryError:
            return {
                "success": False,
                "message": "We couldn't resend the confirmation email. Please try again.",
            }
        return {
            "success": True,
            "message": "Confirmation email resent. Check your inbox.",
            "status": "pending",
        }

    if educator.status == "UNSUBSCRIBED":
        # Resurrect: new token, PENDING, mailing_list back on.
        educator.status = "PENDING"
        educator.mailing_list = True
        educator.source = source
        educator.confirm_token = str(uuid.uuid4())
        educator.unsubscribed_at = None
        educator.language = language
        db.commit()
        try:
            _send_confirmation_email(email, educator.confirm_token, language)
        except EmailDeliveryError:
            return {
                "success": False,
                "message": "We saved your subscription but couldn't send the confirmation email. Please try again.",
            }
        return {
            "success": True,
            "message": "Check your email to confirm your subscription.",
            "status": "pending",
        }

    # Unknown status — defensive.
    return {"success": False, "message": "Unable to process subscription."}


def confirm(token: str, language: str, db: Session) -> dict:
    """
    Confirm a subscription using the token from the confirmation email.

    Single-use: clears `confirm_token` on success so the link cannot be
    replayed.
    """
    educator = db.query(Educator).filter(Educator.confirm_token == token).first()

    if not educator:
        return {
            "success": False,
            "confirmed": False,
            "email": None,
            "message": "Invalid or expired confirmation token.",
        }

    if educator.status == "PENDING":
        now = _utcnow()
        educator.status = "CONFIRMED"
        educator.confirmed_at = educator.confirmed_at or now
        educator.confirm_token = None
        educator.language = language
        db.commit()
        return {
            "success": True,
            "confirmed": True,
            "email": educator.email,
            "message": "Your subscription has been confirmed!",
        }

    if educator.status == "CONFIRMED":
        # Token-via-CONFIRMED is unusual (we clear the token on confirm),
        # but if it happens just refresh the language preference.
        educator.language = language
        db.commit()
        return {
            "success": True,
            "confirmed": True,
            "email": educator.email,
            "message": "Your subscription is already confirmed.",
        }

    return {
        "success": False,
        "confirmed": False,
        "email": educator.email,
        "message": "Unable to confirm subscription.",
    }


def unsubscribe(email: str, db: Session) -> dict:
    """
    Unsubscribe an email from the mailing list.

    If the row has gate access (`access_expires_at` is not None), only the
    mailing_list flag is flipped — gate access is preserved. Otherwise the
    status flips to UNSUBSCRIBED, matching the prior Sheets-based behavior.
    """
    email = email.strip().lower()
    educator = db.query(Educator).filter(Educator.email == email).first()

    if educator is None or not educator.mailing_list:
        return {
            "success": False,
            "unsubscribed": False,
            "message": "This email is not subscribed.",
        }

    if educator.status == "PENDING":
        return {
            "success": False,
            "unsubscribed": False,
            "message": "This subscription has not been confirmed yet.",
        }

    if educator.status == "UNSUBSCRIBED":
        return {
            "success": True,
            "unsubscribed": True,
            "message": "This email is already unsubscribed.",
        }

    if educator.status == "CONFIRMED":
        educator.mailing_list = False
        educator.unsubscribed_at = _utcnow()
        if educator.access_expires_at is None:
            # No gate access — full unsubscribe.
            educator.status = "UNSUBSCRIBED"
        db.commit()
        return {
            "success": True,
            "unsubscribed": True,
            "message": "You have been unsubscribed successfully.",
        }

    return {
        "success": False,
        "unsubscribed": False,
        "message": "Unable to unsubscribe.",
    }
