"""Authentication API endpoints for magic links."""

import logging
import secrets
from datetime import UTC, datetime, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_token
from app.core.config import get_settings
from app.core.database import get_db
from app.models import AdminEmail, MagicLink, MagicLinkAction
from app.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    VerifyTokenRequest,
    VerifyTokenResponse,
)
from app.services.email_sender import EmailDeliveryError
from app.services.notifications import get_notification_service

logger = logging.getLogger(__name__)

# How long an admin magic link (and the session it becomes) stays valid. A week,
# not a day: the operator works long content-generation sessions and a 24h token
# expired mid-week (2026-10-04). Logout still revokes it server-side.
ADMIN_LINK_VALIDITY = timedelta(days=7)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/verify-token", response_model=VerifyTokenResponse)
def verify_token(
    request: VerifyTokenRequest,
    db: Session = Depends(get_db),
):
    """
    Verify a magic link token.

    Returns:
    - valid: Whether the token is valid and not expired
    - action: The allowed action for this token
    - order_reference: Order reference if applicable
    - booking_reference: Booking reference if applicable
    - admin_email: Admin email if this is an admin token
    - expires_at: When the token expires
    """
    magic_link = db.query(MagicLink).filter(MagicLink.token == request.token).first()

    if not magic_link:
        logger.warning("auth failure: %s (%s)", "magic_link_verify", "token_not_found")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="auth failure: magic_link_verify",
            level="warning",
            data={"event": "magic_link_verify", "reason": "token_not_found"},
        )
        return VerifyTokenResponse(valid=False)

    # Check if expired
    if magic_link.expires_at < datetime.now(UTC):
        logger.warning("auth failure: %s (%s)", "magic_link_verify", "token_expired")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="auth failure: magic_link_verify",
            level="warning",
            data={"event": "magic_link_verify", "reason": "token_expired"},
        )
        return VerifyTokenResponse(valid=False)

    # Check if already used (for single-use actions)
    if magic_link.used_at and magic_link.action in [
        MagicLinkAction.CANCEL_BOOKING.value,
        MagicLinkAction.RENEW_PAYMENT.value,
    ]:
        return VerifyTokenResponse(valid=False)

    # Get order/booking reference if applicable
    order_reference = None
    booking_reference = None
    admin_email = None

    if magic_link.order_id:
        from app.models import Order

        order = db.query(Order).filter(Order.id == magic_link.order_id).first()
        if order:
            order_reference = order.reference

    if magic_link.booking_id:
        from app.models import Booking

        booking = db.query(Booking).filter(Booking.id == magic_link.booking_id).first()
        if booking:
            booking_reference = booking.reference

    if magic_link.admin_email:
        admin_email = magic_link.admin_email

    return VerifyTokenResponse(
        valid=True,
        action=magic_link.action,
        order_reference=order_reference,
        booking_reference=booking_reference,
        admin_email=admin_email,
        expires_at=magic_link.expires_at,
    )


@router.post("/admin/request-access", response_model=AdminLoginResponse)
def request_admin_access(
    request: AdminLoginRequest,
    db: Session = Depends(get_db),
):
    """
    Request admin access magic link.

    - Checks if email is in admin_emails table
    - Creates magic link token
    - Returns success message (actual email sending is handled by Make.com)

    Note: For security, always returns success even if email is not found.
    """
    # Check if email is in admin list
    admin = (
        db.query(AdminEmail)
        .filter(AdminEmail.email == request.email)
        .filter(AdminEmail.is_active.is_(True))
        .first()
    )

    if not admin:
        logger.warning("auth failure: %s (%s)", "admin_login_request", "email_not_in_allowlist")
        sentry_sdk.add_breadcrumb(
            category="auth",
            message="auth failure: admin_login_request",
            level="warning",
            data={
                "event": "admin_login_request",
                "reason": "email_not_in_allowlist",
                "email": request.email,
            },
        )
        # Return success for security (don't reveal if email exists)
        return AdminLoginResponse(
            message="If this email is registered, you will receive a login link shortly.",
            email_sent=False,  # Actually false, but don't reveal
        )

    # Create magic link for admin access
    magic_link = MagicLink(
        token=secrets.token_urlsafe(32),
        admin_email=request.email,
        action=MagicLinkAction.ADMIN_ACCESS.value,
        expires_at=datetime.now(UTC) + ADMIN_LINK_VALIDITY,
    )
    db.add(magic_link)
    db.commit()

    # Send magic link email
    settings = get_settings()
    # Deliver the token in the URL *fragment* (#token=...), not the query string:
    # browsers never send the fragment to the server, so it stays out of access
    # logs and Referer headers. The SPA reads it from window.location.hash (#57).
    magic_link_url = f"{settings.frontend_url}/admin#token={magic_link.token}"
    try:
        notification_service = get_notification_service()
        notification_service.send_admin_magic_link(
            admin_email=request.email,
            magic_link_url=magic_link_url,
        )
    except EmailDeliveryError as e:
        import logging

        logging.getLogger(__name__).error(
            "Admin magic link email failed for %s: %s", request.email, e
        )

    return AdminLoginResponse(
        message="If this email is registered, you will receive a login link shortly.",
        email_sent=True,
    )


@router.post("/admin/logout", status_code=204)
def admin_logout(
    magic_link: MagicLink = Depends(validate_admin_token),
    db: Session = Depends(get_db),
) -> Response:
    """Revoke the caller's admin magic link on logout (#57).

    Admin tokens are valid for 7 days; without server-side revocation a token stays
    replayable from browser history / logs after the admin signs out (the old
    client-only logout just cleared sessionStorage). Deleting the row makes
    logout effective immediately. The dev-token bypass resolves to a synthetic
    in-memory link with no DB row, so it is skipped.
    """
    if isinstance(magic_link, MagicLink):
        db.delete(magic_link)
        db.commit()
    return Response(status_code=204)
