"""Unit tests for PayPalService (#42).

The service constructs its own ``httpx.Client``, so we patch
``app.services.paypal.httpx.Client`` to inject an ``httpx.MockTransport`` that
routes canned responses by request path. No real network is touched. Covers
OAuth token caching/refresh, invoice create/send/status/cancel, and webhook
signature verification (including the fail-closed path when ``webhook_id`` is
unset, per #32).
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from app.services.paypal import PayPalService

TOKEN_PATH = "/v1/oauth2/token"


def _make_service():
    """A service with deterministic config and a cold token cache."""
    svc = PayPalService()
    svc.base_url = "https://paypal.test"
    svc.client_id = "client-id"
    svc.client_secret = "client-secret"
    svc.business_email = "biz@example.com"
    svc.webhook_id = "WH-TEST"
    svc._access_token = None
    svc._token_expires_at = None
    return svc


def _patch_client(monkeypatch, handler):
    """Route every httpx.Client built inside the paypal module through MockTransport."""
    real_client = httpx.Client  # capture before patching (paypal.httpx is this module)

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.paypal.httpx.Client", factory)


def _order_and_bookings():
    order = SimpleNamespace(
        reference="ORD-PP1",
        customer_name="Jane Doe",
        customer_email="jane@example.com",
        grand_total="100.00",
    )
    booking = SimpleNamespace(
        tour=SimpleNamespace(name="Casco Tour"),
        tour_date=date(2099, 1, 1),
        time_slot="AM",
        total_participants=2,
        grand_total="100.00",
    )
    return order, [booking]


def test_access_token_is_cached(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600})

    svc = _make_service()
    _patch_client(monkeypatch, handler)

    assert svc._get_access_token() == "tok-123"
    assert svc._get_access_token() == "tok-123"
    # Second call served from cache — only one token request hit the wire.
    assert calls.count(TOKEN_PATH) == 1


def test_access_token_refreshes_when_expired(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

    svc = _make_service()
    svc._access_token = "stale"
    svc._token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    _patch_client(monkeypatch, handler)

    assert svc._get_access_token() == "fresh"
    assert calls.count(TOKEN_PATH) == 1


def test_create_draft_invoice_posts_and_returns_id(monkeypatch):
    def handler(request):
        if request.url.path.endswith(TOKEN_PATH):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.method == "POST" and request.url.path.endswith("/v2/invoicing/invoices"):
            return httpx.Response(201, json={"id": "INV-9"})
        return httpx.Response(404, json={})

    svc = _make_service()
    _patch_client(monkeypatch, handler)
    order, bookings = _order_and_bookings()

    result = svc.create_draft_invoice(order, bookings, due_hours=48)
    assert result["invoice_id"] == "INV-9"
    assert result["total"] == "100.00"
    assert len(result["items"]) == 1
    assert result["items"][0]["unit_amount"]["value"] == "100.00"


def test_send_invoice_returns_payer_view_url(monkeypatch):
    def handler(request):
        path = request.url.path
        if path.endswith(TOKEN_PATH):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.method == "POST" and path.endswith("/send"):
            return httpx.Response(200, json={})
        if request.method == "GET" and path.endswith("/invoices/INV-9"):
            return httpx.Response(
                200,
                json={"links": [{"rel": "payer-view", "href": "https://pay.test/INV-9"}]},
            )
        return httpx.Response(404, json={})

    svc = _make_service()
    _patch_client(monkeypatch, handler)
    assert svc.send_invoice("INV-9") == "https://pay.test/INV-9"


def test_get_invoice_status(monkeypatch):
    def handler(request):
        if request.url.path.endswith(TOKEN_PATH):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"status": "SENT"})

    svc = _make_service()
    _patch_client(monkeypatch, handler)
    assert svc.get_invoice_status("INV-9") == "SENT"


@pytest.mark.parametrize("status_code,expected", [(200, True), (204, True), (422, False)])
def test_cancel_invoice(monkeypatch, status_code, expected):
    def handler(request):
        if request.url.path.endswith(TOKEN_PATH):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(status_code, json={})

    svc = _make_service()
    _patch_client(monkeypatch, handler)
    assert svc.cancel_invoice("INV-9") is expected


@pytest.mark.parametrize("verification_status,expected", [("SUCCESS", True), ("FAILURE", False)])
def test_verify_webhook_signature(monkeypatch, verification_status, expected):
    def handler(request):
        if request.url.path.endswith(TOKEN_PATH):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"verification_status": verification_status})

    svc = _make_service()
    _patch_client(monkeypatch, handler)
    result = svc.verify_webhook_signature(
        transmission_id="tid",
        transmission_time="ttime",
        event_body="{}",
        cert_url="https://cert",
        actual_signature="sig",
        auth_algo="SHA256withRSA",
    )
    assert result is expected


def test_verify_webhook_signature_fails_closed_without_webhook_id(monkeypatch):
    """No webhook_id -> reject without any HTTP call (fail closed, #32)."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    svc = _make_service()
    svc.webhook_id = ""
    _patch_client(monkeypatch, handler)

    result = svc.verify_webhook_signature(
        transmission_id="tid",
        transmission_time="ttime",
        event_body="{}",
        cert_url="https://cert",
        actual_signature="sig",
        auth_algo="SHA256withRSA",
    )
    assert result is False
    assert calls == []  # never reached the network
