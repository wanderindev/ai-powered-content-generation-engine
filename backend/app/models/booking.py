"""Booking model for individual tour bookings."""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import (
    BookingStatus,
    OrderStatus,
    TimeSlot,
    TransportDirection,
    status_check,
)

if TYPE_CHECKING:
    from app.models.hotel import Hotel
    from app.models.order import Order
    from app.models.tour import Tour

__all__ = ["Booking", "BookingStatus", "BookingStatusLog", "TimeSlot"]

# Statuses that hold a slot (#40). A second booking for the same
# (tour_date, time_slot) in any of these states is a double-booking.
_ACTIVE_SLOT_STATUSES = (
    BookingStatus.PENDING_PAYMENT,
    BookingStatus.PAYMENT_LINK_SENT,
    BookingStatus.PAID,
    BookingStatus.CONFIRMED,
)
# Partial-index predicate: enforce one active booking per slot, but exempt
# fixed-price (open-group) tours, which intentionally allow many per slot and
# set allow_multiple_per_slot=True.
_ACTIVE_SLOT_PREDICATE = (
    "status IN (" + ", ".join(f"'{s.value}'" for s in _ACTIVE_SLOT_STATUSES) + ")"
    " AND allow_multiple_per_slot = false"
)


class Booking(Base):
    """
    Individual tour booking.

    Each booking represents one tour on one date for a group.
    Multiple bookings can be part of the same order.

    Participant structure (JSON):
    {
        "senior_female": {"resident": 0, "tourist": 1},
        "senior_male": {"resident": 0, "tourist": 0},
        "adult": {"resident": 1, "tourist": 2},
        "child_5_17": {"resident": 0, "tourist": 1},
        "child_under_5": {"resident": 0, "tourist": 0}
    }
    """

    __tablename__ = "bookings"
    __table_args__ = (
        status_check("bookings", "status", BookingStatus),
        status_check("bookings", "time_slot", TimeSlot),
        status_check("bookings", "transport_direction", TransportDirection),
        # #40: serialize concurrent reservations of the same slot at the DB level.
        # Postgres rejects the second active insert; create_order maps it to 409.
        Index(
            "uq_active_booking_slot",
            "tour_date",
            "time_slot",
            unique=True,
            postgresql_where=text(_ACTIVE_SLOT_PREDICATE),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # PIC-XXXXX
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    tour_id: Mapped[int] = mapped_column(ForeignKey("tours.id"), nullable=False, index=True)

    # Scheduling
    tour_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_slot: Mapped[str] = mapped_column(String(2), nullable=False)  # AM/PM
    preferred_time: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "9:00 AM"
    # True for fixed-price (open-group) tours, which allow multiple bookings per
    # slot. The partial unique index (uq_active_booking_slot) excludes these rows
    # so only private tours are constrained to one active booking per slot (#40).
    allow_multiple_per_slot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    # Status
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.PENDING_PAYMENT.value)

    # Participants (stored as JSON for flexibility)
    participants: Mapped[dict] = mapped_column(JSON, nullable=False)
    total_participants: Mapped[int] = mapped_column(Integer, nullable=False)
    total_residents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tourists: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Transport
    needs_transport: Mapped[bool] = mapped_column(Boolean, default=False)
    transport_direction: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # TO, FROM, BOTH
    hotel_id: Mapped[int | None] = mapped_column(ForeignKey("hotels.id"), nullable=True, index=True)
    hotel_name_manual: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # If not in list
    transport_quote_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # Pricing breakdown (stored at time of booking)
    service_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    senior_discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    tickets_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    transport_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    order: Mapped["Order"] = relationship("Order", back_populates="bookings")
    tour: Mapped["Tour"] = relationship("Tour")
    hotel: Mapped["Hotel | None"] = relationship("Hotel")


class BookingStatusLog(Base):
    """
    Audit trail for booking and order status changes.
    """

    __tablename__ = "booking_status_log"
    # The log records both booking and order transitions, so each column accepts
    # the union of the two status vocabularies.
    __table_args__ = (
        status_check("booking_status_log", "previous_status", BookingStatus, OrderStatus),
        status_check("booking_status_log", "new_status", BookingStatus, OrderStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id"), nullable=True, index=True
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_by: Mapped[str | None] = mapped_column(
        String(254), nullable=True
    )  # admin email or "system"
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # PayPal webhook event id (top-level ``id`` in the webhook payload). Unique so a
    # redelivered webhook cannot double-process an order. NULL for admin-driven logs;
    # Postgres allows multiple NULLs under a UNIQUE constraint, so they are unaffected.
    paypal_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
