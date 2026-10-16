"""Tests for the global SMTP guard (#563).

A managed-agents sandbox running this suite on 2026-10-05 delivered real mail
through the IP-allowlisted Workspace relay. These pin the three layers that
now stop that: the autouse ``smtp_outbox`` fixture, the unresolvable test relay
host, and ``send_email``'s refusal to touch the real transport under
ENVIRONMENT=test.
"""

import smtplib

import pytest
from app.core.config import get_settings
from app.services import email_sender
from app.services.email_sender import RealSendBlockedError, send_email


def test_send_email_is_recorded_not_sent(smtp_outbox):
    send_email("someone@example.com", "Hello", "<p>Hi</p>")

    assert len(smtp_outbox) == 1
    assert smtp_outbox[0]["To"] == "someone@example.com"
    assert smtp_outbox[0]["Subject"] == "Hello"


def test_guard_is_active_without_being_requested():
    """Autouse: a test that never mentions the fixture is still guarded."""
    assert smtplib.SMTP is not email_sender._REAL_SMTP


def test_test_relay_host_is_unresolvable():
    assert get_settings().smtp_relay_host == "smtp.invalid"


def test_real_transport_is_refused_under_test_environment(monkeypatch, smtp_outbox):
    """A bypassed guard fails loudly instead of connecting."""
    monkeypatch.setattr(smtplib, "SMTP", email_sender._REAL_SMTP)

    with pytest.raises(RealSendBlockedError):
        send_email("someone@example.com", "S", "B")
    assert smtp_outbox == []


def test_refusal_is_not_an_email_delivery_error():
    """Admin-side callers absorb EmailDeliveryError; the refusal must escape them."""
    assert not issubclass(RealSendBlockedError, email_sender.EmailDeliveryError)


def test_app_flow_lands_in_the_outbox(test_client, smtp_outbox):
    """An endpoint that emails (the contact form) is caught by the guard."""
    response = test_client.post(
        "/api/v1/contact",
        json={
            "interest": "tours",
            "first_name": "Ana",
            "last_name": "Test",
            "email": "ana@example.com",
            "message": "Hola",
        },
    )

    assert response.status_code in (200, 201), response.text
    assert len(smtp_outbox) == 1
    assert "[Contact Form]" in smtp_outbox[0]["Subject"]
