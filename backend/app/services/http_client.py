"""Shared hardened outbound HTTP client for media acquisition (epic #275).

Single insertion point for every outbound HTTP call the media-acquisition path
makes to semi-trusted upstreams (LOC, Wikimedia, IIIF tile servers, and the
image bytes those point at). This is the media/crawler mirror of ``llm.py`` —
``image_storage.download_image``, ``LOCService``, and ``WikimediaService`` all
fetch *through* this module rather than each re-implementing safety defaults.

Provides:

- **SSRF-validating transport** (:class:`SSRFValidatingTransport`) — enforces an
  https-only scheme allowlist, a host blocklist, and rejects any host that
  resolves to a private / loopback / link-local / reserved / multicast address
  (incl. the cloud-metadata IP). Validation lives *in the transport* so it sees
  the resolved IP and runs again on **every redirect hop**; a pre-flight
  ``urlparse`` check would be bypassable via DNS rebinding or an attacker
  redirect. The validated IP is *pinned* into the connection (the request URL is
  rewritten to the IP literal, Host header and TLS SNI preserved) to close the
  resolve-then-connect TOCTOU window; a connect failure falls back to the next
  validated address, which pinning would otherwise forfeit.
- **Pooled-client factory** (:func:`make_hardened_client`) — one client per
  service instance / per crawl, reused across calls (no per-call TLS handshake).
- **Retry helper** (:func:`run_with_retry`) — exponential backoff on transient
  5xx / 429 and transport errors only; never on 4xx, never on an SSRF rejection.
- **Rate limiter** (:class:`RateLimiter`) — monotonic-based minimum interval.
  **Per-process, not cross-worker** — Redis-backed coordination is deferred to
  the #277 follow-up.
"""

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# --- SSRF policy (closed security vocab — kept here, not in env config) ---

# https only. LOC, Wikimedia, IIIF, tile.loc.gov, and upload.wikimedia.org are
# all https; plaintext http is rejected.
ALLOWED_SCHEMES = frozenset({"https"})

# Belt-and-suspenders names; the resolved-IP check below is the real guard.
BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal", "169.254.169.254"})

# How many of a host's validated addresses a single request may try before
# giving up. Pinning (below) costs httpcore's own per-address failover, and this
# is what restores it — bounded, because ``media_http_timeout`` applies per
# connect attempt and ``import_media_from_url`` runs on the request path.
MAX_CONNECT_ATTEMPTS = 3


class SSRFError(ValueError):
    """Raised when a request URL fails SSRF validation.

    Subclasses ``ValueError`` (not ``httpx.TransportError``) so it is never
    swept up by the transient-error retry policy — an SSRF rejection is final.
    """


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """True if ``ip`` is in a range we must never connect to from a fetch."""
    # IPv4-mapped IPv6 (``::ffff:127.0.0.1``) — validate the embedded v4 so a
    # mapped loopback/private address can't slip past the v6 predicates.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # incl. 169.254.169.254 cloud metadata
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class SSRFValidatingTransport(httpx.HTTPTransport):
    """``httpx`` transport that validates + pins the resolved IP before connect.

    Runs for the initial request and again for each redirect hop (httpx
    re-dispatches every hop through the transport).
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url

        if url.scheme not in ALLOWED_SCHEMES:
            raise SSRFError(f"Disallowed scheme: {url.scheme!r}")

        host = url.host
        if not host:
            raise SSRFError("Request URL has no host")
        if host.lower() in BLOCKED_HOSTS:
            raise SSRFError(f"Disallowed host: {host!r}")

        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise SSRFError(f"Could not resolve host {host!r}: {e}") from e

        resolved = {info[4][0] for info in infos}
        if not resolved:
            raise SSRFError(f"Host {host!r} resolved to no addresses")

        # Reject if ANY resolved address is blocked — an attacker controlling DNS
        # could return a mix of safe and unsafe records.
        for ip_str in resolved:
            ip = ipaddress.ip_address(ip_str)
            if _is_blocked_ip(ip):
                raise SSRFError(f"Host {host!r} resolves to blocked address {ip_str}")

        # Pin to a validated IP so httpcore connects to exactly the address we
        # checked (no second DNS lookup). Host header is already set from the
        # original URL; preserve TLS SNI / cert hostname via the extension.
        # Prefer IPv4: this and prod both lack IPv6 egress, so pinning to an
        # IPv6 record from a dual-stack host (e.g. upload.wikimedia.org) fails
        # with ENETUNREACH. ``resolved`` is a set — iteration order is
        # otherwise process-hash-randomized, so this was an intermittent
        # failure, not a v6-specific one; sorting makes the pick deterministic.
        candidates = sorted(ip for ip in resolved if ":" not in ip) + sorted(
            ip for ip in resolved if ":" in ip
        )
        request.extensions = {**request.extensions, "sni_hostname": host}

        # Pinning an IP literal also costs httpcore's per-address failover:
        # ``socket.create_connection`` walks every ``getaddrinfo`` result and
        # tries the next one when a connect fails, but an IP literal resolves to
        # exactly one. So walk the validated candidates here instead — one dead
        # record on a multi-homed host should not be a failed fetch. Only
        # connect-phase errors are retried (nothing has been written to the wire
        # yet, so the request stays replayable), and every candidate comes from
        # the set validated above, so the SSRF guarantee is unchanged.
        attempts = candidates[:MAX_CONNECT_ATTEMPTS]
        for pinned in attempts[:-1]:
            request.url = url.copy_with(host=pinned)
            try:
                return super().handle_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                logger.warning(
                    "Connect to %s pinned at %s failed (%s); trying next address", host, pinned, e
                )

        # Last candidate: let its failure propagate untouched.
        request.url = url.copy_with(host=attempts[-1])
        return super().handle_request(request)


def make_hardened_client(
    *,
    timeout: float | None = None,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    """Return a pooled ``httpx.Client`` wired to the SSRF-validating transport.

    Construct one per service instance / per crawl and reuse it across calls.
    Redirects are followed by the client and re-validated per hop by the
    transport.
    """
    settings = get_settings()
    return httpx.Client(
        transport=SSRFValidatingTransport(),
        timeout=settings.media_http_timeout if timeout is None else timeout,
        follow_redirects=follow_redirects,
        headers=headers,
    )


# --- Retry on transient failures (issue #73) ---

_RETRYABLE_STATUS = frozenset({429, *range(500, 600)})

T = TypeVar("T")


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transient transport errors and 5xx / 429 only.

    Never retries an :class:`SSRFError` (it is a ``ValueError``, not a
    ``TransportError``) or a 4xx (a 404 / 400 won't fix itself).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


def run_with_retry(fn: Callable[[], T], *, max_attempts: int | None = None) -> T:
    """Run ``fn`` with exponential backoff on transient HTTP failures.

    ``fn`` must call ``response.raise_for_status()`` so retryable 5xx / 429
    surface as ``httpx.HTTPStatusError``. Re-raises the final exception once
    attempts are exhausted.
    """
    attempts = get_settings().media_http_max_retries if max_attempts is None else max_attempts
    retrying = Retrying(
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_exponential(multiplier=0.5, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    return retrying(fn)


# --- Rate limiting (per-process; cross-worker coordination deferred to #277) ---


class RateLimiter:
    """Enforce a minimum interval between calls (monotonic-clock based).

    Per-process state: each worker has its own limiter, so concurrent workers
    can collectively exceed an upstream's published ceiling. Adequate for the
    manual, infrequent, single-admin crawls today; Redis-backed cross-worker
    coordination is the #277 follow-up.
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last_request_time: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.monotonic()
