"""Tests for the Sentry PII scrubber (issue #9)."""

from app.core.observability import scrub_pii_event


def _full_event():
    """A representative Sentry event carrying every PII-bearing field."""
    return {
        "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
        "breadcrumbs": {"values": [{"message": "did a thing"}]},
        "transaction": "POST /api/v1/contact",
        "tags": {"endpoint": "contact"},
        "user": {"email": "educator@school.edu", "ip_address": "203.0.113.7"},
        "request": {
            "method": "POST",
            "url": "https://panamaincontext.com/api/v1/contact",
            "query_string": "code=123456",
            "data": {"name": "Jane Doe", "email": "jane@example.com"},
            "cookies": {"session": "secret-token"},
            "headers": {
                "Authorization": "Bearer abc",
                "Cookie": "session=secret-token",
                "Content-Type": "application/json",
                "User-Agent": "pytest",
            },
        },
    }


def test_scrub_removes_request_pii():
    event = scrub_pii_event(_full_event())
    request = event["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "query_string" not in request
    # Non-sensitive request metadata is preserved for debugging.
    assert request["method"] == "POST"
    assert request["url"].endswith("/api/v1/contact")


def test_scrub_removes_sensitive_headers_case_insensitively():
    event = scrub_pii_event(_full_event())
    headers = event["request"]["headers"]
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    # Harmless headers survive.
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == "pytest"


def test_scrub_removes_user_context():
    event = scrub_pii_event(_full_event())
    assert "user" not in event


def test_scrub_preserves_error_data():
    event = scrub_pii_event(_full_event())
    assert event["exception"]["values"][0]["type"] == "ValueError"
    assert event["breadcrumbs"]["values"][0]["message"] == "did a thing"
    assert event["transaction"] == "POST /api/v1/contact"
    assert event["tags"] == {"endpoint": "contact"}


def test_scrub_returns_event_not_none():
    """before_send must return the event; None would drop it entirely."""
    assert scrub_pii_event(_full_event()) is not None


def test_scrub_handles_event_without_request():
    event = scrub_pii_event({"exception": {"values": []}})
    assert event == {"exception": {"values": []}}
