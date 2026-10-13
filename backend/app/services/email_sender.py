"""
Outbound email transport via Google Workspace SMTP relay.

`send_email()` dispatches via `smtp-relay.gmail.com:587` over STARTTLS using
the Python stdlib. The droplet's outbound IP is allowlisted in Workspace
admin; no SMTP auth is required. Raises `EmailDeliveryError` on any failure
— callers must catch or propagate; silent bool returns are not supported.

Replaces the Composio GMAIL_SEND_EMAIL path that swallowed Gmail-side
failures (issue #67) in session 04. The Composio dependency was fully
removed in session 05 along with the Sheets-based mailing list.
"""

import logging
import smtplib
from email.mime.text import MIMEText

import sentry_sdk

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an outbound email could not be delivered to the SMTP relay."""


def send_email(to: str, subject: str, body: str) -> None:
    """
    Send an HTML email via the configured SMTP relay.

    Raises EmailDeliveryError on transport, auth, or timeout failure.
    Sentry-captures the underlying exception before re-raising, so callers
    do not need to re-capture.
    """
    settings = get_settings()

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
