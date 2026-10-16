"""Sentry event scrubbing.

We run Sentry with ``send_default_pii=False`` (see ``app.main``), so the SDK
already withholds most PII. This module adds an explicit ``before_send`` hook so
the guarantee is enforced in our own code rather than relying on SDK defaults:
request bodies, cookies, auth headers, and any user context are stripped before
an event leaves the process. Error metadata (exception, stacktrace, breadcrumbs,
tags, transaction name, request method/URL) is preserved for debugging — except
that credential-bearing URL parameters (``access_token``, ``input_token``,
``client_secret``, ``token``) are redacted from breadcrumbs, both as each one
is recorded (``scrub_breadcrumb``, the ``before_breadcrumb`` hook) and again on
the event. Sentry's httpx integration records every outbound request's query
string as a breadcrumb (``http.query``) even with tracing off, and the SDK's
default scrubber matches dict *keys* only, so a token in a URL would otherwise
ride along on every later event (#576: the Graph API ``debug_token`` call has
to send the Instagram token as ``input_token``). Free-text breadcrumb data —
the two email-bearing ones noted in CLAUDE.md — is still not inspected. The
same redaction (plus credential-named dict keys and ``Bearer`` values) runs over
exception / thread messages and stacktrace frame locals, which sentry-sdk
captures by default. Events for ``/api/v1/ask`` (Ask PIC, #635) go further:
every frame's locals are dropped outright, because the reader's free-text
question sits in FastAPI's own request frames where no redaction pattern can
find it.

The site collects educator emails, contact-form submissions, and booking PII
(names, child age ranges, dietary notes); none of it should land in Sentry. See
issue #9.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Request headers that may carry credentials or session identifiers. Compared
# case-insensitively against the keys Sentry attaches to ``request.headers``.
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}


_FILTERED = "[Filtered]"
_SECRET_NAMES = r"(?:access|input|refresh)_token|client_secret|token"

# ``name=value`` pairs whose value is a credential, wherever they appear in a
# string (a bare query string, a full URL, a request/URL repr, a message).
_TOKEN_PARAM = re.compile(rf"(?i)(?<![A-Za-z0-9_])({_SECRET_NAMES})=[^&#\s\"']*")
# ``'name': 'value'`` inside a dict repr (how Sentry serializes frame locals
# such as ``params = {'input_token': ...}``).
_TOKEN_QUOTED_PAIR = re.compile(
    rf"""(?i)(['"])({_SECRET_NAMES}|authorization)\1(\s*:\s*)(['"])(?:(?!\4).)*\4"""
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
# Dict keys whose value is a credential outright.
_SECRET_KEYS = {
    "access_token",
    "input_token",
    "refresh_token",
    "client_secret",
    "token",
    "authorization",
    "api_key",
}


def redact_secrets(text: str) -> str:
    """Redact credential-looking substrings from free text.

    Also used by ``app.services.instagram`` on the operator-facing error
    messages it builds, so a token never reaches a message in the first place.
    """
    text = _TOKEN_PARAM.sub(rf"\1={_FILTERED}", text)
    text = _TOKEN_QUOTED_PAIR.sub(rf"\1\2\1\3\4{_FILTERED}\4", text)
    return _BEARER.sub(f"Bearer {_FILTERED}", text)


def _redact(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {
            k: (
                _FILTERED
                if isinstance(k, str) and k.lower() in _SECRET_KEYS and v is not None
                else _redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


# Routes whose events lose every stacktrace frame's ``vars`` (#635). Ask PIC's
# question is free reader text (PII-equivalent, #9), and FastAPI's own request
# frames (``app()``, ``solve_dependencies``) hold the parsed ``body`` above any
# frame our code can guard, so the structural fix is to drop frame locals for
# the whole route. The event itself is always kept: exception type and message,
# frames (file/line/function), tags, breadcrumbs, route and method still reach
# Sentry so real errors stay visible.
# ``/mcp`` (#639): MCP tool arguments carry research / article bodies, and the
# SDK's request frames hold the parsed JSON-RPC body; same structural fix.
_VARS_STRIPPED_PATHS = frozenset({"/api/v1/ask", "/mcp"})


def _normalize_path(raw: object) -> str | None:
    """The path part of a request URL (absolute or not) or a transaction name
    (``/api/v1/ask`` or ``POST /api/v1/ask``), without a trailing slash."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    token = raw.strip().split()[-1]
    path = urlsplit(token).path
    return path.rstrip("/").lower() or None


def _strips_frame_vars(event: dict) -> bool:
    """True when the event belongs to a route in ``_VARS_STRIPPED_PATHS``, judged
    by the request URL path or the transaction name (either is enough)."""
    candidates = [event.get("transaction")]
    request = event.get("request")
    if isinstance(request, dict):
        candidates.append(request.get("url"))
    return any(_normalize_path(c) in _VARS_STRIPPED_PATHS for c in candidates)


def _scrub_stacktrace(container: dict, *, strip_vars: bool = False) -> None:
    """Redact credentials from one exception/thread's message and frame locals.

    sentry-sdk captures frame locals by default (``include_local_variables``),
    and its default scrubber is key-based and non-recursive — it knows neither
    ``params`` nor ``input_token``. A Graph API failure deep in httpx would
    otherwise ship the token in ``params``, the request repr, or the URL repr
    (#576 review).
    """
    if isinstance(container.get("value"), str):
        container["value"] = redact_secrets(container["value"])
    stacktrace = container.get("stacktrace")
    frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
    if isinstance(frames, list):
        for frame in frames:
            if isinstance(frame, dict) and "vars" in frame:
                if strip_vars:
                    del frame["vars"]
                else:
                    frame["vars"] = _redact(frame["vars"])


def scrub_breadcrumb(crumb: dict, hint: dict | None = None) -> dict | None:
    """Redact credential URL parameters from one breadcrumb.

    Wired up as ``before_breadcrumb``; ``scrub_pii_event`` re-applies it to
    every breadcrumb on the event as a second line.
    """
    for key in ("message", "data"):
        if key in crumb:
            crumb[key] = _redact(crumb[key])
    return crumb


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

    # Every exception in the chain and every thread, our frames and FastAPI's.
    strip_vars = _strips_frame_vars(event)
    for section in ("exception", "threads"):
        block = event.get(section)
        values = block.get("values") if isinstance(block, dict) else None
        if isinstance(values, list):
            for container in values:
                if isinstance(container, dict):
                    _scrub_stacktrace(container, strip_vars=strip_vars)

    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    if isinstance(values, list):
        for crumb in values:
            if isinstance(crumb, dict):
                scrub_breadcrumb(crumb)

    # No code path calls ``set_user`` today, but drop it defensively so a future
    # regression can't leak identity (email/username/IP) to Sentry.
    event.pop("user", None)

    return event
