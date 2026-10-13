"""Webhook API endpoints for external integrations."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models import (
    Booking,
    BookingStatusLog,
    Order,
    OrderStatus,
)
from app.services.email_sender import EmailDeliveryError
from app.services.notifications import get_notification_service
from app.services.orders import apply_paid_transition
from app.services.paypal import get_paypal_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/paypal")
async def paypal_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Unified PayPal webhook endpoint.

    Routes by event_type:
    - INVOICING.INVOICE.PAID → mark order as paid
    - INVOICING.INVOICE.CANCELLED → mark order as expired

    Async so the request body is read on the main ASGI event loop. The route was
    previously sync and spawned a fresh event loop on a thread-pool worker to
    await ``request.body()``, which is undefined behavior (issue #33). The
    downstream ``_handle_*`` helpers stay sync — they only do DB work.
    """
    body = await request.body()
    event = json.loads(body)

    # Get webhook headers for signature verification
    transmission_id = request.headers.get("Paypal-Transmission-Id")
    transmission_time = request.headers.get("Paypal-Transmission-Time")
    cert_url = request.headers.get("Paypal-Cert-Url")
    actual_sig = request.headers.get("Paypal-Transmission-Sig")
    auth_algo = request.headers.get("Paypal-Auth-Algo")

    # Verify webhook signature
    paypal_service = get_paypal_service()
    if not paypal_service.verify_webhook_signature(
        transmission_id=transmission_id or "",
        transmission_time=transmission_time or "",
        event_body=body.decode(),
        cert_url=cert_url or "",
        actual_signature=actual_sig or "",
        auth_algo=auth_algo or "",
    ):
        logger.warning("PayPal webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = event.get("event_type")
    event_id = event.get("id")
    logger.info("Received PayPal webhook event: %s (%s)", event_type, event_id)

    if event_type == "INVOICING.INVOICE.PAID":
        return _handle_invoice_paid(event, event_id, db)
    elif event_type == "INVOICING.INVOICE.CANCELLED":
        return _handle_invoice_cancelled(event, db)
    else:
        logger.info("Ignoring PayPal event type: %s", event_type)
        return {"status": "ignored", "event_type": event_type}


def _handle_invoice_paid(event: dict, event_id: str | None, db: Session):
    """Handle INVOICING.INVOICE.PAID event.

    Idempotent against PayPal redeliveries and concurrent workers (issue #34):
    an early event-id dedup check short-circuits the common retry, while the
    ``with_for_update()`` row lock plus the shared :func:`apply_paid_transition`
    PAID guard serialize any concurrent first-deliveries.
    """
    resource = event.get("resource", {})
    invoice_id = resource.get("id")

    if not invoice_id:
        logger.error("PayPal webhook missing invoice ID")
        raise HTTPException(status_code=400, detail="Missing invoice ID")

    # Cheap idempotency layer: a redelivery of an already-processed event id has
    # nothing to do. The UNIQUE constraint on the column is the race backstop.
    if event_id and (
        db.query(BookingStatusLog.id).filter(BookingStatusLog.paypal_event_id == event_id).first()
    ):
        logger.info("PayPal event %s already processed; ignoring redelivery", event_id)
        return {"status": "already_processed", "event_id": event_id}

    # Row-level lock serializes concurrent webhook handlers for this order.
    # ``of=Order`` locks only the orders row — the joinedload collection produces a
    # LEFT OUTER JOIN, and Postgres rejects FOR UPDATE on the nullable side of one.
    order = (
        db.query(Order)
        .options(joinedload(Order.bookings).joinedload(Booking.tour))
        .filter(Order.paypal_invoice_id == invoice_id)
        .with_for_update(of=Order)
        .first()
    )

    if not order:
        logger.warning("Order not found for PayPal invoice: %s", invoice_id)
        return {"status": "order_not_found", "invoice_id": invoice_id}

    transitioned = apply_paid_transition(
        db,
        order,
        changed_by="paypal_webhook",
        notes=f"PayPal invoice {invoice_id} paid",
        paypal_event_id=event_id,
    )

    if not transitioned:
        logger.info("Order %s already marked as paid", order.reference)
        return {"status": "already_paid", "order_reference": order.reference}

    try:
        notification_service = get_notification_service()
        notification_service.notify_payment_received(order, list(order.bookings))
    except EmailDeliveryError as e:
        logger.error("Payment notification failed for %s: %s", order.reference, e)

    logger.info("Order %s marked as paid via PayPal webhook", order.reference)
    return {"status": "success", "order_reference": order.reference}


def _handle_invoice_cancelled(event: dict, db: Session):
    """Handle INVOICING.INVOICE.CANCELLED event."""
    resource = event.get("resource", {})
    invoice_id = resource.get("id")

    if not invoice_id:
        return {"status": "ignored", "reason": "no invoice_id"}

    order = db.query(Order).filter(Order.paypal_invoice_id == invoice_id).with_for_update().first()
    if order and order.status != OrderStatus.PAID.value:
        previous_status = order.status
        order.status = OrderStatus.EXPIRED.value
        log = BookingStatusLog(
            order_id=order.id,
            previous_status=previous_status,
            new_status=OrderStatus.EXPIRED.value,
            changed_by="paypal_webhook",
            notes=f"PayPal invoice {invoice_id} cancelled",
        )
        db.add(log)
        db.commit()

    return {"status": "processed"}


@router.get("/health")
def webhook_health():
    """Health check for webhook endpoint."""
    return {"status": "healthy", "service": "webhooks"}
