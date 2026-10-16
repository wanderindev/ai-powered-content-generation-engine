"""Tests for the Sentry PII scrubber (issue #9)."""

import pytest
from app.core.observability import redact_secrets, scrub_breadcrumb, scrub_pii_event


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


# --- Credential URL parameters in breadcrumbs (#576) -------------------------


def _httpx_crumb(query):
    """The shape sentry-sdk's httpx integration records for an outbound GET."""
    return {
        "type": "http",
        "category": "httplib",
        "data": {
            "url": "https://graph.facebook.com/v23.0/debug_token",
            "http.method": "GET",
            "http.query": query,
            "http.fragment": "",
            "status_code": 200,
        },
    }


def test_scrub_breadcrumb_redacts_graph_tokens():
    crumb = scrub_breadcrumb(
        _httpx_crumb("input_token=EAAGsecret123&access_token=EAAGsecret123&fields=username")
    )
    assert "EAAGsecret123" not in str(crumb)
    assert crumb["data"]["http.query"] == (
        "input_token=[Filtered]&access_token=[Filtered]&fields=username"
    )
    assert crumb["data"]["url"] == "https://graph.facebook.com/v23.0/debug_token"


def test_scrub_breadcrumb_redacts_tokens_inside_full_urls_and_messages():
    crumb = scrub_breadcrumb(
        {
            "message": "GET https://x.test/a?token=abc123&b=1",
            "data": {"nested": ["https://x.test/?client_secret=s3cr3t"]},
        }
    )
    assert "abc123" not in str(crumb)
    assert "s3cr3t" not in str(crumb)
    assert "b=1" in crumb["message"]


def test_scrub_breadcrumb_leaves_lookalike_params_alone():
    crumb = scrub_breadcrumb(_httpx_crumb("fields=status_code&page_token_hint=1"))
    assert crumb["data"]["http.query"] == "fields=status_code&page_token_hint=1"


def test_event_with_graph_breadcrumb_carries_no_token():
    """An event captured after a debug_token GET (e.g. the expiry warning)."""
    event = {
        "message": "Instagram access token expires soon",
        "breadcrumbs": {"values": [_httpx_crumb("input_token=EAAGlive&fields=x")]},
    }
    scrubbed = scrub_pii_event(event)
    assert "EAAGlive" not in str(scrubbed)


def test_event_breadcrumbs_as_plain_list_are_scrubbed():
    event = scrub_pii_event({"breadcrumbs": [_httpx_crumb("access_token=EAAGlive")]})
    assert "EAAGlive" not in str(event)


def test_sentry_init_wires_the_breadcrumb_hook():
    import inspect

    import app.main as main_module

    assert "before_breadcrumb=scrub_breadcrumb" in inspect.getsource(main_module)


def test_scrub_redacts_tokens_in_exception_frame_vars_and_message():
    """#576 review: sentry-sdk captures frame locals by default; a Graph failure
    would otherwise ship params={'input_token': ...} and token-bearing reprs."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "InstagramPublishError",
                    "value": "failed for https://g/debug_token?input_token=EAAGlive",
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "_request",
                                "vars": {
                                    "params": "{'input_token': 'EAAGlive', 'fields': 'x'}",
                                    "request": (
                                        "<Request('GET', "
                                        "'https://g/debug_token?input_token=EAAGlive')>"
                                    ),
                                    "headers": {"Authorization": "'Bearer EAAGlive'"},
                                    "input_token": "'EAAGlive'",
                                    "kwargs": {"params": {"input_token": "'EAAGlive'"}},
                                    "count": 3,
                                },
                            }
                        ]
                    },
                }
            ]
        },
        "threads": {
            "values": [{"stacktrace": {"frames": [{"vars": {"auth": "Bearer EAAGlive"}}]}}]
        },
    }
    scrubbed = scrub_pii_event(event)
    assert "EAAGlive" not in str(scrubbed)
    frame_vars = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["count"] == 3
    assert "'fields': 'x'" in frame_vars["params"]


def test_scrub_redacts_api_key_frame_local():
    """#624: the Voyage seam holds its key in a local named ``api_key``."""
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [{"vars": {"api_key": "'pa-voyage-live'", "url": "'u'"}}]
                    }
                }
            ]
        }
    }
    scrubbed = scrub_pii_event(event)
    assert "pa-voyage-live" not in str(scrubbed)
    assert scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["url"] == "'u'"


# --- #635: frame locals are dropped for /api/v1/ask -----------------------------

_QUESTION = "SECRET-QUESTION when did the canal open"


def _ask_event(url=None, transaction=None):
    """A 5xx as sentry-sdk ships it: a chained exception whose frames include
    FastAPI's request handler (``body``) and our own frame, plus a thread."""

    def frames():
        return [
            {
                "filename": "fastapi/routing.py",
                "function": "app",
                "lineno": 300,
                "vars": {"body": {"question": _QUESTION}, "request": "<Request>"},
            },
            {
                "filename": "app/api/ask.py",
                "function": "_ask",
                "lineno": 90,
                "vars": {"question": f"'{_QUESTION}'", "access_token": "'x'"},
            },
        ]

    event = {
        "exception": {
            "values": [
                {
                    "type": "OperationalError",
                    "value": "db down",
                    "stacktrace": {"frames": frames()},
                },
                {"type": "RuntimeError", "value": "outer", "stacktrace": {"frames": frames()}},
            ]
        },
        "threads": {"values": [{"id": 1, "stacktrace": {"frames": frames()}}]},
        "breadcrumbs": {"values": [{"message": "llm call"}]},
        "tags": {"feature": "ask_pic"},
        "request": {"method": "POST", "data": {"question": _QUESTION}},
    }
    if url is not None:
        event["request"]["url"] = url
    if transaction is not None:
        event["transaction"] = transaction
    return event


@pytest.mark.parametrize(
    ("url", "transaction"),
    [
        ("https://api.panamaincontext.com/api/v1/ask", None),
        ("http://testserver/api/v1/ask/", None),
        ("https://api.panamaincontext.com/api/v1/ask?x=1", None),
        ("/api/v1/ask", None),
        (None, "/api/v1/ask"),
        (None, "POST /api/v1/ask"),
        ("https://api.panamaincontext.com/API/V1/ASK", None),
    ],
)
def test_ask_route_events_lose_every_frames_vars(url, transaction):
    scrubbed = scrub_pii_event(_ask_event(url, transaction))
    assert "SECRET-QUESTION" not in str(scrubbed)
    stacks = [v["stacktrace"] for v in scrubbed["exception"]["values"]]
    stacks += [t["stacktrace"] for t in scrubbed["threads"]["values"]]
    for stack in stacks:
        assert all("vars" not in frame for frame in stack["frames"])
        # Frames themselves survive: file, function, line.
        assert [f["function"] for f in stack["frames"]] == ["app", "_ask"]
        assert stack["frames"][1]["filename"] == "app/api/ask.py"
        assert stack["frames"][1]["lineno"] == 90
    # The event is kept, with what triage needs.
    values = scrubbed["exception"]["values"]
    assert [(v["type"], v["value"]) for v in values] == [
        ("OperationalError", "db down"),
        ("RuntimeError", "outer"),
    ]
    assert scrubbed["tags"] == {"feature": "ask_pic"}
    assert scrubbed["breadcrumbs"]["values"] == [{"message": "llm call"}]
    assert scrubbed["request"]["method"] == "POST"


@pytest.mark.parametrize(
    ("url", "transaction"),
    [
        ("https://api.panamaincontext.com/api/v1/contact", "/api/v1/contact"),
        ("https://api.panamaincontext.com/api/v1/ask-other", None),
        ("https://api.panamaincontext.com/api/v1/asks", "/api/v1/asks"),
        (None, None),
    ],
)
def test_other_routes_keep_their_frame_vars(url, transaction):
    scrubbed = scrub_pii_event(_ask_event(url, transaction))
    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][1]
    # Unchanged apart from the existing credential redaction.
    assert frame["vars"]["question"] == f"'{_QUESTION}'"
    assert frame["vars"]["access_token"] == "[Filtered]"
    thread_frame = scrubbed["threads"]["values"][0]["stacktrace"]["frames"][0]
    assert thread_frame["vars"]["body"] == {"question": _QUESTION}


def test_redact_secrets_leaves_ordinary_text_alone():
    text = "Media container not ready after 30s (status IN_PROGRESS)"
    assert redact_secrets(text) == text
