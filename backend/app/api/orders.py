"""Order API endpoints."""

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import _bearer_token
from app.core.database import get_db
from app.models import (
    Booking,
    BookingStatus,
    BookingStatusLog,
    Hotel,
    MagicLink,
    MagicLinkAction,
    Order,
    OrderStatus,
    PricingConfig,
    Tour,
    TourAttraction,
)
from app.schemas.order import (
    BookingResponse,
    CancelOrderRequest,
    CreateOrderRequest,
    OrderResponse,
)
from app.services.availability import get_availability_service
from app.services.email_sender import EmailDeliveryError
from app.services.notifications import get_notification_service
from app.services.pricing import calculate_fixed_price, get_pricing_engine

router = APIRouter(prefix="/orders", tags=["orders"])

# Marker written to BookingStatusLog.notes when a customer requests renewal.
# Used to detect a still-pending duplicate request and avoid re-notifying (#38).
_RENEWAL_REQUEST_NOTE = "Customer requested payment link renewal"


def _generate_reference(prefix: str, length: int = 5) -> str:
    """Generate a unique reference like ORD-A1B2C or PIC-X9Y8Z."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Avoid similar-looking chars
    suffix = "".join(secrets.choice(chars) for _ in range(length))
    return f"{prefix}-{suffix}"


def _order_token(
    authorization: str | None = Header(default=None),
) -> str | None:
    """Resolve the customer order token from the ``Authorization: Bearer`` header.

    The transitional ``?token=`` query fallback was removed once all clients sent
    the header (epic #263), keeping the token out of access logs / Referer /
    browser history."""
    return _bearer_token(authorization)


def _validate_magic_link(
    token: str | None, db: Session, required_action: MagicLinkAction | None = None
) -> MagicLink:
    """Validate a magic link token and return the MagicLink record."""
    magic_link = (
        db.query(MagicLink)
        .filter(MagicLink.token == token)
        .filter(MagicLink.expires_at > datetime.now(UTC))
        .filter(MagicLink.used_at.is_(None))
        .first()
    )

    if not magic_link:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if required_action and magic_link.action != required_action.value:
        raise HTTPException(status_code=403, detail="Token not valid for this action")

    return magic_link


def _get_config_value(db: Session, key: str, default: str) -> str:
    """Get a config value from pricing_config."""
    config = db.query(PricingConfig).filter(PricingConfig.key == key).first()
    return config.value if config else default


@router.post("", response_model=OrderResponse)
def create_order(
    request: CreateOrderRequest,
    db: Session = Depends(get_db),
):
    """
    Create an order from cart checkout.

    - Creates order and booking records
    - Blocks time slots
    - Returns order confirmation with magic link token

    Payment link is created separately (PayPal) or manually (Cuanto).
    """
    availability_service = get_availability_service(db)
    pricing_engine = get_pricing_engine(db)

    # Validate all bookings first. Track the pre-discount subtotal and the
    # discount separately so Order.subtotal carries distinct information from
    # Order.grand_total (#39). Each booking's grand_total already nets the
    # senior discount, so subtotal - discount == sum(grand_total) by identity.
    bookings_data = []
    subtotal = Decimal("0")
    discount_total = Decimal("0")

    for item in request.bookings:
        # Get tour
        tour = (
            db.query(Tour)
            .options(joinedload(Tour.attractions).joinedload(TourAttraction.attraction))
            .filter(Tour.slug == item.tour_slug, Tour.is_active.is_(True))
            .first()
        )
        if not tour:
            raise HTTPException(status_code=400, detail=f"Tour not found: {item.tour_slug}")

        # Fixed-price tours (open group) skip availability blocking
        is_fixed_price = tour.fixed_price_config is not None

        if not is_fixed_price:
            # Check availability (private tours only)
            can_book, reason = availability_service.can_book_slot(
                tour_date=item.tour_date,
                time_slot=item.time_slot,
            )
            if not can_book:
                raise HTTPException(
                    status_code=400,
                    detail=f"Slot not available for {tour.name} on {item.tour_date} {item.time_slot}: {reason}",
                )

        # Get hotel zone if transport requested
        hotel = None
        hotel_zone_id = None
        if item.transport and item.transport.hotel_id:
            hotel = db.query(Hotel).filter(Hotel.id == item.transport.hotel_id).first()
            if hotel:
                hotel_zone_id = hotel.zone_id

        # Calculate price
        if is_fixed_price:
            price = calculate_fixed_price(
                config=tour.fixed_price_config,
                participants=item.participants,
            )
        else:
            # Groups of 5+ are transported with a flat round-trip vehicle;
            # one-way transport is not offered at those sizes.
            if (
                item.transport
                and item.participants.total > 4
                and item.transport.direction != "BOTH"
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Transport for groups of 5 or more is round-trip only for {tour.name}"
                    ),
                )

            price = pricing_engine.calculate_full_price(
                tour=tour,
                participants=item.participants,
                transport=item.transport,
                hotel_zone_id=hotel_zone_id,
            )

            if price.requires_quote:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Group size over {tour.max_group_size} requires a "
                        f"custom quote for {tour.name}"
                    ),
                )

        bookings_data.append(
            {
                "item": item,
                "tour": tour,
                "hotel": hotel,
                "price": price,
                "is_fixed_price": is_fixed_price,
            }
        )
        subtotal += price.service_total + price.tickets_total + price.transport_total
        discount_total += price.senior_discount

    # Get reservation validity from config
    reservation_days = int(_get_config_value(db, "reservation_validity_days", "7"))

    # Create order
    order = Order(
        reference=_generate_reference("ORD"),
        customer_name=request.customer.name,
        customer_email=request.customer.email,
        customer_phone=request.customer.phone,
        customer_hotel=request.customer.hotel,
        customer_language=request.customer.language,
        status=OrderStatus.PENDING.value,
        payment_method=request.payment_method,
        subtotal=subtotal,
        grand_total=subtotal - discount_total,
        reservation_expires_at=datetime.now(UTC) + timedelta(days=reservation_days),
    )
    db.add(order)
    db.flush()  # Get order.id

    # Create bookings
    created_bookings = []
    for data in bookings_data:
        item = data["item"]
        tour = data["tour"]
        hotel = data["hotel"]
        price = data["price"]

        participants_dict = item.participants.to_dict()

        booking = Booking(
            reference=_generate_reference("PIC"),
            order_id=order.id,
            tour_id=tour.id,
            tour_date=item.tour_date,
            time_slot=item.time_slot,
            preferred_time=item.preferred_time,
            # Fixed-price tours are exempt from the one-active-booking-per-slot
            # unique index (#40); private tours are constrained.
            allow_multiple_per_slot=data["is_fixed_price"],
            status=BookingStatus.PENDING_PAYMENT.value,
            participants=participants_dict,
            total_participants=item.participants.total,
            total_residents=item.participants.total_residents,
            total_tourists=item.participants.total_tourists,
            needs_transport=item.transport is not None,
            transport_direction=item.transport.direction if item.transport else None,
            hotel_id=hotel.id if hotel else None,
            hotel_name_manual=item.transport.hotel_name_manual
            if item.transport and not hotel
            else None,
            transport_quote_required=price.transport_quote_required,
            service_total=price.service_total,
            senior_discount=price.senior_discount,
            tickets_total=price.tickets_total,
            transport_total=price.transport_total,
            grand_total=price.grand_total,
        )
        db.add(booking)
        created_bookings.append((booking, tour))

    # Create magic link for order access
    magic_link = MagicLink(
        token=secrets.token_urlsafe(32),
        order_id=order.id,
        action=MagicLinkAction.VIEW_ORDER.value,
        # Token lifetime tracks the reservation window (reservation_validity_days,
        # default 7) instead of the old fixed 30 days — a shorter-lived in-URL
        # credential, less blast radius if it leaks (#43).
        expires_at=datetime.now(UTC) + timedelta(days=reservation_days),
    )
    db.add(magic_link)

    # Log status
    log = BookingStatusLog(
        order_id=order.id,
        previous_status=None,
        new_status=OrderStatus.PENDING.value,
        changed_by="system",
        notes="Order created",
    )
    db.add(log)

    try:
        db.commit()
    except IntegrityError:
        # The partial unique index (uq_active_booking_slot) tripped: another
        # order reserved one of these slots between our availability check and
        # this commit. Postgres serialized the race; surface it as a 409 so the
        # customer can pick a different slot (#40).
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="One of the selected time slots was just booked by someone else. "
            "Please choose a different date or time.",
        ) from None
    db.refresh(order)

    # Send notifications (non-blocking - don't fail the order if email fails)
    try:
        notification_service = get_notification_service()
        # Reload bookings with relationships for email templates
        order_bookings = (
            db.query(Booking)
            .options(
                joinedload(Booking.tour),
                joinedload(Booking.hotel),
            )
            .filter(Booking.order_id == order.id)
            .all()
        )
        notification_service.notify_new_order(
            order=order,
            bookings=order_bookings,
            magic_link_token=magic_link.token,
        )
    except EmailDeliveryError as e:
        # Order is persisted; customer-confirmation email failed. The wrapper
        # has Sentry-captured. Diego sees the alert and can resend manually.
        # TODO(#37 layer 2): persist Order.notification_failed_at for an
        # admin-dashboard view of stranded customers.
        import logging

        logging.getLogger(__name__).error(
            "Order %s created but customer email failed: %s", order.reference, e
        )

    # Build response
    return OrderResponse(
        reference=order.reference,
        status=order.status,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        customer_phone=order.customer_phone,
        customer_hotel=order.customer_hotel,
        customer_language=order.customer_language,
        payment_method=order.payment_method,
        subtotal=order.subtotal,
        grand_total=order.grand_total,
        magic_link_token=magic_link.token,
        bookings=[
            BookingResponse(
                reference=b.reference,
                tour_name=t.name,
                tour_name_es=t.name_es,
                tour_slug=t.slug,
                tour_date=b.tour_date,
                time_slot=b.time_slot,
                preferred_time=b.preferred_time,
                participants=b.participants,
                total_participants=b.total_participants,
                total_residents=b.total_residents,
                total_tourists=b.total_tourists,
                needs_transport=b.needs_transport,
                hotel_name=b.hotel.name if b.hotel else b.hotel_name_manual,
                service_total=b.service_total,
                senior_discount=b.senior_discount,
                tickets_total=b.tickets_total,
                transport_total=b.transport_total,
                grand_total=b.grand_total,
                status=b.status,
            )
            for b, t in created_bookings
        ],
        created_at=order.created_at,
        payment_link_expires_at=order.payment_link_expires_at,
        paypal_invoice_url=order.paypal_invoice_url,
    )


@router.get("/{reference}", response_model=OrderResponse)
def get_order(
    reference: str,
    token: str | None = Depends(_order_token),
    db: Session = Depends(get_db),
):
    """
    Get order details (requires valid magic link token).
    """
    magic_link = _validate_magic_link(token, db)

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

    # Verify token is for this order
    if magic_link.order_id != order.id:
        raise HTTPException(status_code=403, detail="Token not valid for this order")

    return OrderResponse(
        reference=order.reference,
        status=order.status,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        customer_phone=order.customer_phone,
        customer_hotel=order.customer_hotel,
        customer_language=order.customer_language,
        payment_method=order.payment_method,
        subtotal=order.subtotal,
        grand_total=order.grand_total,
        bookings=[
            BookingResponse(
                reference=b.reference,
                tour_name=b.tour.name,
                tour_name_es=b.tour.name_es,
                tour_slug=b.tour.slug,
                tour_date=b.tour_date,
                time_slot=b.time_slot,
                preferred_time=b.preferred_time,
                participants=b.participants,
                total_participants=b.total_participants,
                total_residents=b.total_residents,
                total_tourists=b.total_tourists,
                needs_transport=b.needs_transport,
                hotel_name=b.hotel.name if b.hotel else b.hotel_name_manual,
                service_total=b.service_total,
                senior_discount=b.senior_discount,
                tickets_total=b.tickets_total,
                transport_total=b.transport_total,
                grand_total=b.grand_total,
                status=b.status,
            )
            for b in order.bookings
        ],
        created_at=order.created_at,
        payment_link_expires_at=order.payment_link_expires_at,
        paypal_invoice_url=order.paypal_invoice_url,
    )


@router.post("/{reference}/cancel")
def cancel_order(
    reference: str,
    request: CancelOrderRequest,
    token: str | None = Depends(_order_token),
    db: Session = Depends(get_db),
):
    """
    Cancel an unpaid order (requires valid magic link token).
    """
    magic_link = _validate_magic_link(token, db)

    order = db.query(Order).filter(Order.reference == reference).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if magic_link.order_id != order.id:
        raise HTTPException(status_code=403, detail="Token not valid for this order")

    # Can only cancel unpaid orders
    if order.status not in [
        OrderStatus.PENDING.value,
        OrderStatus.PAYMENT_LINK_SENT.value,
    ]:
        raise HTTPException(status_code=400, detail="Cannot cancel order in current status")

    # Update order status
    previous_status = order.status
    order.status = OrderStatus.CANCELLED.value

    # Update all booking statuses
    for booking in order.bookings:
        booking.status = BookingStatus.CANCELLED.value

    # Mark magic link as used
    magic_link.used_at = datetime.now(UTC)

    # Log status change
    log = BookingStatusLog(
        order_id=order.id,
        previous_status=previous_status,
        new_status=OrderStatus.CANCELLED.value,
        changed_by="customer",
        notes=f"Cancelled by customer. Reason: {request.reason or 'Not provided'}",
    )
    db.add(log)

    db.commit()

    # Send cancellation notification (admin-only)
    try:
        notification_service = get_notification_service()
        notification_service.notify_order_cancelled(
            order=order,
            cancelled_by="customer",
            reason=request.reason,
        )
    except EmailDeliveryError as e:
        import logging

        logging.getLogger(__name__).error(
            "Cancellation notification failed for order %s: %s", order.reference, e
        )

    return {"message": "Order cancelled successfully", "reference": order.reference}


@router.post("/{reference}/renew-payment")
def renew_payment_link(
    reference: str,
    token: str | None = Depends(_order_token),
    db: Session = Depends(get_db),
):
    """
    Request a new payment link (requires valid magic link token).

    Used when the original payment link has expired.
    """
    magic_link = _validate_magic_link(token, db)

    order = db.query(Order).filter(Order.reference == reference).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if magic_link.order_id != order.id:
        raise HTTPException(status_code=403, detail="Token not valid for this order")

    # Can only renew for expired/pending orders
    if order.status not in [
        OrderStatus.PENDING.value,
        OrderStatus.PAYMENT_LINK_SENT.value,
        OrderStatus.EXPIRED.value,
    ]:
        raise HTTPException(
            status_code=400, detail="Cannot renew payment for order in current status"
        )

    # Check if reservation has expired
    if order.reservation_expires_at and order.reservation_expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=400,
            detail="Reservation has expired. Please create a new booking.",
        )

    # Renewal is a MANUAL follow-up: Diego re-issues and sends the link. There is
    # no automated path, so the only thing that prompts him is the admin
    # notification below. Idempotency: if the customer already has a still-pending
    # renewal request (the most recent status-log entry is one, with no
    # intervening change), don't notify again — just acknowledge. The magic link
    # is left valid so the customer keeps access to the manage page (#38, #36).
    latest_log = (
        db.query(BookingStatusLog)
        .filter(BookingStatusLog.order_id == order.id)
        .order_by(BookingStatusLog.id.desc())
        .first()
    )
    already_requested = latest_log is not None and latest_log.notes == _RENEWAL_REQUEST_NOTE

    # Reset payment link expiry (a new link will be issued manually).
    order.payment_link_expires_at = None

    # Best-effort PayPal cleanup. No-op on a duplicate request (invoice already
    # cleared by the first one), since the guard below requires a live invoice id.
    if order.payment_method == "PAYPAL" and order.paypal_invoice_id:
        from app.services.paypal import get_paypal_service

        paypal = get_paypal_service()
        try:
            paypal.cancel_invoice(order.paypal_invoice_id)
        except Exception as e:
            # The old invoice may already be cancelled, or PayPal may be
            # transiently unavailable. Do not block renewal, but make the
            # failure observable instead of swallowing it.
            import logging

            import sentry_sdk

            logging.getLogger(__name__).warning(
                "Failed to cancel PayPal invoice %s during renewal of order %s: %s",
                order.paypal_invoice_id,
                order.reference,
                e,
            )
            sentry_sdk.capture_exception(e)

        order.paypal_invoice_id = None
        order.paypal_invoice_url = None
        order.status = OrderStatus.PENDING.value

    if not already_requested:
        db.add(
            BookingStatusLog(
                order_id=order.id,
                previous_status=order.status,
                new_status=order.status,  # Status doesn't change
                changed_by="customer",
                notes=_RENEWAL_REQUEST_NOTE,
            )
        )

    db.commit()

    # Notify Diego so he knows to re-issue the link (only on a fresh request).
    if not already_requested:
        try:
            get_notification_service().notify_admin_renewal_request(order)
        except EmailDeliveryError as e:
            import logging

            logging.getLogger(__name__).error(
                "Renewal-request notification failed for order %s: %s", order.reference, e
            )

    return {
        "message": "Your renewal request was received. We've notified our team and "
        "will send you a new payment link by email shortly.",
        "reference": order.reference,
    }
