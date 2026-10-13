"""
Tests for NotificationService dispatch behavior under email-delivery failure.

These are regression tests for the contract flip in PR fix/composio-cluster-b:
methods used to return bool (often discarded by callers — issues #67, #70);
they now return None and propagate EmailDeliveryError on customer-side
failures, while catching+logging admin-side failures.

`_send_email` (notifications.py's local name for `send_email`) is patched
directly. No DB, no SMTP, no templates beyond what notifications.py renders
inline — SimpleNamespace stand-ins for Order/Booking/Tour suffice (matches
the convention in test_notifications.py).
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.services.email_sender import EmailDeliveryError
from app.services.notifications import NotificationService


def make_tour() -> SimpleNamespace:
    return SimpleNamespace(name="Casco Viejo Walking Tour", name_es="Tour por Casco Viejo")


def make_booking() -> SimpleNamespace:
    return SimpleNamespace(
        tour=make_tour(),
        tour_date=date(2026, 6, 15),
        time_slot="AM",
        preferred_time=None,
        total_participants=4,
        grand_total=Decimal("120.00"),
    )


def make_order() -> SimpleNamespace:
    return SimpleNamespace(
        reference="ORD-AB12CD",
        customer_name="María García",
        customer_email="maria@example.com",
        customer_phone="+507 6000-1234",
        customer_hotel="Hotel Central",
        customer_language="en",
        payment_method="YAPPY",
        grand_total=Decimal("240.00"),
    )


class TestNotifyNewOrder:
    def test_customer_failure_propagates(self):
        """Stranded customer must surface — caller decides how to handle."""
        order, bookings = make_order(), [make_booking()]
        # First call (customer) fails; second call (admin) never reached.
        with patch(
            "app.services.notifications._send_email",
            side_effect=EmailDeliveryError("customer relay rejected"),
        ) as mock_send:
            with pytest.raises(EmailDeliveryError, match="customer relay rejected"):
                NotificationService().notify_new_order(
                    order=order, bookings=bookings, magic_link_token="tok"
                )
            assert mock_send.call_count == 1  # admin never called

    def test_admin_failure_is_caught_and_logged(self, caplog):
        """Customer was notified; admin failure must not strand the customer."""
        order, bookings = make_order(), [make_booking()]
        call_count = {"n": 0}

        def fake_send(to, subject, body):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return  # customer succeeds
            raise EmailDeliveryError("admin relay rejected")

        with patch("app.services.notifications._send_email", side_effect=fake_send):
            with caplog.at_level("ERROR"):
                # No exception expected.
                NotificationService().notify_new_order(
                    order=order, bookings=bookings, magic_link_token="tok"
                )
            assert call_count["n"] == 2
            assert any(
                "Admin notification failed for order ORD-AB12CD" in r.message
                for r in caplog.records
            )

    def test_happy_path_calls_both_recipients(self):
        order, bookings = make_order(), [make_booking()]
        with patch("app.services.notifications._send_email") as mock_send:
            NotificationService().notify_new_order(
                order=order, bookings=bookings, magic_link_token="tok"
            )
            assert mock_send.call_count == 2
            customer_call, admin_call = mock_send.call_args_list
            assert customer_call.args[0] == "maria@example.com"
            # admin recipient comes from settings.admin_notification_email
            assert admin_call.args[0] == "diego@panamaincontext.com"


class TestNotifyPaymentReceived:
    def test_customer_failure_propagates(self):
        order, bookings = make_order(), [make_booking()]
        with patch(
            "app.services.notifications._send_email",
            side_effect=EmailDeliveryError("customer relay rejected"),
        ) as mock_send:
            with pytest.raises(EmailDeliveryError):
                NotificationService().notify_payment_received(order=order, bookings=bookings)
            assert mock_send.call_count == 1

    def test_admin_failure_is_caught_and_logged(self, caplog):
        """Regression: previously the admin return was discarded entirely (#70)."""
        order, bookings = make_order(), [make_booking()]
        call_count = {"n": 0}

        def fake_send(to, subject, body):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return
            raise EmailDeliveryError("admin relay rejected")

        with patch("app.services.notifications._send_email", side_effect=fake_send):
            with caplog.at_level("ERROR"):
                NotificationService().notify_payment_received(order=order, bookings=bookings)
            assert call_count["n"] == 2
            assert any(
                "Admin payment notification failed for order ORD-AB12CD" in r.message
                for r in caplog.records
            )
