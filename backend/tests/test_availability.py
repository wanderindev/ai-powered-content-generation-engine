"""
Tests for AvailabilityService.

Issue #46 consolidated the duplicated availability query: ``is_slot_available``
is now a thin wrapper over ``get_blocking_reason``. These tests pin the
behaviour-preserving invariant that the two methods always agree
(``available`` iff ``reason is None``) across the free, paid-blocked, and
actively-reserved cases.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models import (
    Booking,
    BookingStatus,
    Order,
    OrderStatus,
    Tour,
    Zone,
)
from app.services.availability import AvailabilityService

# A date far in the future so it never collides with "past" guards.
FUTURE_DATE = date(2099, 1, 1)


def _create_tour(test_db):
    zone = Zone(
        name="Test Zone",
        name_es="Zona de Prueba",
        display_order=1,
        is_active=True,
    )
    test_db.add(zone)
    test_db.commit()
    tour = Tour(
        slug="test-tour",
        name="Test Tour",
        name_es="Tour de Prueba",
        duration_hours=Decimal("2.0"),
        max_group_size=7,
        zone_id=zone.id,
        is_active=True,
    )
    test_db.add(tour)
    test_db.commit()
    test_db.refresh(tour)
    return tour


def _create_order(test_db, status=OrderStatus.PENDING, reservation_expires_at=None):
    order = Order(
        reference="ORD-AVAIL-TEST",
        customer_name="Test Customer",
        customer_email="customer@example.com",
        customer_phone="+1234567890",
        customer_language="en",
        status=status.value,
        payment_method="PAYPAL",
        subtotal=Decimal("100.00"),
        grand_total=Decimal("100.00"),
        reservation_expires_at=reservation_expires_at,
    )
    test_db.add(order)
    test_db.commit()
    test_db.refresh(order)
    return order


def _create_booking(
    test_db,
    tour,
    order,
    status=BookingStatus.PENDING_PAYMENT,
    tour_date=FUTURE_DATE,
    time_slot="AM",
):
    booking = Booking(
        reference="PIC-AVAIL-TEST",
        order_id=order.id,
        tour_id=tour.id,
        tour_date=tour_date,
        time_slot=time_slot,
        participants={"adult": {"resident": 0, "tourist": 2}},
        total_participants=2,
        total_residents=0,
        total_tourists=2,
        service_total=Decimal("100.00"),
        grand_total=Decimal("100.00"),
        status=status.value,
    )
    test_db.add(booking)
    test_db.commit()
    test_db.refresh(booking)
    return booking


def test_methods_agree_when_slot_is_free(test_db):
    """Free slot: available is True and there is no blocking reason."""
    _create_tour(test_db)
    svc = AvailabilityService(test_db)

    reason = svc.get_blocking_reason(FUTURE_DATE, "AM")
    available = svc.is_slot_available(FUTURE_DATE, "AM")

    assert reason is None
    assert available is True
    assert available is (reason is None)


def test_methods_agree_when_slot_is_paid_blocked(test_db):
    """A PAID booking blocks the slot in both methods."""
    tour = _create_tour(test_db)
    order = _create_order(test_db, status=OrderStatus.PAID)
    _create_booking(test_db, tour, order, status=BookingStatus.PAID)
    svc = AvailabilityService(test_db)

    reason = svc.get_blocking_reason(FUTURE_DATE, "AM")
    available = svc.is_slot_available(FUTURE_DATE, "AM")

    assert reason == "This time slot is already booked."
    assert available is False
    assert available is (reason is None)


def test_methods_agree_when_slot_is_actively_reserved(test_db):
    """A pending booking with an unexpired reservation blocks both methods."""
    tour = _create_tour(test_db)
    order = _create_order(
        test_db,
        status=OrderStatus.PENDING,
        reservation_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    _create_booking(
        test_db, tour, order, status=BookingStatus.PENDING_PAYMENT
    )
    svc = AvailabilityService(test_db)

    reason = svc.get_blocking_reason(FUTURE_DATE, "AM")
    available = svc.is_slot_available(FUTURE_DATE, "AM")

    assert reason == "This time slot is temporarily held for another booking."
    assert available is False
    assert available is (reason is None)


def test_methods_agree_when_reservation_expired(test_db):
    """An expired pending reservation no longer blocks either method."""
    tour = _create_tour(test_db)
    order = _create_order(
        test_db,
        status=OrderStatus.PENDING,
        reservation_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    _create_booking(
        test_db, tour, order, status=BookingStatus.PENDING_PAYMENT
    )
    svc = AvailabilityService(test_db)

    reason = svc.get_blocking_reason(FUTURE_DATE, "AM")
    available = svc.is_slot_available(FUTURE_DATE, "AM")

    assert reason is None
    assert available is True
    assert available is (reason is None)
