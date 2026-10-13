"""
Notification service for tour booking system.

Sends emails via the SMTP relay (see email_sender.send_email) for:
- New order notifications (customer + admin)
- Payment confirmation
- Follow-up email requests
- Admin magic link emails
- Order cancellation notifications
- Booking reschedule notifications

HTML bodies are rendered via Jinja2 with autoescape enabled — every interpolated
value is HTML-escaped by default, blocking the injection vector where a customer
submits an attacker-controlled `customer_name` (etc.) that would otherwise render
as live HTML in Diego's inbox. See issue #31.

Contract: methods return None. Customer-facing email failures propagate
EmailDeliveryError — the customer is the critical recipient (a stranded
customer cannot reach their booking; see #36/#37). Admin-side email failures
are caught and logged: the wrapper has already Sentry-captured, and a missed
admin notification is recoverable via the admin UI. This resolves the #70
'customer_sent or admin_sent' bug (which conflated either-success with
both-success) and the discarded-admin-return bug in notify_payment_received.
"""

import logging
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import get_settings
from app.models import Booking, Order
from app.services.email_sender import EmailDeliveryError
from app.services.email_sender import send_email as _send_email

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)


class NotificationService:
    """
    Service to send email notifications via the Google Workspace SMTP relay.

    All emails flow through `email_sender.send_email` which submits to
    `smtp-relay.gmail.com:587` over STARTTLS using stdlib `smtplib`.
    """

    def notify_new_order(
        self,
        order: Order,
        bookings: list[Booking],
        magic_link_token: str,
    ) -> None:
        """
        Send new order notifications.

        Customer email failure propagates EmailDeliveryError (stranded customer
        cannot reach their booking). Admin email failure is logged only.
        """
        settings = get_settings()
        language = order.customer_language or "en"
        # Token rides in the URL *fragment* (#token=...), which browsers never
        # send to the server — keeps it out of access logs and Referer headers.
        # The reference stays in the query string (not sensitive). The SPA reads
        # the token from window.location.hash (#43).
        manage_url = (
            f"{settings.frontend_url}/booking/manage"
            f"?reference={order.reference}#token={magic_link_token}"
        )

        if language == "es":
            customer_subject = f"Panama In Context - Pedido Recibido #{order.reference}"
        else:
            customer_subject = f"Panama In Context - Order Received #{order.reference}"

        customer_body = _render(
            f"new_order_customer.{language}.html",
            order=order,
            bookings=bookings,
            language=language,
            manage_url=manage_url,
        )
        _send_email(order.customer_email, customer_subject, customer_body)

        admin_subject = f"[New Order] #{order.reference} - {order.customer_name}"
        admin_body = _render(
            "new_order_admin.html",
            order=order,
            bookings=bookings,
            language="en",
        )
        try:
            _send_email(settings.admin_notification_email, admin_subject, admin_body)
        except EmailDeliveryError as e:
            logger.error("Admin notification failed for order %s: %s", order.reference, e)

    def notify_payment_received(
        self,
        order: Order,
        bookings: list[Booking],
    ) -> None:
        """
        Send payment confirmation emails to customer and admin.

        Customer email failure propagates EmailDeliveryError. Admin email
        failure is logged only.
        """
        settings = get_settings()
        language = order.customer_language or "en"

        if language == "es":
            subject = f"Panama In Context - Pago Confirmado #{order.reference}"
        else:
            subject = f"Panama In Context - Payment Confirmed #{order.reference}"

        body = _render(
            f"payment_received_customer.{language}.html",
            order=order,
            bookings=bookings,
            language=language,
        )
        _send_email(order.customer_email, subject, body)

        admin_subject = f"[Payment Received] #{order.reference} - {order.customer_name}"
        admin_body = _render("payment_received_admin.html", order=order)
        try:
            _send_email(settings.admin_notification_email, admin_subject, admin_body)
        except EmailDeliveryError as e:
            logger.error("Admin payment notification failed for order %s: %s", order.reference, e)

    def request_followup_email(
        self,
        order: Order,
        followup_number: int,
    ) -> None:
        """Send a followup/reminder email to customer about pending payment."""
        language = order.customer_language or "en"

        if language == "es":
            subject = f"Panama In Context - Recordatorio de Pago #{order.reference}"
        else:
            subject = f"Panama In Context - Payment Reminder #{order.reference}"

        body = _render(f"followup_customer.{language}.html", order=order)
        _send_email(order.customer_email, subject, body)

    def notify_order_cancelled(
        self,
        order: Order,
        cancelled_by: str,
        reason: str | None = None,
    ) -> None:
        """Notify admin about order cancellation."""
        settings = get_settings()

        subject = f"[Order Cancelled] #{order.reference} - {order.customer_name}"
        body = _render(
            "order_cancelled_admin.html",
            order=order,
            cancelled_by=cancelled_by,
            reason=reason,
        )
        _send_email(settings.admin_notification_email, subject, body)

    def notify_admin_renewal_request(self, order: Order) -> None:
        """Notify admin that a customer requested a new payment link (#38).

        Renewal is a manual follow-up — Diego re-issues and sends the link — so
        this notification is the only thing that prompts him. Admin-only: the
        caller catches and logs EmailDeliveryError (a missed admin alert is
        recoverable via the admin order list).
        """
        settings = get_settings()
        subject = f"[Renewal Requested] #{order.reference} - {order.customer_name}"
        body = _render("renewal_request_admin.html", order=order)
        _send_email(settings.admin_notification_email, subject, body)

    def notify_booking_rescheduled(
        self,
        order: Order,
        booking: Booking,
        old_date: date,
        old_time_slot: str,
        reason: str | None = None,
    ) -> None:
        """Notify customer that their booking has been rescheduled."""
        language = order.customer_language or "en"
        tour_name = booking.tour.name_es if language == "es" else booking.tour.name

        if language == "es":
            subject = f"Panama In Context - Reserva Reprogramada #{order.reference}"
        else:
            subject = f"Panama In Context - Booking Rescheduled #{order.reference}"

        body = _render(
            f"booking_rescheduled_customer.{language}.html",
            order=order,
            booking=booking,
            tour_name=tour_name,
            old_date=old_date,
            old_time_slot=old_time_slot,
            reason=reason,
        )
        _send_email(order.customer_email, subject, body)

    def send_admin_magic_link(
        self,
        admin_email: str,
        magic_link_url: str,
    ) -> None:
        """Send admin magic link login email."""
        subject = "Panama In Context - Admin Access Link"
        body = _render("admin_magic_link.html", magic_link_url=magic_link_url)
        _send_email(admin_email, subject, body)


def get_notification_service() -> NotificationService:
    """Get notification service instance."""
    return NotificationService()
