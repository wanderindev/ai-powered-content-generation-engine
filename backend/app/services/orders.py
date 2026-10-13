"""Order state-machine transitions.

Shared, idempotent, lock-aware mutations for order/booking status. Callers MUST
fetch the order under ``with_for_update()`` before invoking these helpers so that
concurrent webhook redeliveries and admin actions are serialized per order. This
is the single home for the "mark order paid" transition used by both the PayPal
webhook handler and the admin mark-paid endpoint (issues #32/#33/#34).
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    BookingStatus,
    BookingStatusLog,
    Order,
    OrderStatus,
)


def apply_paid_transition(
    db: Session,
    order: Order,
    *,
    changed_by: str,
    notes: str | None = None,
    paypal_event_id: str | None = None,
) -> bool:
    """Mark ``order`` and all its bookings PAID, write an audit log row, and commit.

    The order MUST already be loaded under ``with_for_update()`` so concurrent
    callers are serialized per order. Idempotent: if the order is already PAID,
    returns ``False`` without mutating or committing. Returns ``True`` when it
    performed the transition — the caller should then fire the payment-received
    notification.

    ``paypal_event_id`` (when provided) is stored on the log row; its UNIQUE
    constraint is the DB-level backstop against double-processing a redelivered
    webhook.
    """
    if order.status == OrderStatus.PAID.value:
        return False

    previous_status = order.status
    order.status = OrderStatus.PAID.value
    order.paid_at = datetime.now(UTC)

    for booking in order.bookings:
        booking.status = BookingStatus.PAID.value

    log = BookingStatusLog(
        order_id=order.id,
        previous_status=previous_status,
        new_status=OrderStatus.PAID.value,
        changed_by=changed_by,
        notes=notes,
        paypal_event_id=paypal_event_id,
    )
    db.add(log)
    db.commit()
    return True
