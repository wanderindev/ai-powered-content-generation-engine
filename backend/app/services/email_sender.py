"""
Outbound email transport via Google Workspace SMTP relay.

`send_email()` dispatches via `smtp-relay.gmail.com:587` over STARTTLS using
the Python stdlib. The droplet's outbound IP is allowlisted in Workspace
admin; no SMTP auth is required. Raises `EmailDeliveryError` on any failure
— callers must catch or propagate; silent bool returns are not supported.

Replaces the Composio GMAIL_SEND_EMAIL path that swallowed Gmail-side
failures (issue #67) in session 04. The Composio dependency was fully
removed in session 05 along with the Sheets-based mailing list.

Under ``ENVIRONMENT=test`` (forced by ``tests/conftest.py``) a send that reaches
the *real* ``smtplib.SMTP`` raises ``RealSendBlockedError`` instead of
connecting (#563): the relay authorizes by sender IP, so a test run from any
allowlisted machine would otherwise deliver real mail.
"""

import logging
import smtplib
from email.mime.text import MIMEText

import sentry_sdk

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an outbound email could not be delivered to the SMTP relay."""


class RealSendBlockedError(RuntimeError):
    """Raised when a test run reaches the real SMTP transport (#563).

    Deliberately *not* an ``EmailDeliveryError``: admin-side callers absorb
    that one, and this must fail the test loudly rather than be logged.
    """


# Captured at import, before any test fixture can patch the module attribute.
_REAL_SMTP = smtplib.SMTP


def send_email(to: str, subject: str, body: str) -> None:
    """
    Send an HTML email via the configured SMTP relay.

    Raises EmailDeliveryError on transport, auth, or timeout failure.
    Sentry-captures the underlying exception before re-raising, so callers
    do not need to re-capture.
    """
    settings = get_settings()

    if settings.environment == "test" and smtplib.SMTP is _REAL_SMTP:
        raise RealSendBlockedError(
            f"Refusing a real SMTP send to {to} under ENVIRONMENT=test; "
            "the conftest smtp_outbox guard was bypassed"
        )

    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.sender_email
    msg["To"] = to

    try:
        with smtplib.SMTP(
            settings.smtp_relay_host,
            settings.smtp_relay_port,
            timeout=settings.smtp_relay_timeout,
        ) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.send_message(msg)
        logger.info("Email sent to %s: %s", to, subject)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        logger.error("Failed to send email to %s: %s", to, e)
        sentry_sdk.capture_exception(e)
        raise EmailDeliveryError(f"Failed to send email to {to}: {e}") from e
