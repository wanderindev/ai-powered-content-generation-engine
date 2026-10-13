"""Tests for order API endpoints."""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest import mock

import pytest
from app.models import (
    Attraction,
    Booking,
    BookingStatus,
    BookingStatusLog,
    MagicLink,
    MagicLinkAction,
    Order,
    OrderStatus,
    PricingConfig,
    Tour,
    TourAttraction,
    Zone,
)
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def pricing_config(test_db):
    """Seed pricing configuration."""
    configs = [
        PricingConfig(key="base_fee", value="10.00"),
        PricingConfig(key="hourly_rate_pax_1", value="10.00"),
        PricingConfig(key="hourly_rate_pax_2_3", value="6.00"),
        PricingConfig(key="hourly_rate_pax_4_5", value="4.00"),
        PricingConfig(key="hourly_rate_pax_6_7", value="2.00"),
        PricingConfig(key="senior_discount_percent", value="20"),
        PricingConfig(key="reservation_validity_days", value="7"),
    ]
    for config in configs:
        test_db.add(config)
    test_db.commit()


@pytest.fixture
def zone(test_db):
    """Create a test zone."""
    zone = Zone(
        id=1,
        name="Test Zone",
        name_es="Zona de Prueba",
        display_order=1,
        is_active=True,
    )
    test_db.add(zone)
    test_db.commit()
    return zone


@pytest.fixture
def attraction(test_db):
    """Create a test attraction."""
    attraction = Attraction(
        id=1,
        name="Test Attraction",
        name_es="Atracción de Prueba",
        ticket_adult_tourist=Decimal("20.00"),
        ticket_adult_resident=Decimal("5.00"),
        is_active=True,
    )
    test_db.add(attraction)
    test_db.commit()
    return attraction


@pytest.fixture
def tour(test_db, zone, attraction):
    """Create a test tour."""
    tour = Tour(
        id=1,
        slug="test-tour",
        name="Test Tour",
        name_es="Tour de Prueba",
        duration_hours=Decimal("3.0"),
        max_group_size=7,
        zone_id=zone.id,
        is_active=True,
    )
    test_db.add(tour)
    test_db.flush()

    tour_attraction = TourAttraction(
        tour_id=tour.id,
        attraction_id=attraction.id,
        is_optional=False,
        visit_order=1,
    )
    test_db.add(tour_attraction)
    test_db.commit()
    return tour


@pytest.fixture
def fixed_price_tour(test_db, zone):
    """Create a fixed-price (open-group) tour, which allows many bookings/slot."""
    tour = Tour(
        id=2,
        slug="fixed-tour",
        name="Fixed Tour",
        name_es="Tour Fijo",
        duration_hours=Decimal("2.0"),
        max_group_size=30,
        zone_id=zone.id,
        is_active=True,
        fixed_price_config={
            "adult": 20.00,
            "child_5_10": 12.50,
            "child_under_5": 0,
            "senior_discount_pct": 20,
        },
    )
    test_db.add(tour)
    test_db.commit()
    return tour


def _hdr(token):
    """Authorization header for a customer order token (#263: header-only auth)."""
    return {"Authorization": f"Bearer {token}"}


def _order_payload(
    tour_slug, tour_date, time_slot="AM", participants=None, email="john@example.com"
):
    """Build a minimal create-order payload for one booking."""
    return {
        "customer": {
            "name": "John Doe",
            "email": email,
            "phone": "+1234567890",
            "language": "en",
        },
        "payment_method": "PAYPAL",
        "bookings": [
            {
                "tour_slug": tour_slug,
                "tour_date": tour_date,
                "time_slot": time_slot,
                "participants": participants or {"adult": {"resident": 0, "tourist": 2}},
            }
        ],
    }


class TestCalculatePrice:
    """Tests for POST /api/v1/bookings/calculate-price endpoint."""

    def test_calculate_price(self, test_client, pricing_config, tour):
        """Test price calculation."""
        response = test_client.post(
            "/api/v1/bookings/calculate-price",
            json={
                "tour_slug": "test-tour",
                "participants": {
                    "adult": {"resident": 0, "tourist": 2},
                },
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Service: $10 + 3 * ($10 + $6) = $58
        assert Decimal(data["service_total"]) == Decimal("58.00")
        # Tickets: 2 * $20 = $40
        assert Decimal(data["tickets_total"]) == Decimal("40.00")
        # No transport
        assert Decimal(data["transport_total"]) == Decimal("0")
        # Total: $98
        assert Decimal(data["grand_total"]) == Decimal("98.00")

    def test_calculate_price_tour_not_found(self, test_client, pricing_config):
        """Test price calculation for non-existent tour."""
        response = test_client.post(
            "/api/v1/bookings/calculate-price",
            json={
                "tour_slug": "non-existent",
                "participants": {
                    "adult": {"resident": 0, "tourist": 1},
                },
            },
        )
        assert response.status_code == 404


class TestCheckAvailability:
    """Tests for POST /api/v1/bookings/check-availability endpoint."""

    def test_check_availability(self, test_client, tour):
        """Test availability check for available slot."""
        future_date = (date.today() + timedelta(days=7)).isoformat()

        response = test_client.post(
            "/api/v1/bookings/check-availability",
            json={
                "tour_slug": "test-tour",
                "tour_date": future_date,
                "time_slot": "AM",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True

    def test_check_availability_past_date(self, test_client, tour):
        """Test availability check for past date."""
        past_date = (date.today() - timedelta(days=1)).isoformat()

        response = test_client.post(
            "/api/v1/bookings/check-availability",
            json={
                "tour_slug": "test-tour",
                "tour_date": past_date,
                "time_slot": "AM",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert "past" in data["reason"].lower()


class TestCreateOrder:
    """Tests for POST /api/v1/orders endpoint."""

    def test_create_order(self, test_client, pricing_config, tour):
        """Test creating a new order."""
        future_date = (date.today() + timedelta(days=7)).isoformat()

        response = test_client.post(
            "/api/v1/orders",
            json={
                "customer": {
                    "name": "John Doe",
                    "email": "john@example.com",
                    "phone": "+1234567890",
                    "language": "en",
                },
                "payment_method": "PAYPAL",
                "bookings": [
                    {
                        "tour_slug": "test-tour",
                        "tour_date": future_date,
                        "time_slot": "AM",
                        "preferred_time": "9:00 AM",
                        "participants": {
                            "adult": {"resident": 0, "tourist": 2},
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["reference"].startswith("ORD-")
        assert data["status"] == "PENDING"
        assert data["customer_name"] == "John Doe"
        assert data["customer_email"] == "john@example.com"
        assert len(data["bookings"]) == 1
        assert data["bookings"][0]["reference"].startswith("PIC-")
        assert data["bookings"][0]["tour_name"] == "Test Tour"
        assert data["bookings"][0]["time_slot"] == "AM"
        # #36: create_order returns the VIEW_ORDER magic-link token so the
        # confirmation page can fetch the order without it touching the URL.
        assert data["magic_link_token"]
        # #39: no senior here, so subtotal == grand_total but is still present.
        assert data["subtotal"] == data["grand_total"]

    def test_create_order_invalid_tour(self, test_client, pricing_config):
        """Test creating order with invalid tour."""
        future_date = (date.today() + timedelta(days=7)).isoformat()

        response = test_client.post(
            "/api/v1/orders",
            json={
                "customer": {
                    "name": "John Doe",
                    "email": "john@example.com",
                    "phone": "+1234567890",
                },
                "payment_method": "PAYPAL",
                "bookings": [
                    {
                        "tour_slug": "non-existent",
                        "tour_date": future_date,
                        "time_slot": "AM",
                        "participants": {
                            "adult": {"resident": 0, "tourist": 1},
                        },
                    }
                ],
            },
        )
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_create_order_group_too_large(self, test_client, pricing_config, tour):
        """Test creating order with group > 7."""
        future_date = (date.today() + timedelta(days=7)).isoformat()

        response = test_client.post(
            "/api/v1/orders",
            json={
                "customer": {
                    "name": "John Doe",
                    "email": "john@example.com",
                    "phone": "+1234567890",
                },
                "payment_method": "PAYPAL",
                "bookings": [
                    {
                        "tour_slug": "test-tour",
                        "tour_date": future_date,
                        "time_slot": "AM",
                        "participants": {
                            "adult": {"resident": 0, "tourist": 10},
                        },
                    }
                ],
            },
        )
        assert response.status_code == 400
        assert "quote" in response.json()["detail"].lower()


class TestOrderSubtotalDiscount:
    """#39: Order.subtotal carries the pre-discount total, distinct from grand_total."""

    def test_subtotal_excludes_discount_grand_total_nets_it(
        self, test_client, pricing_config, tour
    ):
        """With a senior present, subtotal > grand_total and the identity holds."""
        future_date = (date.today() + timedelta(days=7)).isoformat()
        # One senior + one adult tourist -> a non-zero senior discount.
        payload = _order_payload(
            "test-tour",
            future_date,
            participants={
                "senior_female": {"resident": 0, "tourist": 1},
                "adult": {"resident": 0, "tourist": 1},
            },
        )
        response = test_client.post("/api/v1/orders", json=payload)
        assert response.status_code == 200
        data = response.json()

        subtotal = Decimal(data["subtotal"])
        grand_total = Decimal(data["grand_total"])
        booking = data["bookings"][0]

        # subtotal is the pre-discount sum of the booking components.
        expected_subtotal = (
            Decimal(booking["service_total"])
            + Decimal(booking["tickets_total"])
            + Decimal(booking["transport_total"])
        )
        assert subtotal == expected_subtotal
        # grand_total nets the senior discount; the discount is non-zero here.
        assert grand_total == subtotal - Decimal(booking["senior_discount"])
        assert Decimal(booking["senior_discount"]) > 0
        assert subtotal > grand_total

    def test_subtotal_sums_across_multiple_bookings(self, test_client, pricing_config, tour):
        """Multi-booking order: subtotal and grand_total aggregate every booking."""
        d1 = (date.today() + timedelta(days=7)).isoformat()
        d2 = (date.today() + timedelta(days=8)).isoformat()
        payload = _order_payload(
            "test-tour", d1, participants={"adult": {"resident": 0, "tourist": 2}}
        )
        payload["bookings"].append(
            {
                "tour_slug": "test-tour",
                "tour_date": d2,
                "time_slot": "PM",
                "participants": {"senior_male": {"resident": 0, "tourist": 1}},
            }
        )
        response = test_client.post("/api/v1/orders", json=payload)
        assert response.status_code == 200
        data = response.json()

        bookings = data["bookings"]
        expected_subtotal = sum(
            Decimal(b["service_total"])
            + Decimal(b["tickets_total"])
            + Decimal(b["transport_total"])
            for b in bookings
        )
        expected_discount = sum(Decimal(b["senior_discount"]) for b in bookings)
        assert Decimal(data["subtotal"]) == expected_subtotal
        assert Decimal(data["grand_total"]) == expected_subtotal - expected_discount


class TestSlotConcurrency:
    """#40: one active booking per slot for private tours; open-group tours exempt."""

    def test_sequential_duplicate_blocked_by_availability(self, test_client, pricing_config, tour):
        """The normal (non-racing) path: the second request is caught at the check (400)."""
        future_date = (date.today() + timedelta(days=7)).isoformat()
        payload = _order_payload("test-tour", future_date)

        assert test_client.post("/api/v1/orders", json=payload).status_code == 200
        second = test_client.post("/api/v1/orders", json=payload)
        assert second.status_code == 400
        assert "not available" in second.json()["detail"].lower()

    def test_race_past_check_returns_409(self, test_client, pricing_config, tour, monkeypatch):
        """Simulate the TOCTOU race: the check passes but the unique index trips -> 409."""
        future_date = (date.today() + timedelta(days=7)).isoformat()
        payload = _order_payload("test-tour", future_date)

        # First order really holds the slot.
        assert test_client.post("/api/v1/orders", json=payload).status_code == 200

        # Force the availability check to pass so the second insert reaches the
        # partial unique index (the real-world race window).
        monkeypatch.setattr(
            "app.services.availability.AvailabilityService.can_book_slot",
            lambda self, tour_date, time_slot: (True, None),
        )
        second = test_client.post("/api/v1/orders", json=payload)
        assert second.status_code == 409
        assert "just booked" in second.json()["detail"].lower()

    def test_slot_freed_after_cancel(self, test_client, pricing_config, tour):
        """Cancelling an order releases its slot for a new booking."""
        future_date = (date.today() + timedelta(days=7)).isoformat()
        payload = _order_payload("test-tour", future_date)

        first = test_client.post("/api/v1/orders", json=payload).json()
        token = first["magic_link_token"]
        cancel = test_client.post(
            f"/api/v1/orders/{first['reference']}/cancel",
            headers=_hdr(token),
            json={"reason": "changed plans"},
        )
        assert cancel.status_code == 200

        # Slot is free again (CANCELLED is outside the active set).
        assert test_client.post("/api/v1/orders", json=payload).status_code == 200

    def test_fixed_price_allows_multiple_per_slot(
        self, test_client, pricing_config, fixed_price_tour
    ):
        """Open-group tours can take many bookings for the same slot."""
        future_date = (date.today() + timedelta(days=7)).isoformat()
        payload = _order_payload(
            "fixed-tour", future_date, participants={"adult": {"resident": 0, "tourist": 3}}
        )
        assert test_client.post("/api/v1/orders", json=payload).status_code == 200
        assert test_client.post("/api/v1/orders", json=payload).status_code == 200

    def test_db_unique_index_rejects_duplicate_active_slot(self, test_db, tour):
        """Direct DB proof: a second active private-tour booking on a slot is rejected."""
        common = dict(
            order_id=None,
            tour_id=tour.id,
            tour_date=date.today() + timedelta(days=7),
            time_slot="AM",
            status=BookingStatus.PENDING_PAYMENT.value,
            participants={"adult": {"resident": 0, "tourist": 1}},
            total_participants=1,
            service_total=Decimal("10"),
            grand_total=Decimal("10"),
        )
        # A parent order is required (order_id is NOT NULL).
        order = Order(
            reference="ORD-SLOT01",
            customer_name="A",
            customer_email="a@example.com",
            customer_phone="+1",
            customer_language="en",
            status=OrderStatus.PENDING.value,
            payment_method="PAYPAL",
            subtotal=Decimal("10"),
            grand_total=Decimal("10"),
        )
        test_db.add(order)
        test_db.flush()
        common["order_id"] = order.id

        test_db.add(Booking(reference="PIC-SLOT01", **common))
        test_db.add(Booking(reference="PIC-SLOT02", **common))
        with pytest.raises(IntegrityError):
            test_db.commit()


class TestGetOrderToken:
    """#36: the create token round-trips through GET; GET never leaks a token."""

    def test_create_token_fetches_order_and_get_omits_token(
        self, test_client, pricing_config, tour
    ):
        future_date = (date.today() + timedelta(days=7)).isoformat()
        created = test_client.post(
            "/api/v1/orders", json=_order_payload("test-tour", future_date)
        ).json()
        token = created["magic_link_token"]
        assert token

        ok = test_client.get(f"/api/v1/orders/{created['reference']}", headers=_hdr(token))
        assert ok.status_code == 200
        body = ok.json()
        assert body["reference"] == created["reference"]
        # GET must not echo a token (it is only minted on create).
        assert body["magic_link_token"] is None

    def test_get_with_bad_token_rejected(self, test_client, pricing_config, tour):
        future_date = (date.today() + timedelta(days=7)).isoformat()
        created = test_client.post(
            "/api/v1/orders", json=_order_payload("test-tour", future_date)
        ).json()
        bad = test_client.get(f"/api/v1/orders/{created['reference']}", headers=_hdr("nope"))
        assert bad.status_code == 401


class TestOrderTokenTransport:
    """#43/#263: the order token is read **only** from the Authorization: Bearer
    header. The transitional ``?token=`` query fallback has been removed."""

    def _create(self, test_client):
        future_date = (date.today() + timedelta(days=7)).isoformat()
        return test_client.post(
            "/api/v1/orders", json=_order_payload("test-tour", future_date)
        ).json()

    def test_bearer_header_authenticates(self, test_client, pricing_config, tour):
        created = self._create(test_client)
        resp = test_client.get(
            f"/api/v1/orders/{created['reference']}",
            headers={"Authorization": f"Bearer {created['magic_link_token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["reference"] == created["reference"]

    def test_query_token_rejected(self, test_client, pricing_config, tour):
        # The ?token= query fallback was removed (#263) — a token supplied only
        # via the query string no longer authenticates.
        created = self._create(test_client)
        resp = test_client.get(
            f"/api/v1/orders/{created['reference']}",
            params={"token": created["magic_link_token"]},
        )
        assert resp.status_code == 401

    def test_query_token_ignored_when_header_present(self, test_client, pricing_config, tour):
        # The query string is no longer read — a valid header authenticates even
        # with a garbage token in the (ignored) query.
        created = self._create(test_client)
        resp = test_client.get(
            f"/api/v1/orders/{created['reference']}?token=bogus",
            headers={"Authorization": f"Bearer {created['magic_link_token']}"},
        )
        assert resp.status_code == 200

    def test_no_credential_rejected(self, test_client, pricing_config, tour):
        created = self._create(test_client)
        assert test_client.get(f"/api/v1/orders/{created['reference']}").status_code == 401


class TestRenewPaymentLink:
    """Tests for POST /api/v1/orders/{reference}/renew-payment endpoint."""

    def _make_paypal_order(self, test_db):
        """Create a PENDING PayPal order with an existing invoice + renewal link."""
        order = Order(
            reference="ORD-RENEW1",
            customer_name="Jane Doe",
            customer_email="jane@example.com",
            customer_phone="+1234567890",
            customer_language="en",
            status=OrderStatus.PENDING.value,
            payment_method="PAYPAL",
            paypal_invoice_id="INV2-OLD-0001",
            paypal_invoice_url="https://paypal.example/INV2-OLD-0001",
            reservation_expires_at=datetime.now(UTC) + timedelta(days=3),
            subtotal=Decimal("100.00"),
            grand_total=Decimal("100.00"),
        )
        test_db.add(order)
        test_db.flush()

        link = MagicLink(
            token="renew-token-0001",
            order_id=order.id,
            action=MagicLinkAction.RENEW_PAYMENT.value,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        test_db.add(link)
        test_db.commit()
        return order, link

    def test_renew_succeeds_when_cancel_invoice_fails(
        self, test_client, test_db, caplog, monkeypatch
    ):
        """Cancel-invoice failure is logged + captured but does not block renewal.

        Best-effort cleanup: even if cancelling the old PayPal invoice raises,
        the renewal must still succeed (200), clear the invoice fields, and set
        the order back to PENDING. The swallowed failure must now be observable.
        """
        order, _ = self._make_paypal_order(test_db)

        # Mock PayPal so no real API call is made; cancel_invoice raises.
        mock_service = mock.Mock()
        mock_service.cancel_invoice.side_effect = RuntimeError("PayPal unavailable")

        captured = {}

        def fake_capture_exception(exc):
            captured["exc"] = exc

        monkeypatch.setattr("app.services.paypal.get_paypal_service", lambda: mock_service)
        monkeypatch.setattr("sentry_sdk.capture_exception", fake_capture_exception)
        # Isolate this test to the PayPal-cancel path: stub the admin renewal
        # notification so its email send doesn't also capture_exception (#38).
        monkeypatch.setattr("app.api.orders.get_notification_service", lambda: mock.Mock())

        with caplog.at_level(logging.WARNING):
            response = test_client.post(
                f"/api/v1/orders/{order.reference}/renew-payment",
                headers=_hdr("renew-token-0001"),
            )

        # Renewal still succeeds (best-effort cleanup, no re-raise).
        assert response.status_code == 200
        assert response.json()["reference"] == "ORD-RENEW1"

        # Invoice fields cleared and status reset to PENDING.
        test_db.refresh(order)
        assert order.paypal_invoice_id is None
        assert order.paypal_invoice_url is None
        assert order.status == OrderStatus.PENDING.value

        # The cancel was attempted exactly once with the old invoice id.
        mock_service.cancel_invoice.assert_called_once_with("INV2-OLD-0001")

        # Failure is observable: logged at WARNING and captured by Sentry.
        assert any(
            "Failed to cancel PayPal invoice" in rec.message and rec.levelname == "WARNING"
            for rec in caplog.records
        )
        assert isinstance(captured.get("exc"), RuntimeError)

    def _make_cuanto_order(self, test_db, reference="ORD-RENEW2"):
        """Create a PENDING Cuanto order with a usable magic link (no PayPal)."""
        order = Order(
            reference=reference,
            customer_name="Sam Doe",
            customer_email="sam@example.com",
            customer_phone="+1234567890",
            customer_language="en",
            status=OrderStatus.PENDING.value,
            payment_method="CUANTO",
            reservation_expires_at=datetime.now(UTC) + timedelta(days=3),
            subtotal=Decimal("50.00"),
            grand_total=Decimal("50.00"),
        )
        test_db.add(order)
        test_db.flush()
        link = MagicLink(
            token=f"renew-{reference}",
            order_id=order.id,
            action=MagicLinkAction.VIEW_ORDER.value,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        test_db.add(link)
        test_db.commit()
        return order, link

    def test_renewal_notifies_admin_and_keeps_token_valid(self, test_client, test_db, monkeypatch):
        """#38: a fresh request notifies admin, logs it, and does NOT consume the token."""
        order, link = self._make_cuanto_order(test_db)

        notifier = mock.Mock()
        monkeypatch.setattr("app.api.orders.get_notification_service", lambda: notifier)

        resp = test_client.post(
            f"/api/v1/orders/{order.reference}/renew-payment",
            headers=_hdr(link.token),
        )
        assert resp.status_code == 200
        # Honest copy: no false "within 24 hours" promise, no claim of an auto link.
        assert "notified our team" in resp.json()["message"].lower()

        # Admin was notified exactly once for the order.
        notifier.notify_admin_renewal_request.assert_called_once()
        assert notifier.notify_admin_renewal_request.call_args.args[0].reference == order.reference

        # A renewal-request log row was written.
        from app.api.orders import _RENEWAL_REQUEST_NOTE

        logs = test_db.query(BookingStatusLog).filter(BookingStatusLog.order_id == order.id).all()
        assert any(log_row.notes == _RENEWAL_REQUEST_NOTE for log_row in logs)

        # Token is still valid -> the customer keeps access to the manage page (#36).
        ok = test_client.get(f"/api/v1/orders/{order.reference}", headers=_hdr(link.token))
        assert ok.status_code == 200

    def test_duplicate_request_does_not_renotify(self, test_client, test_db, monkeypatch):
        """#38: repeated clicks while a request is pending don't re-spam the admin."""
        order, link = self._make_cuanto_order(test_db)

        notifier = mock.Mock()
        monkeypatch.setattr("app.api.orders.get_notification_service", lambda: notifier)

        first = test_client.post(
            f"/api/v1/orders/{order.reference}/renew-payment", headers=_hdr(link.token)
        )
        second = test_client.post(
            f"/api/v1/orders/{order.reference}/renew-payment", headers=_hdr(link.token)
        )
        assert first.status_code == 200
        assert second.status_code == 200

        # Only the first request notified Diego; the duplicate was idempotent.
        notifier.notify_admin_renewal_request.assert_called_once()
        # And it did not write a second renewal log.
        from app.api.orders import _RENEWAL_REQUEST_NOTE

        renewal_logs = (
            test_db.query(BookingStatusLog)
            .filter(
                BookingStatusLog.order_id == order.id,
                BookingStatusLog.notes == _RENEWAL_REQUEST_NOTE,
            )
            .count()
        )
        assert renewal_logs == 1
