"""Admin API endpoints for tour booking management."""

import logging
from datetime import UTC, date, datetime, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import validate_admin_token
from app.core.database import get_db
from app.models import (
    Booking,
    BookingStatus,
    BookingStatusLog,
    MagicLink,
    Order,
    OrderStatus,
    PricingConfig,
)
from app.schemas.admin import (
    AdminBookingDetail,
    AdminCancelRequest,
    AdminOrderDetail,
    AdminOrderListItem,
    CalendarBookingItem,
    InvoicePreview,
    InvoiceSentResponse,
    MarkPaidRequest,
    MarkPaymentSentRequest,
    RescheduleBookingRequest,
    UpdateOrderNotesRequest,
)
from app.services.availability import get_availability_service
from app.services.email_sender import EmailDeliveryError
from app.services.notifications import get_notification_service
from app.services.orders import apply_paid_transition
from app.services.paypal import get_paypal_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/bookings",
    tags=["booking-admin"],
    dependencies=[Depends(validate_admin_token)],
)


def _get_config_value(db: Session, key: str, default: str) -> str:
    """Get a config value from pricing_config."""
    config = db.query(PricingConfig).filter(PricingConfig.key == key).first()
    return config.value if config else default


def _capture_paypal_orphan(operation: str, context: dict, exc: Exception) -> None:
    """Log + Sentry-capture a PayPal/DB drift with enough context to reconcile by hand (#41).

    PayPal admin endpoints mutate remote PayPal state (create/send/cancel an invoice)
    *before* persisting the matching DB change, and there is no compensating action. When
    the local side fails we cannot un-ring the PayPal bell, so we record exactly what
    drifted: the operation, the order/invoice identifiers, and which PayPal side effect
    already happened (``context["side_effect"]``). Tagged ``paypal_orphan`` so these are
    trivially filterable in Sentry.
    """
    logger.error(
        "PayPal/DB drift in %s; manual reconciliation needed. context=%s",
        operation,
        context,
        exc_info=exc,
    )
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("paypal_orphan", operation)
        scope.set_context("paypal_orphan", {"operation": operation, **context})
        sentry_sdk.capture_exception(exc)


def _commit_or_capture_orphan(db: Session, *, operation: str, context: dict) -> None:
    """Commit the session; on failure roll back, capture the PayPal/DB drift, raise 500 (#41).

    Use after a PayPal mutation has already succeeded and only the local DB write remains.
    Rolling back keeps us from persisting partial state; the captured orphan names the
    completed PayPal side effect (``context["side_effect"]``) so it can be reconciled.
    """
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        _capture_paypal_orphan(operation, context, exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "PayPal operation succeeded but the booking record could not be updated. "
                "This has been logged for manual reconciliation."
            ),
        ) from exc


@router.get("/orders", response_model=list[AdminOrderListItem])
def list_orders(
    status: str | None = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    """
    List orders for admin (requires valid admin magic link).

    Optional filters:
    - status: Filter by order status (PENDING, PAYMENT_LINK_SENT, PAID, etc.)
    """
    query = db.query(Order).options(joinedload(Order.bookings))

    if status:
        query = query.filter(Order.status == status)

    orders = query.order_by(Order.created_at.desc()).limit(100).all()

    result = []
    for order in orders:
        # Get first tour date from bookings
        first_tour_date = None
        if order.bookings:
            first_tour_date = min(b.tour_date for b in order.bookings)
            first_tour_date = datetime.combine(first_tour_date, datetime.min.time())

        result.append(
            AdminOrderListItem(
                reference=order.reference,
                customer_name=order.customer_name,
                customer_email=order.customer_email,
                customer_phone=order.customer_phone,
                status=order.status,
                payment_method=order.payment_method,
                grand_total=order.grand_total,
                created_at=order.created_at,
                paid_at=order.paid_at,
                bookings_count=len(order.bookings),
                first_tour_date=first_tour_date,
            )
        )

    return result


@router.get("/orders/{reference}", response_model=AdminOrderDetail)
def get_order_detail(
    reference: str,
    db: Session = Depends(get_db),
):
    """
    Get full order details for admin.
    """
    order = (
        db.query(Order)
        .options(
            joinedload(Order.bookings).joinedload(Booking.tour),
            joinedload(Order.bookings).joinedload(Booking.hotel),
        )
        .filter(Order.reference == reference)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return AdminOrderDetail(
        reference=order.reference,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        customer_phone=order.customer_phone,
        customer_hotel=order.customer_hotel,
        customer_language=order.customer_language,
        status=order.status,
        payment_method=order.payment_method,
        payment_link_sent_at=order.payment_link_sent_at,
        payment_link_expires_at=order.payment_link_expires_at,
        paid_at=order.paid_at,
        paypal_invoice_id=order.paypal_invoice_id,
        paypal_invoice_url=order.paypal_invoice_url,
        reservation_expires_at=order.reservation_expires_at,
        followup_1_sent_at=order.followup_1_sent_at,
        followup_2_sent_at=order.followup_2_sent_at,
        followup_3_sent_at=order.followup_3_sent_at,
        subtotal=order.subtotal,
        grand_total=order.grand_total,
        admin_notes=order.admin_notes,
        created_at=order.created_at,
        updated_at=order.updated_at,
        bookings=[
            AdminBookingDetail(
                reference=b.reference,
                tour_name=b.tour.name,
                tour_slug=b.tour.slug,
                tour_date=datetime.combine(b.tour_date, datetime.min.time()),
                time_slot=b.time_slot,
                preferred_time=b.preferred_time,
                status=b.status,
                participants=b.participants,
                total_participants=b.total_participants,
                total_residents=b.total_residents,
                total_tourists=b.total_tourists,
                needs_transport=b.needs_transport,
                transport_direction=b.transport_direction,
                hotel_name=b.hotel.name if b.hotel else b.hotel_name_manual,
                transport_quote_required=b.transport_quote_required,
                service_total=b.service_total,
                senior_discount=b.senior_discount,
                tickets_total=b.tickets_total,
                transport_total=b.transport_total,
                grand_total=b.grand_total,
                created_at=b.created_at,
            )
            for b in order.bookings
        ],
    )


@router.post("/orders/{reference}/mark-payment-sent")
def mark_payment_sent(
    reference: str,
    request: MarkPaymentSentRequest,
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """
    Mark that payment link was sent (for Cuanto payments).

    Used when admin manually sends a Cuanto payment link.
    """
    order = db.query(Order).filter(Order.reference == reference).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in [OrderStatus.PENDING.value]:
        raise HTTPException(status_code=400, detail="Can only mark payment sent for pending orders")

    previous_status = order.status
    order.status = OrderStatus.PAYMENT_LINK_SENT.value
    order.payment_link_sent_at = datetime.now(UTC)

    # Log the change
    log = BookingStatusLog(
        order_id=order.id,
        previous_status=previous_status,
        new_status=order.status,
        changed_by=magic_link.admin_email,
        notes=request.notes,
    )
    db.add(log)
    db.commit()

    return {"message": "Order marked as payment link sent", "reference": reference}


@router.post("/orders/{reference}/mark-paid")
def mark_paid(
    reference: str,
    request: MarkPaidRequest,
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """
    Mark order as paid.

    Updates order and all bookings to PAID status.
    """
    # ``of=Order`` locks only the orders row — the joinedload collection produces a
    # LEFT OUTER JOIN, and Postgres rejects FOR UPDATE on the nullable side of one.
    # The lock serializes this admin action against a concurrent PayPal webhook so
    # the order cannot be marked paid twice (issue #34).
    order = (
        db.query(Order)
        .options(joinedload(Order.bookings))
        .filter(Order.reference == reference)
        .with_for_update(of=Order)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status in [OrderStatus.PAID.value, OrderStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=400, detail=f"Cannot mark order as paid: current status is {order.status}"
        )

    # Shared, idempotent transition: flips order + bookings to PAID, writes the
    # audit log, commits. The status guard above means it always transitions here.
    apply_paid_transition(
        db,
        order,
        changed_by=magic_link.admin_email,
        notes=request.notes,
    )

    # Send payment confirmation notification
    try:
        notification_service = get_notification_service()
        # Reload bookings with relationships for email templates
        bookings_with_tours = (
            db.query(Booking)
            .options(
                joinedload(Booking.tour),
                joinedload(Booking.hotel),
            )
            .filter(Booking.order_id == order.id)
            .all()
        )
        notification_service.notify_payment_received(
            order=order,
            bookings=bookings_with_tours,
        )
    except EmailDeliveryError as e:
        import logging

        logging.getLogger(__name__).error(
            "Payment notification failed for order %s: %s", order.reference, e
        )

    return {"message": "Order marked as paid", "reference": reference}


@router.patch("/orders/{reference}/notes")
def update_order_notes(
    reference: str,
    request: UpdateOrderNotesRequest,
    db: Session = Depends(get_db),
):
    """
    Update admin notes on an order.
    """
    order = db.query(Order).filter(Order.reference == reference).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.admin_notes = request.notes
    db.commit()

    return {"message": "Notes updated", "reference": reference}


@router.get("/stats")
def get_booking_stats(
    db: Session = Depends(get_db),
):
    """
    Get booking statistics for admin dashboard.
    """
    # Count orders by status
    status_counts = db.query(Order.status, func.count(Order.id)).group_by(Order.status).all()

    # Count upcoming bookings
    today = datetime.now(UTC).date()
    upcoming_count = (
        db.query(Booking)
        .filter(Booking.tour_date >= today)
        .filter(Booking.status.in_([BookingStatus.PAID.value, BookingStatus.CONFIRMED.value]))
        .count()
    )

    # Calculate total revenue (paid orders)
    total_revenue = (
        db.query(func.sum(Order.grand_total))
        .filter(Order.status == OrderStatus.PAID.value)
        .scalar()
    ) or 0

    return {
        "status_counts": {status: count for status, count in status_counts},
        "upcoming_bookings": upcoming_count,
        "total_revenue": float(total_revenue),
    }


@router.post("/orders/{reference}/create-invoice", response_model=InvoicePreview)
def create_invoice(
    reference: str,
    db: Session = Depends(get_db),
):
    """
    Create a draft PayPal invoice for admin review (not yet sent to customer).
    """
    order = (
        db.query(Order)
        .options(joinedload(Order.bookings).joinedload(Booking.tour))
        .filter(Order.reference == reference)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_method != "PAYPAL":
        raise HTTPException(status_code=400, detail="Order payment method is not PayPal")

    if order.status not in [OrderStatus.PENDING.value]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create invoice for order in {order.status} status",
        )

    if order.paypal_invoice_id:
        raise HTTPException(
            status_code=400,
            detail=f"Invoice already exists: {order.paypal_invoice_id}",
        )

    paypal = get_paypal_service()
    result = paypal.create_draft_invoice(
        order,
        list(order.bookings),
        due_hours=int(_get_config_value(db, "payment_link_validity_hours", "48")),
    )

    # Store the draft invoice ID
    order.paypal_invoice_id = result["invoice_id"]
    _commit_or_capture_orphan(
        db,
        operation="create_invoice",
        context={
            "order_reference": order.reference,
            "paypal_invoice_id": result["invoice_id"],
            "side_effect": "draft invoice created on PayPal; DB has no pointer to it",
        },
    )

    return InvoicePreview(**result)


@router.post("/orders/{reference}/send-invoice", response_model=InvoiceSentResponse)
def send_invoice(
    reference: str,
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """
    Send a draft PayPal invoice to the customer.

    Updates order status to PAYMENT_LINK_SENT.
    """
    order = db.query(Order).filter(Order.reference == reference).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.paypal_invoice_id:
        raise HTTPException(status_code=400, detail="No draft invoice to send. Create one first.")

    if order.status not in [OrderStatus.PENDING.value]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot send invoice for order in {order.status} status",
        )

    paypal = get_paypal_service()
    payer_view_url = paypal.send_invoice(order.paypal_invoice_id)

    # Update order
    previous_status = order.status
    order.status = OrderStatus.PAYMENT_LINK_SENT.value
    order.paypal_invoice_url = payer_view_url
    order.payment_link_sent_at = datetime.now(UTC)
    validity_hours = int(_get_config_value(db, "payment_link_validity_hours", "48"))
    order.payment_link_expires_at = datetime.now(UTC) + timedelta(hours=validity_hours)

    log = BookingStatusLog(
        order_id=order.id,
        previous_status=previous_status,
        new_status=order.status,
        changed_by=magic_link.admin_email,
        notes=f"PayPal invoice {order.paypal_invoice_id} sent to {order.customer_email}",
    )
    db.add(log)
    _commit_or_capture_orphan(
        db,
        operation="send_invoice",
        context={
            "order_reference": order.reference,
            "paypal_invoice_id": order.paypal_invoice_id,
            "customer_email": order.customer_email,
            "side_effect": "customer was emailed the invoice; DB still shows status=PENDING",
        },
    )

    return InvoiceSentResponse(
        invoice_id=order.paypal_invoice_id,
        payer_view_url=payer_view_url,
        message="Invoice sent to customer",
    )


@router.post("/orders/{reference}/cancel-invoice")
def cancel_invoice(
    reference: str,
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """
    Cancel a PayPal invoice (draft or sent but unpaid).
    """
    order = db.query(Order).filter(Order.reference == reference).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.paypal_invoice_id:
        raise HTTPException(status_code=400, detail="No invoice to cancel")

    if order.status == OrderStatus.PAID.value:
        raise HTTPException(status_code=400, detail="Cannot cancel invoice for a paid order")

    paypal = get_paypal_service()
    cancelled_invoice_id = order.paypal_invoice_id
    success = paypal.cancel_invoice(cancelled_invoice_id)

    if not success:
        raise HTTPException(status_code=502, detail="Failed to cancel invoice on PayPal")

    previous_status = order.status
    order.status = OrderStatus.PENDING.value
    order.paypal_invoice_id = None
    order.paypal_invoice_url = None
    order.payment_link_sent_at = None
    order.payment_link_expires_at = None

    log = BookingStatusLog(
        order_id=order.id,
        previous_status=previous_status,
        new_status=order.status,
        changed_by=magic_link.admin_email,
        notes="PayPal invoice cancelled",
    )
    db.add(log)
    _commit_or_capture_orphan(
        db,
        operation="cancel_invoice",
        context={
            "order_reference": order.reference,
            "paypal_invoice_id": cancelled_invoice_id,
            "side_effect": (
                "invoice cancelled on PayPal (customer notified); DB still shows it active"
            ),
        },
    )

    return {"message": "Invoice cancelled", "reference": reference}


@router.get("/calendar", response_model=list[CalendarBookingItem])
def get_calendar_bookings(
    start_date: date | None = Query(None, description="Start date (default: today)"),
    end_date: date | None = Query(None, description="End date (default: start + 60 days)"),
    db: Session = Depends(get_db),
):
    """
    Get bookings for calendar view, grouped by date.
    """
    if not start_date:
        start_date = date.today()
    if not end_date:
        end_date = start_date + timedelta(days=60)

    bookings = (
        db.query(Booking)
        .options(
            joinedload(Booking.tour),
            joinedload(Booking.order),
        )
        .filter(
            Booking.tour_date >= start_date,
            Booking.tour_date <= end_date,
            Booking.status != BookingStatus.CANCELLED.value,
        )
        .order_by(Booking.tour_date, Booking.time_slot)
        .all()
    )

    return [
        CalendarBookingItem(
            date=b.tour_date.isoformat(),
            time_slot=b.time_slot,
            preferred_time=b.preferred_time,
            booking_reference=b.reference,
            order_reference=b.order.reference,
            tour_name=b.tour.name,
            tour_slug=b.tour.slug,
            total_participants=b.total_participants,
            status=b.status,
            order_status=b.order.status,
            payment_method=b.order.payment_method,
            customer_name=b.order.customer_name,
            needs_transport=b.needs_transport,
            grand_total=b.grand_total,
        )
        for b in bookings
    ]


@router.post("/orders/{reference}/admin-cancel")
def admin_cancel_order(
    reference: str,
    request: AdminCancelRequest,
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """
    Cancel an order from the admin side.

    Cancels PayPal invoice if present and unpaid.
    """
    order = (
        db.query(Order)
        .options(joinedload(Order.bookings))
        .filter(Order.reference == reference)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status == OrderStatus.CANCELLED.value:
        raise HTTPException(status_code=400, detail="Order is already cancelled")

    # Cancel PayPal invoice if exists and not paid
    if order.paypal_invoice_id and order.status != OrderStatus.PAID.value:
        try:
            paypal = get_paypal_service()
            paypal.cancel_invoice(order.paypal_invoice_id)
        except Exception as e:
            logger.warning("Failed to cancel PayPal invoice %s: %s", order.paypal_invoice_id, e)

    previous_status = order.status
    order.status = OrderStatus.CANCELLED.value

    for booking in order.bookings:
        booking.status = BookingStatus.CANCELLED.value

    log = BookingStatusLog(
        order_id=order.id,
        previous_status=previous_status,
        new_status=OrderStatus.CANCELLED.value,
        changed_by=magic_link.admin_email,
        notes=f"Cancelled by admin. Reason: {request.reason or 'Not provided'}",
    )
    db.add(log)
    db.commit()

    try:
        notification_service = get_notification_service()
        notification_service.notify_order_cancelled(
            order=order,
            cancelled_by=f"admin ({magic_link.admin_email})",
            reason=request.reason,
        )
    except EmailDeliveryError as e:
        logger.error("Cancellation notification failed for order %s: %s", order.reference, e)

    return {"message": "Order cancelled", "reference": reference}


@router.post("/{booking_reference}/reschedule")
def reschedule_booking(
    booking_reference: str,
    request: RescheduleBookingRequest,
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """
    Reschedule a booking to a new date/time slot.

    If the order has an unpaid PayPal invoice, cancels it and creates a new one.
    If paid, only updates the booking date and notifies the customer.
    """
    booking = (
        db.query(Booking)
        .options(
            joinedload(Booking.tour),
            joinedload(Booking.order).joinedload(Order.bookings).joinedload(Booking.tour),
        )
        .filter(Booking.reference == booking_reference)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.status == BookingStatus.CANCELLED.value:
        raise HTTPException(status_code=400, detail="Cannot reschedule a cancelled booking")

    # Parse and validate new date
    try:
        new_date = date.fromisoformat(request.new_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Check availability (but skip the current booking's slot)
    availability_service = get_availability_service(db)
    old_date = booking.tour_date
    old_slot = booking.time_slot

    # Temporarily "free" the current slot for the check if same booking
    is_same_slot = new_date == old_date and request.new_time_slot == old_slot
    if not is_same_slot:
        can_book, reason = availability_service.can_book_slot(new_date, request.new_time_slot)
        if not can_book:
            raise HTTPException(
                status_code=400,
                detail=f"Slot not available: {reason}",
            )

    # Update booking
    booking.tour_date = new_date
    booking.time_slot = request.new_time_slot
    booking.preferred_time = request.new_preferred_time

    # Log the change
    log = BookingStatusLog(
        booking_id=booking.id,
        order_id=booking.order_id,
        previous_status=booking.status,
        new_status=booking.status,
        changed_by=magic_link.admin_email,
        notes=(
            f"Rescheduled from {old_date.isoformat()} {old_slot} to "
            f"{new_date.isoformat()} {request.new_time_slot}. "
            f"Reason: {request.reason or 'Not provided'}"
        ),
    )
    db.add(log)

    order = booking.order
    order_reference = order.reference

    # Handle PayPal invoice for unpaid orders. The re-issue is cancel-old + create-new +
    # send-new in sequence; any step can fail mid-way (#41). Previously the except merely
    # logged and fell through to db.commit(), persisting the booking-date change against a
    # cancelled/missing invoice. Now a failure rolls the WHOLE reschedule back (booking date
    # included) and surfaces the orphan — either both the date and the invoice move, or
    # neither does on our side.
    if (
        order.payment_method == "PAYPAL"
        and order.paypal_invoice_id
        and order.status != OrderStatus.PAID.value
    ):
        old_invoice_id = order.paypal_invoice_id
        try:
            paypal = get_paypal_service()
            # Cancel old invoice
            paypal.cancel_invoice(order.paypal_invoice_id)
            order.paypal_invoice_id = None
            order.paypal_invoice_url = None
            order.payment_link_sent_at = None
            order.payment_link_expires_at = None
            order.status = OrderStatus.PENDING.value

            # Create new invoice with updated booking details
            order_bookings = (
                db.query(Booking)
                .options(joinedload(Booking.tour))
                .filter(Booking.order_id == order.id)
                .all()
            )
            result = paypal.create_draft_invoice(
                order,
                order_bookings,
                due_hours=int(_get_config_value(db, "payment_link_validity_hours", "48")),
            )
            order.paypal_invoice_id = result["invoice_id"]

            # Auto-send the new invoice
            payer_view_url = paypal.send_invoice(order.paypal_invoice_id)
            order.status = OrderStatus.PAYMENT_LINK_SENT.value
            order.paypal_invoice_url = payer_view_url
            order.payment_link_sent_at = datetime.now(UTC)
            validity_hours = int(_get_config_value(db, "payment_link_validity_hours", "48"))
            order.payment_link_expires_at = datetime.now(UTC) + timedelta(hours=validity_hours)

            rescheduled_log = BookingStatusLog(
                order_id=order.id,
                previous_status=OrderStatus.PENDING.value,
                new_status=OrderStatus.PAYMENT_LINK_SENT.value,
                changed_by=magic_link.admin_email,
                notes="New PayPal invoice created and sent after reschedule",
            )
            db.add(rescheduled_log)
        except Exception as exc:
            db.rollback()
            _capture_paypal_orphan(
                "reschedule_booking",
                {
                    "booking_reference": booking_reference,
                    "order_reference": order_reference,
                    "old_invoice_id": old_invoice_id,
                    "side_effect": (
                        "old PayPal invoice may have been cancelled and/or a new one created "
                        "before the failure; booking-date change rolled back to stay consistent"
                    ),
                },
                exc,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Could not re-issue the PayPal invoice for the new date. The booking was "
                    "not rescheduled. This has been logged for manual reconciliation."
                ),
            ) from exc

    _commit_or_capture_orphan(
        db,
        operation="reschedule_booking",
        context={
            "booking_reference": booking_reference,
            "order_reference": order_reference,
            "side_effect": "booking rescheduled; PayPal invoice re-issued if applicable",
        },
    )

    # Notify customer
    if request.notify_customer:
        try:
            notification_service = get_notification_service()
            notification_service.notify_booking_rescheduled(
                order=order,
                booking=booking,
                old_date=old_date,
                old_time_slot=old_slot,
                reason=request.reason,
            )
        except EmailDeliveryError as e:
            logger.error("Reschedule notification failed for order %s: %s", order.reference, e)

    return {
        "message": "Booking rescheduled",
        "booking_reference": booking_reference,
        "new_date": new_date.isoformat(),
        "new_time_slot": request.new_time_slot,
    }


@router.get("/orders/{reference}/invoice-status")
def get_invoice_status(
    reference: str,
    db: Session = Depends(get_db),
):
    """
    Check the live status of a PayPal invoice.
    """
    order = db.query(Order).filter(Order.reference == reference).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.paypal_invoice_id:
        raise HTTPException(status_code=400, detail="No PayPal invoice for this order")

    paypal = get_paypal_service()
    status = paypal.get_invoice_status(order.paypal_invoice_id)

    return {
        "invoice_id": order.paypal_invoice_id,
        "status": status,
        "order_reference": reference,
    }
