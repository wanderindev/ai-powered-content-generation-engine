"""
Tests for the SMTP-relay-based send_email wrapper.

Mocks `smtplib.SMTP` to exercise the success path and each failure mode the
wrapper translates into `EmailDeliveryError`. Verifies that:

- the configured host/port/timeout are passed through;
- the STARTTLS handshake is invoked before send_message;
- transport, auth, OS-level, and timeout exceptions all surface as
  `EmailDeliveryError`;
- `sentry_sdk.capture_exception` is called with the original cause before
  re-raising (wrapper-level Sentry capture means callers don't double-capture).

These tests intentionally do not touch the database — the wrapper is pure
stdlib transport. The Postgres testcontainer fixture is therefore not pulled in.
"""

import smtplib
import socket
from unittest.mock import MagicMock, patch

import pytest
from app.services.email_sender import EmailDeliveryError, send_email


@pytest.fixture
def smtp_mock():
    """Patch smtplib.SMTP; yield (constructor_mock, instance_mock)."""
    with patch("app.services.email_sender.smtplib.SMTP") as ctor:
        instance = MagicMock()
        # `with smtplib.SMTP(...) as smtp:` uses __enter__/__exit__
        instance.__enter__.return_value = instance
        instance.__exit__.return_value = False
        ctor.return_value = instance
        yield ctor, instance


@pytest.fixture
def sentry_capture_mock():
    with patch("app.services.email_sender.sentry_sdk.capture_exception") as m:
        yield m


class TestSendEmailHappyPath:
    def test_send_email_completes_without_exception(self, smtp_mock):
        _, instance = smtp_mock
        send_email("recipient@example.com", "Subject", "<p>Body</p>")
        instance.starttls.assert_called_once()
        instance.send_message.assert_called_once()

    def test_send_email_uses_configured_host_port_timeout(self, smtp_mock):
        ctor, _ = smtp_mock
        send_email("r@example.com", "S", "B")
        # conftest points the host at smtp.invalid (#563); port/timeout are defaults.
        ctor.assert_called_once_with("smtp.invalid", 587, timeout=10)

    def test_send_email_constructs_html_message_with_headers(self, smtp_mock):
        _, instance = smtp_mock
        send_email("r@example.com", "Hello", "<p>Hi</p>")
        msg = instance.send_message.call_args.args[0]
        assert msg["Subject"] == "Hello"
        assert msg["To"] == "r@example.com"
        assert msg["From"]  # set from settings.sender_email
        assert msg.get_content_type() == "text/html"
        assert msg.get_payload(decode=True).decode("utf-8") == "<p>Hi</p>"


class TestSendEmailFailureModes:
    def test_smtp_exception_on_send_raises_email_delivery_error(
        self, smtp_mock, sentry_capture_mock
    ):
        _, instance = smtp_mock
        original = smtplib.SMTPException("relay rejected")
        instance.send_message.side_effect = original

        with pytest.raises(EmailDeliveryError) as exc_info:
            send_email("r@example.com", "S", "B")

        assert "r@example.com" in str(exc_info.value)
        assert exc_info.value.__cause__ is original
        sentry_capture_mock.assert_called_once_with(original)

    def test_auth_failure_on_starttls_raises(self, smtp_mock, sentry_capture_mock):
        _, instance = smtp_mock
        original = smtplib.SMTPAuthenticationError(530, b"Authentication required")
        instance.starttls.side_effect = original

        with pytest.raises(EmailDeliveryError):
            send_email("r@example.com", "S", "B")

        sentry_capture_mock.assert_called_once_with(original)

    def test_timeout_on_connect_raises(self, sentry_capture_mock):
        original = TimeoutError("connection timed out")
        with patch("app.services.email_sender.smtplib.SMTP", side_effect=original):
            with pytest.raises(EmailDeliveryError) as exc_info:
                send_email("r@example.com", "S", "B")

        assert exc_info.value.__cause__ is original
        sentry_capture_mock.assert_called_once_with(original)

    def test_connection_refused_raises(self, sentry_capture_mock):
        original = ConnectionRefusedError("refused")
        with patch("app.services.email_sender.smtplib.SMTP", side_effect=original):
            with pytest.raises(EmailDeliveryError):
                send_email("r@example.com", "S", "B")

        sentry_capture_mock.assert_called_once_with(original)

    def test_dns_failure_raises(self, sentry_capture_mock):
        # socket.gaierror is an OSError subclass — must be caught by the wrapper.
        original = socket.gaierror("Name or service not known")
        with patch("app.services.email_sender.smtplib.SMTP", side_effect=original):
            with pytest.raises(EmailDeliveryError):
                send_email("r@example.com", "S", "B")

        sentry_capture_mock.assert_called_once_with(original)
