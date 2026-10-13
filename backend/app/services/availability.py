"""
Availability service for tour booking system.

Manages time slot blocking and availability checking.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Order
from app.schemas.tour import AvailabilitySlot


class AvailabilityService:
    """
    Service to manage tour availability.

    Time slots:
    - AM: Morning tours
    - PM: Afternoon tours

    A slot is blocked if:
    - A PAID or CONFIRMED booking exists for that date/slot
    - A PENDING_PAYMENT booking exists with unexpired reservation
    """

    def __init__(self, db: Session):
        self.db = db

    def is_slot_available(self, tour_date: date, time_slot: str) -> bool:
        """
        Check if a time slot is available.

        Thin wrapper over ``get_blocking_reason`` (the canonical
        implementation, which carries strictly more information): a slot is
        available exactly when there is no blocking reason.

        Args:
            tour_date: Date to check
            time_slot: AM or PM

        Returns:
            True if slot is available
        """
        return self.get_blocking_reason(tour_date, time_slot) is None

    def get_blocking_reason(self, tour_date: date, time_slot: str) -> str | None:
        """
        Get reason why a slot is blocked.

        Args:
            tour_date: Date to check
            time_slot: AM or PM

        Returns:
            Blocking reason or None if available
        """
        now = datetime.now(UTC)

        # Check for confirmed/paid bookings
        confirmed = (
            self.db.query(Booking)
            .filter(
                Booking.tour_date == tour_date,
                Booking.time_slot == time_slot,
                Booking.status.in_(
                    [
                        BookingStatus.PAID.value,
                        BookingStatus.CONFIRMED.value,
                    ]
                ),
            )
            .first()
        )

        if confirmed:
            return "This time slot is already booked."

        # Check for pending bookings with valid reservations
        pending = (
            self.db.query(Booking)
            .join(Order)
            .filter(
                Booking.tour_date == tour_date,
                Booking.time_slot == time_slot,
                Booking.status.in_(
                    [
                        BookingStatus.PENDING_PAYMENT.value,
                        BookingStatus.PAYMENT_LINK_SENT.value,
                    ]
                ),
                or_(
                    Order.reservation_expires_at > now,
                    Order.reservation_expires_at.is_(None),
                ),
            )
            .first()
        )

        if pending:
            return "This time slot is temporarily held for another booking."

        return None

    def get_availability_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[AvailabilitySlot]:
        """
        Get availability for a date range.

        Args:
            start_date: Start of range
            end_date: End of range

        Returns:
            List of availability slots
        """
        slots: list[AvailabilitySlot] = []
        current = start_date

        while current <= end_date:
            slots.append(
                AvailabilitySlot(
                    date=current,
                    am_available=self.is_slot_available(current, "AM"),
                    pm_available=self.is_slot_available(current, "PM"),
                )
            )
            current += timedelta(days=1)

        return slots

    def get_next_available_date(
        self,
        start_date: date,
        time_slot: str | None = None,
        max_days: int = 90,
    ) -> date | None:
        """
        Find the next available date.

        Args:
            start_date: Date to start searching from
            time_slot: Specific slot (AM/PM) or None for either
            max_days: Maximum days to search

        Returns:
            Next available date or None
        """
        current = start_date

        for _ in range(max_days):
            if time_slot:
                if self.is_slot_available(current, time_slot):
                    return current
            else:
                if self.is_slot_available(current, "AM") or self.is_slot_available(current, "PM"):
                    return current
            current += timedelta(days=1)

        return None

    def can_book_slot(
        self,
        tour_date: date,
        time_slot: str,
    ) -> tuple[bool, str | None]:
        """
        Check if a slot can be booked.

        Args:
            tour_date: Date to book
            time_slot: AM or PM

        Returns:
            Tuple of (can_book, reason_if_not)
        """
        # Check if date is in the past
        if tour_date < date.today():
            return False, "Cannot book tours in the past."

        # Check availability
        reason = self.get_blocking_reason(tour_date, time_slot)
        if reason:
            return False, reason

        return True, None


def get_availability_service(db: Session) -> AvailabilityService:
    """Get an availability service instance."""
    return AvailabilityService(db)
