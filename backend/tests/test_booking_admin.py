"""
Tests for booking_admin invoice endpoints (issue #46).

These cover the payment-link expiry window being driven by the
``payment_link_validity_hours`` pricing-config key (defaulting to 48h when
absent), and the PayPal ``create_draft_invoice`` due-date being kept in sync
via the ``due_hours`` keyword.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from sqlalchemy.exc import SQLAlchemyError

from app.models import (
    Booking,
    BookingStatus,
    Order,
    OrderStatus,
    PricingConfig,
    Tour,
    Zone,
)

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


def _create_order(test_db, paypal_invoice_id=None):
    order = Order(
        reference="ORD-BA-TEST",
        customer_name="Test Customer",
        customer_email="customer@example.com",
        customer_phone="+1234567890",
        customer_language="en",
        status=OrderStatus.PENDING.value,
        payment_method="PAYPAL",
        subtotal=Decimal("100.00"),
        grand_total=Decimal("100.00"),
        paypal_invoice_id=paypal_invoice_id,
    )
    test_db.add(order)
    test_db.commit()
    test_db.refresh(order)
    return order


def _create_booking(test_db, tour, order):
    booking = Booking(
        reference="PIC-BA-TEST",
        order_id=order.id,
        tour_id=tour.id,
        tour_date=FUTURE_DATE,
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
    test_db.refresh(booking)
    return booking


def _mock_paypal(monkeypatch):
    """Patch booking_admin.get_paypal_service with a Mock service.

    Returns the fake service so call args can be inspected.
    """
    fake = mock.Mock()
    fake.create_draft_invoice.return_value = {
        "invoice_id": "INV-MOCK-123",
        "items": [],
        "due_date": "2099-01-03",
        "total": "100.00",
        "customer_email": "customer@example.com",
        "customer_name": "Test Customer",
    }
    fake.send_invoice.return_value = "https://paypal.test/invoice/INV-MOCK-123"
    monkeypatch.setattr("app.api.booking_admin.get_paypal_service", lambda: fake)
    return fake


def _hours_until(dt):
    return (dt - datetime.now(UTC)).total_seconds() / 3600


def test_send_invoice_defaults_to_48h_when_config_absent(
    test_client, test_db, admin_token, monkeypatch
):
    """No config row -> payment_link_expires_at is ~48h out."""
    tour = _create_tour(test_db)
    order = _create_order(test_db, paypal_invoice_id="INV-MOCK-123")
    _create_booking(test_db, tour, order)
    _mock_paypal(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/send-invoice",
        params={"token": admin_token},
    )

    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    assert order.payment_link_expires_at is not None
    assert 47.5 < _hours_until(order.payment_link_expires_at) <= 48.0


def test_send_invoice_honors_config_validity_hours(test_client, test_db, admin_token, monkeypatch):
    """payment_link_validity_hours row drives the expiry window."""
    test_db.add(PricingConfig(key="payment_link_validity_hours", value="72"))
    test_db.commit()
    tour = _create_tour(test_db)
    order = _create_order(test_db, paypal_invoice_id="INV-MOCK-123")
    _create_booking(test_db, tour, order)
    _mock_paypal(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/send-invoice",
        params={"token": admin_token},
    )

    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    assert order.payment_link_expires_at is not None
    assert 71.5 < _hours_until(order.payment_link_expires_at) <= 72.0


def test_create_invoice_passes_config_due_hours_to_paypal(
    test_client, test_db, admin_token, monkeypatch
):
    """create-invoice forwards the configured validity hours as due_hours."""
    test_db.add(PricingConfig(key="payment_link_validity_hours", value="72"))
    test_db.commit()
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    _create_booking(test_db, tour, order)
    fake = _mock_paypal(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/create-invoice",
        params={"token": admin_token},
    )

    assert resp.status_code == 200, resp.text
    fake.create_draft_invoice.assert_called_once()
    assert fake.create_draft_invoice.call_args.kwargs["due_hours"] == 72


def test_create_invoice_due_hours_defaults_to_48(test_client, test_db, admin_token, monkeypatch):
    """create-invoice defaults due_hours to 48 when config absent."""
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    _create_booking(test_db, tour, order)
    fake = _mock_paypal(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/create-invoice",
        params={"token": admin_token},
    )

    assert resp.status_code == 200, resp.text
    fake.create_draft_invoice.assert_called_once()
    assert fake.create_draft_invoice.call_args.kwargs["due_hours"] == 48


def _mock_notifications(monkeypatch):
    """Stub booking_admin.get_notification_service so no real email is sent."""
    fake = mock.Mock()
    monkeypatch.setattr("app.api.booking_admin.get_notification_service", lambda: fake)
    return fake


# --- Admin endpoint smoke tests (#42) ---------------------------------------
# Auth gating (validate_admin_token), happy paths, and key status transitions
# for the booking_admin endpoints that had no coverage.


def test_admin_endpoint_requires_valid_token(test_client, test_db):
    """No/invalid admin token is rejected (401) before any work."""
    resp = test_client.get("/api/v1/admin/bookings/orders", params={"token": "not-a-token"})
    assert resp.status_code == 401


def test_list_orders_returns_orders(test_client, test_db, admin_token):
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    _create_booking(test_db, tour, order)

    resp = test_client.get("/api/v1/admin/bookings/orders", params={"token": admin_token})
    assert resp.status_code == 200, resp.text
    refs = [o["reference"] for o in resp.json()]
    assert order.reference in refs


def test_get_order_detail_and_404(test_client, test_db, admin_token):
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    _create_booking(test_db, tour, order)

    ok = test_client.get(
        f"/api/v1/admin/bookings/orders/{order.reference}", params={"token": admin_token}
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["reference"] == order.reference
    assert len(body["bookings"]) == 1

    missing = test_client.get(
        "/api/v1/admin/bookings/orders/ORD-NOPE", params={"token": admin_token}
    )
    assert missing.status_code == 404


def test_mark_payment_sent_transitions_pending(test_client, test_db, admin_token):
    order = _create_order(test_db)  # PENDING

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/mark-payment-sent",
        params={"token": admin_token},
        json={"notes": "Cuanto link sent by hand"},
    )
    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    assert order.status == OrderStatus.PAYMENT_LINK_SENT.value
    assert order.payment_link_sent_at is not None


def test_mark_paid_marks_order_and_bookings(test_client, test_db, admin_token, monkeypatch):
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    booking = _create_booking(test_db, tour, order)
    _mock_notifications(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/mark-paid",
        params={"token": admin_token},
        json={"notes": "wire received"},
    )
    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    test_db.refresh(booking)
    assert order.status == OrderStatus.PAID.value
    assert booking.status == BookingStatus.PAID.value


def test_update_order_notes(test_client, test_db, admin_token):
    order = _create_order(test_db)

    resp = test_client.patch(
        f"/api/v1/admin/bookings/orders/{order.reference}/notes",
        params={"token": admin_token},
        json={"notes": "VIP customer"},
    )
    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    assert order.admin_notes == "VIP customer"


def test_get_booking_stats(test_client, test_db, admin_token):
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    _create_booking(test_db, tour, order)

    resp = test_client.get("/api/v1/admin/bookings/stats", params={"token": admin_token})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "status_counts" in body
    assert "upcoming_bookings" in body
    assert "total_revenue" in body


def test_get_calendar_bookings(test_client, test_db, admin_token):
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    booking = _create_booking(test_db, tour, order)

    resp = test_client.get(
        "/api/v1/admin/bookings/calendar",
        params={"token": admin_token, "start_date": "2099-01-01", "end_date": "2099-12-31"},
    )
    assert resp.status_code == 200, resp.text
    refs = [b["booking_reference"] for b in resp.json()]
    assert booking.reference in refs


def test_cancel_invoice_clears_invoice_and_resets_status(
    test_client, test_db, admin_token, monkeypatch
):
    order = _create_order(test_db, paypal_invoice_id="INV-CANCEL-1")
    order.status = OrderStatus.PAYMENT_LINK_SENT.value
    test_db.commit()
    fake = _mock_paypal(monkeypatch)
    fake.cancel_invoice.return_value = True

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/cancel-invoice",
        params={"token": admin_token},
    )
    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    assert order.paypal_invoice_id is None
    assert order.status == OrderStatus.PENDING.value


def test_get_invoice_status(test_client, test_db, admin_token, monkeypatch):
    order = _create_order(test_db, paypal_invoice_id="INV-STATUS-1")
    fake = _mock_paypal(monkeypatch)
    fake.get_invoice_status.return_value = "SENT"

    resp = test_client.get(
        f"/api/v1/admin/bookings/orders/{order.reference}/invoice-status",
        params={"token": admin_token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SENT"


def test_admin_cancel_order(test_client, test_db, admin_token, monkeypatch):
    tour = _create_tour(test_db)
    order = _create_order(test_db)
    booking = _create_booking(test_db, tour, order)
    _mock_notifications(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/admin-cancel",
        params={"token": admin_token},
        json={"reason": "customer no-show"},
    )
    assert resp.status_code == 200, resp.text
    test_db.refresh(order)
    test_db.refresh(booking)
    assert order.status == OrderStatus.CANCELLED.value
    assert booking.status == BookingStatus.CANCELLED.value


def test_reschedule_booking_to_free_slot(test_client, test_db, admin_token, monkeypatch):
    tour = _create_tour(test_db)
    order = _create_order(test_db)  # PAYPAL but no invoice -> no PayPal calls
    booking = _create_booking(test_db, tour, order)
    _mock_notifications(monkeypatch)

    resp = test_client.post(
        f"/api/v1/admin/bookings/{booking.reference}/reschedule",
        params={"token": admin_token},
        json={
            "new_date": "2099-02-01",
            "new_time_slot": "PM",
            "reason": "customer request",
            "notify_customer": False,
        },
    )
    assert resp.status_code == 200, resp.text
    test_db.refresh(booking)
    assert booking.tour_date == date(2099, 2, 1)
    assert booking.time_slot == "PM"


# ---------------------------------------------------------------------------
# #41: PayPal/DB transactional boundaries. A commit that fails *after* a PayPal
# mutation must not silently persist partial state — it should roll back and
# capture the orphan to Sentry so the drift can be reconciled by hand.
# ---------------------------------------------------------------------------


def test_send_invoice_commit_failure_rolls_back_and_captures_orphan(
    test_client, test_db, admin_token, monkeypatch
):
    """PayPal sent the email but the DB commit fails -> 500, no partial state, orphan logged."""
    order = _create_order(test_db, paypal_invoice_id="INV-SEND-FAIL")
    _mock_paypal(monkeypatch)

    capture = mock.Mock()
    monkeypatch.setattr("app.api.booking_admin.sentry_sdk.capture_exception", capture)

    def _boom():
        raise SQLAlchemyError("commit failed")

    monkeypatch.setattr(test_db, "commit", _boom)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/send-invoice",
        params={"token": admin_token},
    )

    assert resp.status_code == 500, resp.text
    assert "reconciliation" in resp.json()["detail"].lower()
    capture.assert_called_once()
    # Partial state was rolled back: the order is still PENDING, not PAYMENT_LINK_SENT.
    test_db.refresh(order)
    assert order.status == OrderStatus.PENDING.value
    assert order.paypal_invoice_url is None


def test_create_invoice_commit_failure_rolls_back_and_captures_orphan(
    test_client, test_db, admin_token, monkeypatch
):
    """PayPal created a draft invoice but the DB commit fails -> 500, pointer not persisted."""
    order = _create_order(test_db)  # no invoice id yet
    _mock_paypal(monkeypatch)

    capture = mock.Mock()
    monkeypatch.setattr("app.api.booking_admin.sentry_sdk.capture_exception", capture)

    def _boom():
        raise SQLAlchemyError("commit failed")

    monkeypatch.setattr(test_db, "commit", _boom)

    resp = test_client.post(
        f"/api/v1/admin/bookings/orders/{order.reference}/create-invoice",
        params={"token": admin_token},
    )

    assert resp.status_code == 500, resp.text
    capture.assert_called_once()
    test_db.refresh(order)
    assert order.paypal_invoice_id is None


def test_reschedule_paypal_reissue_failure_rolls_back_booking_date(
    test_client, test_db, admin_token, monkeypatch
):
    """If the PayPal re-issue fails mid-sequence, the booking date must NOT change (#41).

    Previously the except merely logged and fell through to db.commit(), persisting the
    new date against a cancelled/missing invoice. Now the whole reschedule rolls back.
    """
    tour = _create_tour(test_db)
    order = _create_order(test_db, paypal_invoice_id="INV-RS-OLD")
    order.status = OrderStatus.PAYMENT_LINK_SENT.value
    test_db.commit()
    booking = _create_booking(test_db, tour, order)
    _mock_notifications(monkeypatch)

    fake = _mock_paypal(monkeypatch)
    fake.cancel_invoice.return_value = True
    # Worst case: old invoice cancelled, then create blows up with no replacement.
    fake.create_draft_invoice.side_effect = RuntimeError("PayPal create failed")

    capture = mock.Mock()
    monkeypatch.setattr("app.api.booking_admin.sentry_sdk.capture_exception", capture)

    resp = test_client.post(
        f"/api/v1/admin/bookings/{booking.reference}/reschedule",
        params={"token": admin_token},
        json={
            "new_date": "2099-03-15",
            "new_time_slot": "PM",
            "reason": "customer request",
            "notify_customer": False,
        },
    )

    assert resp.status_code == 500, resp.text
    capture.assert_called_once()
    # Booking date unchanged and invoice pointer intact — nothing committed.
    test_db.refresh(booking)
    test_db.refresh(order)
    assert booking.tour_date == FUTURE_DATE
    assert booking.time_slot == "AM"
    assert order.paypal_invoice_id == "INV-RS-OLD"
    assert order.status == OrderStatus.PAYMENT_LINK_SENT.value
