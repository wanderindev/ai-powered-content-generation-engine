# Outbound HTTP to Semi-Trusted Hosts

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## Outbound HTTP to Semi-Trusted Hosts (epic #275)

`app/services/http_client.py` is the media/crawler mirror of `llm.py`: the single
seam every crawler / media fetch goes through instead of bare `httpx`. Current
call sites are `loc.py`, `wikimedia.py`, `image_storage.py` and `instagram.py` —
a new one belongs here too, not on its own client. Four pieces:

- **`SSRFValidatingTransport`** — https-only scheme allowlist, host blocklist, and
  rejection of any host resolving to a private / loopback / link-local / reserved /
  multicast address (incl. the cloud-metadata IP). Validation lives *in the
  transport* so it sees the resolved IP and re-runs on **every redirect hop**, and
  the validated IP is pinned into the connection (URL rewritten to the IP literal,
  Host header and TLS SNI preserved) to close the resolve-then-connect TOCTOU
  window. Pinning forfeits httpcore's per-address failover, so the transport
  walks the validated addresses itself (IPv4 first — neither dev nor prod has
  IPv6 egress) and moves to the next on a connect-phase error (#561). A pre-flight `urlparse` check is **not** equivalent — it is bypassable
  via DNS rebinding or an attacker redirect.
- **`make_hardened_client`** — pooled-client factory, one client per service
  instance / per crawl.
- **`run_with_retry`** — exponential backoff on transient 5xx/429 and transport
  errors only; never on 4xx, never on an SSRF rejection (`SSRFError` subclasses
  `ValueError`, not `httpx.TransportError`, precisely so the retry policy can't
  sweep it up).
- **`RateLimiter`** — monotonic minimum-interval limiter, **per-process, not
  cross-worker**; Redis-backed coordination is deferred to #277.

The scheme allowlist and host blocklist are module constants, not settings, so
they cannot be loosened from the environment. The fixed-first-party-endpoint
exception is the `gemini_image.py` bullet above.

Image bytes get one more check on top of that transport: `image_storage.py`
derives an upload's content type from the **decoded** bytes (the PIL format),
never from the upstream `Content-Type` header, which is attacker-controlled
(#72). `image/svg+xml` is absent from `FORMAT_TO_CONTENT_TYPE` on purpose — an
SVG served public-read from the CDN can carry `<script>` — so "adding SVG
support" is a stored-XSS change, not a format gap. There is no header-guessing
fallback: an undecodable payload is rejected.

**A 502 from `POST /media-library/media/import-url` is the designed answer to an
unreachable editor-pasted URL, not a server fault.** The handler catches
`httpx.HTTPError` (ConnectTimeout, ReadTimeout after the 60s
`media_http_timeout`, ConnectError) and returns 502 "Could not fetch the image"
(pinned by `test_fetch_failure_is_502`), now with a `logger.warning` naming the
truncated URL; Sentry still reports it because its Starlette integration
captures every 5xx. Three triages in a row (PIC-PYTHON-FASTAPI-21/-22/-23) landed
here: the pasted URL is unrecoverable from the event (#9 scrubs the body), so
check `docker logs` for that warning, not the code. Whether such a URL should be
a 4xx instead is an open operator decision.
