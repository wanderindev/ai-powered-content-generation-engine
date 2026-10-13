"""Sentry event scrubbing.

We run Sentry with ``send_default_pii=False`` (see ``app.main``), so the SDK
already withholds most PII. This module adds an explicit ``before_send`` hook so
the guarantee is enforced in our own code rather than relying on SDK defaults:
request bodies, cookies, auth headers, and any user context are stripped before
an event leaves the process. Error metadata (exception, stacktrace, breadcrumbs,
tags, transaction name, request method/URL) is preserved for debugging.

The site collects educator emails, contact-form submissions, and booking PII
(names, child age ranges, dietary notes); none of it should land in Sentry. See
issue #9.
"""

from __future__ import annotations

# Request headers that may carry credentials or session identifiers. Compared
# case-insensitively against the keys Sentry attaches to ``request.headers``.
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}


def scrub_pii_event(event: dict, hint: dict | None = None) -> dict | None:
    """Strip PII from a Sentry event before it is sent.

    Wired up as the ``before_send`` callback in ``sentry_sdk.init``. Mutates and
    returns ``event``; returning ``None`` would drop the event entirely, which we
    never want here.
    """
    request = event.get("request")
    if isinstance(request, dict):
        # Request/JSON/form body — the most likely place for emails, names, and
        # booking details.
        request.pop("data", None)
        # Cookies (session tokens) and the raw query string (may contain PII in
        # links or verification codes).
        request.pop("cookies", None)
        request.pop("query_string", None)

        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SENSITIVE_HEADERS:
                    headers.pop(key, None)

    # No code path calls ``set_user`` today, but drop it defensively so a future
    # regression can't leak identity (email/username/IP) to Sentry.
    event.pop("user", None)

    return event
