"""Tests for the PayPal webhook handler and its hardening (issues #32/#33/#34).

Covers:
- Fail-closed signature verification → 401 (#32).
- The startup config guard (#32).
- The async body read path marking an order paid (#33).
- Idempotency against PayPal redeliveries: one transition, one log row, one
  customer notification (#34).
- The shared ``apply_paid_transition`` helper used by both the webhook and the
  admin mark-paid endpoint (#34).
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest
from app.main import _check_paypal_config
from app.models import (
    Booking,
    BookingStatus,
    BookingStatusLog,
    Order,
    OrderStatus,
    Tour,
    Zone,
)
from app.services.orders import apply_paid_transition

INVOICE_ID = "INV-WH-TEST"


def _seed_order(test_db, *, status=OrderStatus.PENDING.value, paypal_invoice_id=INVOICE_ID):
    """Create a tour + order + booking and return the order."""
    zone = Zone(name="WH Zone", name_es="Zona WH", display_order=1, is_active=True)
    test_db.add(zone)
    test_db.commit()
    tour = Tour(
        slug="wh-tour",
        name="WH Tour",
        name_es="WH Tour ES",
        duration_hours=Decimal("2.0"),
        max_group_size=7,
        zone_id=zone.id,
        is_active=True,
    )
    test_db.add(tour)
    test_db.commit()

    order = Order(
        reference="ORD-WH-TEST",
        customer_name="WH Customer",
        customer_email="wh@example.com",
        customer_phone="+1234567890",
        customer_language="en",
        status=status,
        payment_method="PAYPAL",
        subtotal=Decimal("100.00"),
        grand_total=Decimal("100.00"),
        paypal_invoice_id=paypal_invoice_id,
    )
    test_db.add(order)
    test_db.commit()

    booking = Booking(
        reference="PIC-WH-TEST",
        order_id=order.id,
        tour_id=tour.id,
        tour_date="2099-01-01",
        time_slot="AM",
        participants={"adult": {"resident": 0, "tourist": 2}},
        total_participants=2,
        total_residents=0,
        total_tourists=2,
        service_total=Decimal("100.00"),
        grand_total=Decimal("100.00"),
        status=BookingStatus.PENDING_PAYMENT.value,
    )
    test_db.add(booking)
    test_db.commit()
    test_db.refresh(order)
    return order


def _paid_event(event_id="WH-EVENT-1", invoice_id=INVOICE_ID):
    return {
        "id": event_id,
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": {"id": invoice_id},
    }


def _patch_paypal(monkeypatch, *, verified=True):
    fake = mock.Mock()
    fake.verify_webhook_signature.return_value = verified
    monkeypatch.setattr("app.api.webhooks.get_paypal_service", lambda: fake)
    return fake


def _patch_notifications(monkeypatch):
    fake = mock.Mock()
    monkeypatch.setattr("app.api.webhooks.get_notification_service", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# #32 — fail closed
# ---------------------------------------------------------------------------


def test_invalid_signature_returns_401(test_client, test_db, monkeypatch):
    _seed_order(test_db)
    _patch_paypal(monkeypatch, verified=False)
    notif = _patch_notifications(monkeypatch)

    resp = test_client.post("/api/v1/webhooks/paypal", json=_paid_event())

    assert resp.status_code == 401
    # Order untouched, no notification fired.
    order = test_db.query(Order).filter(Order.reference == "ORD-WH-TEST").first()
    assert order.status == OrderStatus.PENDING.value
    notif.notify_payment_received.assert_not_called()


def test_startup_guard_raises_when_prod_paypal_configured_without_webhook_id():
    cfg = SimpleNamespace(
        environment="production",
        paypal_client_id="client-abc",
        paypal_webhook_id="",
    )
    with pytest.raises(RuntimeError, match="PAYPAL_WEBHOOK_ID"):
        _check_paypal_config(cfg)


@pytest.mark.parametrize(
    "cfg",
    [
        # Not production.
        SimpleNamespace(environment="test", paypal_client_id="x", paypal_webhook_id=""),
        # PayPal not configured at all.
        SimpleNamespace(environment="production", paypal_client_id="", paypal_webhook_id=""),
        # Fully configured.
        SimpleNamespace(environment="production", paypal_client_id="x", paypal_webhook_id="wh"),
    ],
)
def test_startup_guard_allows_valid_configs(cfg):
    _check_paypal_config(cfg)  # must not raise


# ---------------------------------------------------------------------------
# #33 — async body read marks order paid
# ---------------------------------------------------------------------------


def test_invoice_paid_marks_order_and_bookings_paid(test_client, test_db, monkeypatch):
    order = _seed_order(test_db)
    _patch_paypal(monkeypatch, verified=True)
    notif = _patch_notifications(monkeypatch)

    resp = test_client.post("/api/v1/webhooks/paypal", json=_paid_event())

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    test_db.expire_all()
    order = test_db.query(Order).filter(Order.id == order.id).first()
    assert order.status == OrderStatus.PAID.value
    assert order.paid_at is not None
    assert all(b.status == BookingStatus.PAID.value for b in order.bookings)

    logs = test_db.query(BookingStatusLog).filter(BookingStatusLog.order_id == order.id).all()
    assert len(logs) == 1
    assert logs[0].paypal_event_id == "WH-EVENT-1"
    assert logs[0].changed_by == "paypal_webhook"
    notif.notify_payment_received.assert_called_once()


def test_invoice_paid_order_not_found(test_client, test_db, monkeypatch):
    _patch_paypal(monkeypatch, verified=True)
    _patch_notifications(monkeypatch)

    resp = test_client.post(
        "/api/v1/webhooks/paypal",
        json=_paid_event(invoice_id="INV-DOES-NOT-EXIST"),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "order_not_found"


# ---------------------------------------------------------------------------
# #34 — idempotency against redelivery
# ---------------------------------------------------------------------------


def test_redelivered_event_is_idempotent(test_client, test_db, monkeypatch):
    order = _seed_order(test_db)
    _patch_paypal(monkeypatch, verified=True)
    notif = _patch_notifications(monkeypatch)

    first = test_client.post("/api/v1/webhooks/paypal", json=_paid_event())
    second = test_client.post("/api/v1/webhooks/paypal", json=_paid_event())

    assert first.json()["status"] == "success"
    assert second.json()["status"] == "already_processed"

    logs = test_db.query(BookingStatusLog).filter(BookingStatusLog.order_id == order.id).all()
    assert len(logs) == 1  # no duplicate log row
    notif.notify_payment_received.assert_called_once()  # only one email


def test_distinct_event_after_paid_returns_already_paid(test_client, test_db, monkeypatch):
    """A different event id for an already-paid order does not double-process."""
    order = _seed_order(test_db)
    _patch_paypal(monkeypatch, verified=True)
    notif = _patch_notifications(monkeypatch)

    test_client.post("/api/v1/webhooks/paypal", json=_paid_event(event_id="WH-EVENT-1"))
    second = test_client.post(
        "/api/v1/webhooks/paypal", json=_paid_event(event_id="WH-EVENT-2")
    )

    assert second.json()["status"] == "already_paid"
    logs = test_db.query(BookingStatusLog).filter(BookingStatusLog.order_id == order.id).all()
    assert len(logs) == 1
    notif.notify_payment_received.assert_called_once()


def test_invoice_cancelled_marks_expired(test_client, test_db, monkeypatch):
    order = _seed_order(test_db)
    _patch_paypal(monkeypatch, verified=True)
    _patch_notifications(monkeypatch)

    resp = test_client.post(
        "/api/v1/webhooks/paypal",
        json={
            "id": "WH-CANCEL-1",
            "event_type": "INVOICING.INVOICE.CANCELLED",
            "resource": {"id": INVOICE_ID},
        },
    )

    assert resp.status_code == 200
    test_db.expire_all()
    order = test_db.query(Order).filter(Order.id == order.id).first()
    assert order.status == OrderStatus.EXPIRED.value


# ---------------------------------------------------------------------------
# #34 — shared helper unit behavior
# ---------------------------------------------------------------------------


def test_apply_paid_transition_is_idempotent(test_db):
    order = _seed_order(test_db)

    first = apply_paid_transition(test_db, order, changed_by="admin@example.com")
    second = apply_paid_transition(test_db, order, changed_by="admin@example.com")

    assert first is True
    assert second is False  # already PAID → no-op
    logs = test_db.query(BookingStatusLog).filter(BookingStatusLog.order_id == order.id).all()
    assert len(logs) == 1
