"""
Tests for notification email template rendering.

These are regression tests for issue #31 (HTML injection via attacker-controlled
fields from POST /api/v1/orders). They render each template directly via the
service's Jinja2 Environment with HTML-bearing payloads in every
attacker-controllable field, and assert that:

1. The escaped form is present in the rendered output.
2. The raw HTML payload is NOT present.
3. Legitimate values still render correctly (happy-path sanity check).

No DB is needed — templates only touch attribute access on the passed objects,
so we use SimpleNamespace stand-ins for Order/Booking/Tour. This keeps the file
independent of the Postgres testcontainer used by the rest of the suite.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.services.notifications import _env

# Payloads we expect to be escaped wherever an attacker-controlled value is rendered.
SCRIPT_PAYLOAD = '<script>alert(1)</script>'
SCRIPT_ESCAPED = '&lt;script&gt;alert(1)&lt;/script&gt;'
LINK_PAYLOAD = '<a href="https://evil.example/">click</a>'
LINK_ESCAPED_OPEN = '&lt;a href=&#34;https://evil.example/&#34;&gt;'

ATTACK_NAME = f'Mallory {SCRIPT_PAYLOAD}'
ATTACK_EMAIL = 'attacker+<svg/onload=1>@evil.example'
ATTACK_PHONE = '555 ' + LINK_PAYLOAD
ATTACK_HOTEL = LINK_PAYLOAD
ATTACK_PREFERRED_TIME = LINK_PAYLOAD
ATTACK_REASON = LINK_PAYLOAD
ATTACK_TOUR_NAME = SCRIPT_PAYLOAD


def make_tour(name: str = "Casco Viejo Walking Tour", name_es: str = "Tour por Casco Viejo") -> SimpleNamespace:
    return SimpleNamespace(name=name, name_es=name_es)


def make_booking(
    *,
    tour: SimpleNamespace | None = None,
    tour_date: date = date(2026, 6, 15),
    time_slot: str = "AM",
    preferred_time: str | None = None,
    total_participants: int = 4,
    grand_total: Decimal = Decimal("120.00"),
) -> SimpleNamespace:
    return SimpleNamespace(
        tour=tour or make_tour(),
        tour_date=tour_date,
        time_slot=time_slot,
        preferred_time=preferred_time,
        total_participants=total_participants,
        grand_total=grand_total,
    )


def make_order(
    *,
    reference: str = "ORD-AB12CD",
    customer_name: str = "María García",
    customer_email: str = "maria@example.com",
    customer_phone: str = "+507 6000-1234",
    customer_hotel: str | None = "Hotel Central",
    customer_language: str = "en",
    payment_method: str = "YAPPY",
    grand_total: Decimal = Decimal("240.00"),
) -> SimpleNamespace:
    return SimpleNamespace(
        reference=reference,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        customer_hotel=customer_hotel,
        customer_language=customer_language,
        payment_method=payment_method,
        grand_total=grand_total,
    )


def assert_no_raw_html(rendered: str, *payloads: str) -> None:
    """Assert none of the raw HTML payloads appear in the rendered output."""
    for payload in payloads:
        assert payload not in rendered, (
            f"Unescaped payload found in rendered template: {payload!r}"
        )


# ---------------------------------------------------------------------------
# Partial: booking_rows.html
# ---------------------------------------------------------------------------

class TestBookingRowsPartial:
    """The partial is included by 4 outer templates; lock its behavior down here."""

    def _render(self, **ctx) -> str:
        return _env.get_template("partials/booking_rows.html").render(**ctx)

    def test_preferred_time_is_escaped(self):
        bookings = [make_booking(preferred_time=ATTACK_PREFERRED_TIME)]
        out = self._render(bookings=bookings, language="en")
        assert LINK_ESCAPED_OPEN in out
        assert_no_raw_html(out, LINK_PAYLOAD)

    def test_malicious_tour_name_is_escaped(self):
        # tour.name is admin-controlled, but auto-escape applies regardless.
        bookings = [make_booking(tour=make_tour(name=ATTACK_TOUR_NAME))]
        out = self._render(bookings=bookings, language="en")
        assert SCRIPT_ESCAPED in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD)

    def test_happy_path_renders_expected_fields(self):
        bookings = [make_booking(preferred_time="9:00 AM", total_participants=3)]
        out = self._render(bookings=bookings, language="en")
        assert "Casco Viejo Walking Tour" in out
        assert "2026-06-15" in out
        assert "AM (9:00 AM)" in out
        assert ">3</td>" in out
        assert "$120.00" in out

    def test_spanish_language_uses_spanish_tour_name(self):
        bookings = [make_booking()]
        out = self._render(bookings=bookings, language="es")
        assert "Tour por Casco Viejo" in out
        assert "Casco Viejo Walking Tour" not in out


# ---------------------------------------------------------------------------
# new_order_customer.{en,es}.html
# ---------------------------------------------------------------------------

class TestNewOrderCustomerTemplates:
    @pytest.mark.parametrize("language", ["en", "es"])
    def test_customer_name_is_escaped(self, language):
        order = make_order(customer_name=ATTACK_NAME, customer_language=language)
        bookings = [make_booking()]
        template = _env.get_template(f"new_order_customer.{language}.html")
        out = template.render(
            order=order,
            bookings=bookings,
            language=language,
            manage_url="https://example.com/booking/manage?token=safe-token&reference=ORD-AB12CD",
        )
        assert SCRIPT_ESCAPED in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD)

    @pytest.mark.parametrize("language", ["en", "es"])
    def test_attacker_preferred_time_in_rows_is_escaped(self, language):
        order = make_order(customer_language=language)
        bookings = [make_booking(preferred_time=ATTACK_PREFERRED_TIME)]
        template = _env.get_template(f"new_order_customer.{language}.html")
        out = template.render(
            order=order,
            bookings=bookings,
            language=language,
            manage_url="https://example.com/safe",
        )
        assert LINK_ESCAPED_OPEN in out
        assert_no_raw_html(out, LINK_PAYLOAD)

    def test_happy_path_en(self):
        order = make_order(customer_language="en")
        bookings = [make_booking()]
        out = _env.get_template("new_order_customer.en.html").render(
            order=order,
            bookings=bookings,
            language="en",
            manage_url="https://example.com/booking/manage?token=t&reference=ORD-AB12CD",
        )
        assert "Thank You for Your Booking!" in out
        assert "Hello María García," in out
        assert "<strong>#ORD-AB12CD</strong>" in out
        assert "<strong>Total: $240.00</strong>" in out
        assert 'href="https://example.com/booking/manage?token=t&amp;reference=ORD-AB12CD"' in out

    def test_happy_path_es(self):
        order = make_order(customer_language="es")
        bookings = [make_booking()]
        out = _env.get_template("new_order_customer.es.html").render(
            order=order,
            bookings=bookings,
            language="es",
            manage_url="https://example.com/safe",
        )
        assert "¡Gracias por su reserva!" in out
        assert "Hola María García," in out


# ---------------------------------------------------------------------------
# new_order_admin.html
# ---------------------------------------------------------------------------

class TestNewOrderAdminTemplate:
    def test_every_attacker_controllable_field_is_escaped(self):
        order = make_order(
            customer_name=ATTACK_NAME,
            customer_email=ATTACK_EMAIL,
            customer_phone=ATTACK_PHONE,
            customer_hotel=ATTACK_HOTEL,
        )
        bookings = [make_booking(preferred_time=ATTACK_PREFERRED_TIME)]
        out = _env.get_template("new_order_admin.html").render(
            order=order, bookings=bookings, language="en"
        )
        # name carries <script>, hotel/phone/preferred_time carry <a href>
        assert SCRIPT_ESCAPED in out
        assert LINK_ESCAPED_OPEN in out
        # email payload contains <svg/onload=1> — the < and > must be escaped
        assert "&lt;svg/onload=1&gt;" in out
        assert_no_raw_html(
            out,
            SCRIPT_PAYLOAD,
            LINK_PAYLOAD,
            "<svg/onload=1>",
        )

    def test_happy_path(self):
        order = make_order()
        bookings = [make_booking()]
        out = _env.get_template("new_order_admin.html").render(
            order=order, bookings=bookings, language="en"
        )
        assert "<h2>New Order Received</h2>" in out
        assert "ORD-AB12CD" in out
        assert "María García" in out
        assert "maria@example.com" in out
        assert "Hotel Central" in out

    def test_missing_hotel_falls_back_to_na(self):
        order = make_order(customer_hotel=None)
        bookings = [make_booking()]
        out = _env.get_template("new_order_admin.html").render(
            order=order, bookings=bookings, language="en"
        )
        assert "<strong>Hotel:</strong> N/A" in out


# ---------------------------------------------------------------------------
# payment_received_customer.{en,es}.html
# ---------------------------------------------------------------------------

class TestPaymentReceivedCustomerTemplates:
    @pytest.mark.parametrize("language", ["en", "es"])
    def test_customer_name_is_escaped(self, language):
        order = make_order(customer_name=ATTACK_NAME, customer_language=language)
        bookings = [make_booking()]
        out = _env.get_template(f"payment_received_customer.{language}.html").render(
            order=order, bookings=bookings, language=language
        )
        assert SCRIPT_ESCAPED in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD)

    def test_happy_path_en(self):
        order = make_order()
        bookings = [make_booking()]
        out = _env.get_template("payment_received_customer.en.html").render(
            order=order, bookings=bookings, language="en"
        )
        assert "Payment Confirmed!" in out
        assert "Hello María García," in out
        assert "Total Paid: $240.00" in out

    def test_happy_path_es(self):
        order = make_order(customer_language="es")
        bookings = [make_booking()]
        out = _env.get_template("payment_received_customer.es.html").render(
            order=order, bookings=bookings, language="es"
        )
        assert "¡Pago Confirmado!" in out
        assert "Total Pagado: $240.00" in out


# ---------------------------------------------------------------------------
# payment_received_admin.html
# ---------------------------------------------------------------------------

class TestPaymentReceivedAdminTemplate:
    def test_customer_name_and_email_are_escaped(self):
        order = make_order(customer_name=ATTACK_NAME, customer_email=ATTACK_EMAIL)
        out = _env.get_template("payment_received_admin.html").render(order=order)
        assert SCRIPT_ESCAPED in out
        assert "&lt;svg/onload=1&gt;" in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD, "<svg/onload=1>")

    def test_happy_path(self):
        order = make_order()
        out = _env.get_template("payment_received_admin.html").render(order=order)
        assert "<h2>Payment Received</h2>" in out
        assert "ORD-AB12CD" in out
        assert "$240.00" in out
        assert "YAPPY" in out


# ---------------------------------------------------------------------------
# followup_customer.{en,es}.html
# ---------------------------------------------------------------------------

class TestFollowupCustomerTemplates:
    @pytest.mark.parametrize("language", ["en", "es"])
    def test_customer_name_is_escaped(self, language):
        order = make_order(customer_name=ATTACK_NAME, customer_language=language)
        out = _env.get_template(f"followup_customer.{language}.html").render(order=order)
        assert SCRIPT_ESCAPED in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD)

    def test_happy_path_en(self):
        order = make_order()
        out = _env.get_template("followup_customer.en.html").render(order=order)
        assert "Payment Reminder" in out
        assert "Hello María García," in out
        assert "<strong>#ORD-AB12CD</strong>" in out
        assert "<strong>$240.00</strong>" in out

    def test_happy_path_es(self):
        order = make_order(customer_language="es")
        out = _env.get_template("followup_customer.es.html").render(order=order)
        assert "Recordatorio de Pago" in out
        assert "Hola María García," in out


# ---------------------------------------------------------------------------
# order_cancelled_admin.html
# ---------------------------------------------------------------------------

class TestOrderCancelledAdminTemplate:
    def test_attacker_controlled_fields_are_escaped(self):
        # customer_name, customer_email, and (critically) reason — the
        # customer can supply reason via the public cancel-order endpoint.
        order = make_order(customer_name=ATTACK_NAME, customer_email=ATTACK_EMAIL)
        out = _env.get_template("order_cancelled_admin.html").render(
            order=order, cancelled_by="customer", reason=ATTACK_REASON
        )
        assert SCRIPT_ESCAPED in out
        assert LINK_ESCAPED_OPEN in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD, LINK_PAYLOAD)

    def test_cancelled_by_is_escaped(self):
        order = make_order()
        out = _env.get_template("order_cancelled_admin.html").render(
            order=order, cancelled_by=LINK_PAYLOAD, reason=None
        )
        assert LINK_ESCAPED_OPEN in out
        assert_no_raw_html(out, LINK_PAYLOAD)

    def test_missing_reason_falls_back_to_not_provided(self):
        order = make_order()
        out = _env.get_template("order_cancelled_admin.html").render(
            order=order, cancelled_by="admin", reason=None
        )
        assert "<strong>Reason:</strong> Not provided" in out

    def test_happy_path(self):
        order = make_order()
        out = _env.get_template("order_cancelled_admin.html").render(
            order=order,
            cancelled_by="diego@panamaincontext.com",
            reason="Customer requested cancellation",
        )
        assert "<h2>Order Cancelled</h2>" in out
        assert "ORD-AB12CD" in out
        assert "Customer requested cancellation" in out


# ---------------------------------------------------------------------------
# booking_rescheduled_customer.{en,es}.html
# ---------------------------------------------------------------------------

class TestBookingRescheduledCustomerTemplates:
    @pytest.mark.parametrize("language", ["en", "es"])
    def test_customer_name_and_preferred_time_are_escaped(self, language):
        order = make_order(customer_name=ATTACK_NAME, customer_language=language)
        booking = make_booking(preferred_time=ATTACK_PREFERRED_TIME)
        out = _env.get_template(f"booking_rescheduled_customer.{language}.html").render(
            order=order,
            booking=booking,
            tour_name="Casco Viejo Walking Tour",
            old_date=date(2026, 6, 10),
            old_time_slot="AM",
            reason=None,
        )
        assert SCRIPT_ESCAPED in out
        assert LINK_ESCAPED_OPEN in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD, LINK_PAYLOAD)

    @pytest.mark.parametrize("language", ["en", "es"])
    def test_reason_is_escaped(self, language):
        order = make_order(customer_language=language)
        booking = make_booking()
        out = _env.get_template(f"booking_rescheduled_customer.{language}.html").render(
            order=order,
            booking=booking,
            tour_name="Casco Viejo Walking Tour",
            old_date=date(2026, 6, 10),
            old_time_slot="AM",
            reason=LINK_PAYLOAD,
        )
        assert LINK_ESCAPED_OPEN in out
        assert_no_raw_html(out, LINK_PAYLOAD)

    def test_tour_name_is_escaped(self):
        order = make_order()
        booking = make_booking()
        out = _env.get_template("booking_rescheduled_customer.en.html").render(
            order=order,
            booking=booking,
            tour_name=ATTACK_TOUR_NAME,
            old_date=date(2026, 6, 10),
            old_time_slot="AM",
            reason=None,
        )
        assert SCRIPT_ESCAPED in out
        assert_no_raw_html(out, SCRIPT_PAYLOAD)

    def test_happy_path_en(self):
        order = make_order()
        booking = make_booking(tour_date=date(2026, 7, 1), time_slot="PM", preferred_time="2:00 PM")
        out = _env.get_template("booking_rescheduled_customer.en.html").render(
            order=order,
            booking=booking,
            tour_name="Casco Viejo Walking Tour",
            old_date=date(2026, 6, 10),
            old_time_slot="AM",
            reason="due to weather",
        )
        assert "Booking Rescheduled" in out
        assert "Hello María García," in out
        assert "<strong>Casco Viejo Walking Tour</strong>" in out
        assert "due to weather" in out
        assert "2026-06-10 — AM" in out
        assert "2026-07-01 — PM (2:00 PM)" in out

    def test_happy_path_es(self):
        order = make_order(customer_language="es")
        booking = make_booking(tour_date=date(2026, 7, 1), time_slot="PM")
        out = _env.get_template("booking_rescheduled_customer.es.html").render(
            order=order,
            booking=booking,
            tour_name="Tour por Casco Viejo",
            old_date=date(2026, 6, 10),
            old_time_slot="AM",
            reason=None,
        )
        assert "Reserva Reprogramada" in out
        assert "Hola María García," in out
        assert "<strong>Tour por Casco Viejo</strong>" in out


# ---------------------------------------------------------------------------
# admin_magic_link.html
# ---------------------------------------------------------------------------

class TestAdminMagicLinkTemplate:
    def test_url_is_attribute_escaped(self):
        # The URL is system-constructed (not attacker-controllable), but
        # auto-escape must still apply correctly to href context. Use a URL
        # with characters that require escaping inside an attribute value.
        url = 'https://admin.example.com/login?token=abc&next=/dashboard'
        out = _env.get_template("admin_magic_link.html").render(magic_link_url=url)
        # & must be escaped inside attribute values.
        assert 'href="https://admin.example.com/login?token=abc&amp;next=/dashboard"' in out

    def test_payload_in_url_would_be_neutralized(self):
        # Hypothetical: if a future bug let an attacker influence this URL,
        # autoescape would still block an attribute-breakout payload.
        url = 'https://x"><script>alert(1)</script>'
        out = _env.get_template("admin_magic_link.html").render(magic_link_url=url)
        assert "<script>alert(1)</script>" not in out
        assert "&#34;" in out  # escaped double-quote

    def test_happy_path(self):
        out = _env.get_template("admin_magic_link.html").render(
            magic_link_url="https://admin.example.com/login?token=abc"
        )
        assert "Admin Access" in out
        assert "https://admin.example.com/login?token=abc" in out