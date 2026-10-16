# Observability

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## Observability

- **Sentry** is configured for both backend (`backend/app/main.py`) and frontend
  (`frontend/src/main.jsx`). Both DSNs come from the environment (`SENTRY_DSN`;
  `VITE_SENTRY_DSN` baked in at frontend build time) and init is a no-op when unset,
  so dev/CI/forks stay silent.
- **No tracing is enabled** — `sentry_sdk.init` sets no `traces_sample_rate` and there
  is no DB/pool instrumentation. Triage has stack traces, breadcrumbs and tags only:
  no spans, no DB timings, no cross-request correlation. Don't plan an investigation
  around trace data that will not be there.
- **Events carry no PII** (issue #9). `sentry_sdk.init` sets `send_default_pii=False`
  plus a `before_send=scrub_pii_event` hook (`app/core/observability.py`) that strips
  the request body, cookies, query string, `authorization`/`cookie`/`set-cookie`/
  `x-api-key` headers and any user context; `frontend/src/main.jsx` mirrors it and
  additionally redacts `?token=` out of breadcrumb and request URLs (#36). Exception,
  stacktrace, breadcrumbs, tags, transaction name and request method/URL survive.
  Triage therefore cannot recover which payload, which query params, or which user
  triggered an event — and PII must not be added to Sentry context to make triage
  easier.
- **Breadcrumbs are scrubbed for credential URL parameters only (#576).**
  Sentry's httpx integration records every outbound request's query string as
  a breadcrumb (`http.query`) even with tracing off, and the SDK's default
  scrubber matches dict *keys*, not values. So `scrub_breadcrumb`
  (`before_breadcrumb`, and re-applied by `scrub_pii_event` over the event's
  breadcrumbs) redacts `access_token` / `input_token` / `refresh_token` /
  `client_secret` / `token` `name=value` pairs in breadcrumb messages and data
  — the backend mirror of the frontend's `?token=` redaction. The same
  redaction, plus credential-named dict keys (`input_token`, `authorization`, …),
  `'key': 'value'` pairs in dict reprs and `Bearer …` values, runs over
  exception / thread messages and **stacktrace frame `vars`**: sentry-sdk
  captures frame locals by default (`include_local_variables`) and its own
  scrubber is key-based and non-recursive, so a failure deep in httpx would
  otherwise ship a token in `params` or a request repr. Prefer keeping a
  credential out of the URL and out of exceptions in the first place (the
  Instagram client sends its token as `Authorization: Bearer` — only Graph's
  `debug_token` `input_token` must be a query parameter — and its `_request`
  turns every failure into a token-free, unchained `InstagramPublishError`).
- **Events for `/api/v1/ask` lose every frame's `vars` (#635).** The Ask PIC
  question is free reader text, and FastAPI's own request frames (`app()`,
  `solve_dependencies`) hold the parsed `body` above any frame our code can
  guard, so `scrub_pii_event` strips frame locals from every chained
  exception and thread on any event whose request URL path or transaction is
  that route. The event is never dropped: type, message, frames, tags,
  breadcrumbs, route and method still arrive. So a triage on that route has no
  locals by design. Exception *messages* are not filtered, so don't put the
  question in one. The same leak is why the route's kill switch returns its
  503 instead of raising it (`require_ask_pic_enabled` raises; a raised 5xx is
  an event). A new route that carries reader text belongs in
  `_VARS_STRIPPED_PATHS`; `/mcp` is there too (#639, tool arguments carry
  article / research bodies).
- **Two breadcrumbs carry a raw email today, and the scrubber cannot reach
  them.** Beyond the token-parameter redaction above, `scrub_pii_event` only
  rewrites the event's `request` dict and pops `user`; it does not inspect
  free-text breadcrumb data, which the bullet above records as surviving by
  design (the module docstring's "none of it should land in
  Sentry" is the intent, not an enforced invariant). The two sites are
  `app/api/auth.py` — the `email_not_in_allowlist` branch of `POST
  /auth/admin/request-access` — and `app/services/educator_service.py`, the
  `not_registered` branch of `POST /educators/access`; each attaches
  `data={"event": ..., "reason": ..., "email": ...}`. Note the log and the
  breadcrumb **diverge** at both sites: the `logger.warning` beside each records
  only the event and the reason. So an event from either path does identify a
  user, and a `data={...}` on a new breadcrumb is the one place a well-meaning
  addition silently defeats the #9 guarantee — `add_breadcrumb`, not
  `set_context`, is what to check.
- **Frontend render errors are caught by four boundaries, three of them on the
  money path.** One app-wide `<Sentry.ErrorBoundary fallback={<RootErrorFallback
  />}>` wraps `<App/>` in `main.jsx`, and exactly three pages add their own via
  `Sentry.withErrorBoundary` — `Checkout.jsx`, `OrderConfirmation.jsx`,
  `Contact.jsx`. Any other page's crash therefore falls to the root fallback,
  which renders **outside `<BrowserRouter>`** and so navigates with a plain
  `<a href="/">`, not `<Link>` — it cannot preserve the route. Note the boundary
  itself is a React error boundary regardless of `Sentry.init`, so the recovery
  UI works in dev/CI where the DSN is unset and nothing is reported.
- **cert-watcher** sidecar (`docker/cert-watcher/`) runs daily, probes TLS endpoints, reports to Sentry under cron monitor `cert-watcher`.
- Production logs: `docker-compose logs -f <service>` on the droplet.
- **Only the `app` logger namespace is raised** (issue #424). `_configure_logging`
  in `app/main.py` installs a root stdout handler at **WARNING** and then raises
  `logging.getLogger("app")` alone to `LOG_LEVEL` (default `INFO`), because
  `basicConfig(level=INFO)` would also switch on INFO for httpx, the anthropic
  SDK, botocore and deepl — a real log-volume problem on the droplet. Records are
  gated by the *originating* logger's effective level, so `app.services.*` emits
  and propagates while third-party loggers stay quiet; a `logger.info` under any
  other namespace will not reach `docker logs`. `force=True` is deliberately not
  passed: under pytest the root logger already has handlers and tearing them out
  breaks the suite's caplog assertions. Before #424 nothing configured logging at
  all and every `app.*` INFO line was discarded by `logging.lastResort`.
