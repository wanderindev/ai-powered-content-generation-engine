"""Tests for the shared hardened HTTP client (epic #275).

Covers the SSRF-validating transport, the transient-error retry helper, and the
rate limiter. No real network: socket resolution and the parent transport are
patched so validation logic is exercised in isolation.
"""

import ipaddress
import socket
import time
from unittest.mock import patch

import httpx
import pytest
from app.services.http_client import (
    MAX_CONNECT_ATTEMPTS,
    RateLimiter,
    SSRFError,
    SSRFValidatingTransport,
    _is_blocked_ip,
    run_with_retry,
)


def _addrinfo(ip: str):
    """Build a getaddrinfo-shaped result for a single address."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


class TestIsBlockedIp:
    @pytest.mark.parametrize(
        "ip,blocked",
        [
            ("127.0.0.1", True),  # loopback
            ("10.0.0.1", True),  # private
            ("192.168.1.1", True),  # private
            ("172.16.0.1", True),  # private
            ("169.254.169.254", True),  # link-local / cloud metadata
            ("0.0.0.0", True),  # unspecified
            ("::1", True),  # IPv6 loopback
            ("fe80::1", True),  # IPv6 link-local
            ("::ffff:127.0.0.1", True),  # IPv4-mapped loopback
            ("8.8.8.8", False),  # public
            ("93.184.216.34", False),  # public
        ],
    )
    def test_classification(self, ip, blocked):
        assert _is_blocked_ip(ipaddress.ip_address(ip)) is blocked


class TestSSRFValidatingTransport:
    def _req(self, url: str) -> httpx.Request:
        return httpx.Request("GET", url)

    def test_rejects_non_https_scheme(self):
        transport = SSRFValidatingTransport()
        with pytest.raises(SSRFError, match="scheme"):
            transport.handle_request(self._req("http://example.com/x"))

    def test_rejects_blocked_host(self):
        transport = SSRFValidatingTransport()
        with pytest.raises(SSRFError, match="host"):
            transport.handle_request(self._req("https://localhost/x"))

    def test_rejects_private_resolved_ip(self):
        # DNS-rebinding shape: a "public" name resolves to a private address.
        transport = SSRFValidatingTransport()
        with patch(
            "app.services.http_client.socket.getaddrinfo", return_value=_addrinfo("10.0.0.5")
        ):
            with pytest.raises(SSRFError, match="blocked address"):
                transport.handle_request(self._req("https://evil.example.com/x"))

    def test_rejects_cloud_metadata_ip(self):
        transport = SSRFValidatingTransport()
        with patch(
            "app.services.http_client.socket.getaddrinfo",
            return_value=_addrinfo("169.254.169.254"),
        ):
            with pytest.raises(SSRFError):
                transport.handle_request(self._req("https://meta.example.com/x"))

    def test_rejects_when_any_resolved_ip_is_blocked(self):
        transport = SSRFValidatingTransport()
        mixed = _addrinfo("93.184.216.34") + _addrinfo("127.0.0.1")
        with patch("app.services.http_client.socket.getaddrinfo", return_value=mixed):
            with pytest.raises(SSRFError):
                transport.handle_request(self._req("https://mixed.example.com/x"))

    def test_allows_public_ip_and_pins_connection(self):
        transport = SSRFValidatingTransport()
        sentinel = object()
        with patch(
            "app.services.http_client.socket.getaddrinfo",
            return_value=_addrinfo("93.184.216.34"),
        ):
            with patch.object(
                httpx.HTTPTransport, "handle_request", return_value=sentinel
            ) as parent:
                result = transport.handle_request(self._req("https://example.com/path"))

        assert result is sentinel
        sent = parent.call_args[0][0]
        # Connection pinned to the validated IP, with Host + SNI preserved.
        assert sent.url.host == "93.184.216.34"
        assert sent.extensions.get("sni_hostname") == "example.com"
        assert sent.headers["Host"] == "example.com"

    def test_pins_ipv4_over_ipv6_on_dual_stack_host(self):
        """upload.wikimedia.org-shaped case: dual-stack DNS, no IPv6 egress in
        this environment. Pinning must deterministically prefer the IPv4
        record — picking the IPv6 one fails with ENETUNREACH (real bug hit
        importing a Wikimedia URL through the picker)."""
        transport = SSRFValidatingTransport()
        dual_stack = _addrinfo("2620:0:861:ed1a::2:b") + _addrinfo("208.80.154.240")
        with patch("app.services.http_client.socket.getaddrinfo", return_value=dual_stack):
            with patch.object(
                httpx.HTTPTransport, "handle_request", return_value=object()
            ) as parent:
                transport.handle_request(self._req("https://upload.wikimedia.org/x.jpg"))

        assert parent.call_args[0][0].url.host == "208.80.154.240"

    def test_falls_back_to_next_address_when_pinned_connect_is_refused(self):
        """Pinning an IP literal costs httpcore's per-address failover (an IP
        literal resolves to one address, so ``socket.create_connection`` has
        nothing to walk). A multi-homed host with one dead record must still be
        fetched — otherwise the caller sees ECONNREFUSED on a host a browser
        reaches fine (PIC-PYTHON-FASTAPI-23 shape)."""
        transport = SSRFValidatingTransport()
        sentinel = object()
        attempted: list[str] = []

        def parent(request, *args, **kwargs):
            attempted.append(request.url.host)
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
            return sentinel

        two_records = _addrinfo("93.184.216.35") + _addrinfo("93.184.216.34")
        with patch("app.services.http_client.socket.getaddrinfo", return_value=two_records):
            with patch.object(httpx.HTTPTransport, "handle_request", side_effect=parent):
                result = transport.handle_request(self._req("https://example.com/x.jpg"))

        assert result is sentinel
        # Deterministic order (sorted), and the dead record did not end the request.
        assert attempted == ["93.184.216.34", "93.184.216.35"]

    def test_connect_error_propagates_when_every_address_fails(self):
        """Failover is not a retry loop: once the validated addresses are
        exhausted the original transport error reaches the caller unchanged."""
        transport = SSRFValidatingTransport()
        two_records = _addrinfo("93.184.216.35") + _addrinfo("93.184.216.34")

        def parent(request, *args, **kwargs):
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

        with patch("app.services.http_client.socket.getaddrinfo", return_value=two_records):
            with patch.object(
                httpx.HTTPTransport, "handle_request", side_effect=parent
            ) as mock_parent:
                with pytest.raises(httpx.ConnectError, match="Connection refused"):
                    transport.handle_request(self._req("https://example.com/x.jpg"))

        assert mock_parent.call_count == 2

    def test_connect_attempts_are_bounded(self):
        """``media_http_timeout`` applies per connect attempt and import-url runs
        on the request path, so a many-record host must not multiply the wait
        without limit."""
        transport = SSRFValidatingTransport()
        many = sum((_addrinfo(f"93.184.216.{n}") for n in range(30, 38)), [])

        def parent(request, *args, **kwargs):
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

        with patch("app.services.http_client.socket.getaddrinfo", return_value=many):
            with patch.object(
                httpx.HTTPTransport, "handle_request", side_effect=parent
            ) as mock_parent:
                with pytest.raises(httpx.ConnectError):
                    transport.handle_request(self._req("https://example.com/x.jpg"))

        assert mock_parent.call_count == MAX_CONNECT_ATTEMPTS

    def test_sni_is_preserved_across_the_failover(self):
        transport = SSRFValidatingTransport()
        two_records = _addrinfo("93.184.216.35") + _addrinfo("93.184.216.34")

        def parent(request, *args, **kwargs):
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectError("refused", request=request)
            return object()

        with patch("app.services.http_client.socket.getaddrinfo", return_value=two_records):
            with patch.object(
                httpx.HTTPTransport, "handle_request", side_effect=parent
            ) as mock_parent:
                transport.handle_request(self._req("https://example.com/x.jpg"))

        sent = mock_parent.call_args[0][0]
        assert sent.extensions.get("sni_hostname") == "example.com"
        assert sent.headers["Host"] == "example.com"

    def test_ipv6_only_host_still_pins_when_no_ipv4_available(self):
        transport = SSRFValidatingTransport()
        with patch(
            "app.services.http_client.socket.getaddrinfo",
            return_value=_addrinfo("2620:0:861:ed1a::2:b"),  # real public v6 (upload.wikimedia.org)
        ):
            with patch.object(
                httpx.HTTPTransport, "handle_request", return_value=object()
            ) as parent:
                transport.handle_request(self._req("https://v6only.example.com/x"))

        assert parent.call_args[0][0].url.host == "2620:0:861:ed1a::2:b"


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x.test/")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestRunWithRetry:
    def test_retries_transient_5xx_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _status_error(503)
            return "ok"

        assert run_with_retry(fn, max_attempts=3) == "ok"
        assert calls["n"] == 2

    def test_retries_429(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _status_error(429)
            return "ok"

        assert run_with_retry(fn, max_attempts=3) == "ok"
        assert calls["n"] == 2

    def test_gives_up_after_max_attempts(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _status_error(500)

        with pytest.raises(httpx.HTTPStatusError):
            run_with_retry(fn, max_attempts=2)
        assert calls["n"] == 2

    def test_does_not_retry_on_4xx(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _status_error(404)

        with pytest.raises(httpx.HTTPStatusError):
            run_with_retry(fn, max_attempts=3)
        assert calls["n"] == 1

    def test_does_not_retry_ssrf_error(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise SSRFError("blocked")

        with pytest.raises(SSRFError):
            run_with_retry(fn, max_attempts=3)
        assert calls["n"] == 1


class TestRateLimiter:
    def test_zero_interval_never_sleeps(self):
        rl = RateLimiter(0)
        rl.wait()
        rl.wait()  # no exception, effectively instant

    def test_enforces_minimum_interval(self):
        rl = RateLimiter(0.2)
        rl.wait()  # first call sets the baseline, no sleep
        start = time.monotonic()
        rl.wait()
        assert time.monotonic() - start >= 0.2 * 0.9
