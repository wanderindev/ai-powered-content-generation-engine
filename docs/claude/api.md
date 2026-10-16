# API Structure

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## API Structure

Base URL: `/api/v1/`

Routers registered in `backend/app/main.py`, grouped by domain:

| Router | Surface |
|--------|---------|
| `articles`, `categories`, `search` | Blog content (public) |
| `tours`, `zones`, `bookings`, `orders` | Tour booking flow (public + checkout) |
| `booking_admin`, `dashboard` | Admin views for bookings & ops |
| `edu`, `public_edu`, `media_library`, `public_media`, `educators` | Educational excursions (admin + public `/public-edu`), media library, educator access gate |
| `instagram` | Instagram post pipeline & scheduling admin (`/admin/dashboard/instagram`) |
| `agent_tasks` | Weekly-series task graph — plan-week, driver claim/report, admin queue (`/admin/dashboard/agent-tasks`) |
| `mcp_admin` | MCP tool-call audit log read, `GET /admin/dashboard/mcp/calls` (#639; not on the driver allowlist) |
| `mcp_approvals` | MCP approval queue, `/admin/dashboard/mcp/approvals` list + `{id}/approve` / `reject` (#642; plain `validate_admin_token`, so the driver token can never reach it) |
| `auth`, `admin` | Admin authentication and CMS |
| `contact`, `subscribe` | Forms & mailing list |
| `ask` | Ask PIC public Q&A, `POST /ask` (#635; 503 until `ask_pic_enabled`) |
| `webhooks` | Inbound webhooks (PayPal) |

See `backend/app/api/*.py` for the exhaustive endpoint list within each router.

**"Admin" is three routers at three prefixes, and the table's grouping is by
domain, not by module.** Know which one a new admin endpoint belongs to before
adding it:

- **`/admin` — `admin.py` (~520 lines) is the *pipeline-step* router, not the
  CMS.** Its whole surface is the per-entity and bulk DeepL translate endpoints
  (article title / excerpt / content, taxonomy, category, suggestion title,
  `/translate/taxonomies/all`, `/translate/categories/all`), the series-sections
  status + generate pair, and `POST /articles/{id}/grammar-check` plus `POST
  /articles/{id}/es-accuracy-check` (#557) — the two
  `DRIVER_ALLOWED_CALLS` entries that are not under `/admin/dashboard`.
- **`/admin/dashboard` — `dashboard.py` (~2900 lines) is the CMS**: suggestions,
  research, outlines, articles, publish/unpublish, feature images, narration,
  fact-check kickoff and the deep-research prompt/verdict pair, the editorial
  calendar, tags, `settings/pricing` and `settings/admin-emails`. Smaller
  modules share the prefix with the same dependency: `knowledge.py`
  (`/admin/dashboard/knowledge`) and `mcp_admin.py` (`/admin/dashboard/mcp`).
- **`/admin/bookings` — `booking_admin.py`** is the order/invoice surface, and
  it is the one of the three gated by plain `validate_admin_token`; the other
  two take `validate_admin_or_allowlisted_driver_token`, so the driver token can
  never reach a booking or payment endpoint whatever the allowlist says.

Four routes sit **outside** that prefix, declared inline in `main.py` (plus
the MCP server's `/mcp` and `/.well-known/oauth-protected-resource/mcp`,
added by `mount_mcp`, #638): `/health`
and `/api/v1/health` (the latter is what `docker-compose.prod.yml`'s healthcheck
polls, and what the frontend's `depends_on: service_healthy` waits for), `/`
(a name/version blurb), and the `/static` mount that serves article feature
images off the `backend_static` volume. `docs_url` / `redoc_url` are set to
`/docs` / `/redoc` with **no environment gate**, so the full OpenAPI schema —
every admin and driver route included — is browsable in production. The routes
are still token-gated; it is the *schema* that is public, so don't treat an
endpoint's obscurity as part of its protection.

**Nothing rate-limits inbound requests, at either layer.** `main.py` adds exactly
one middleware, `CORSMiddleware`; there is no slowapi/limiter dependency and no
per-endpoint throttle anywhere in `app/` (every 429 in the backend is *outbound* —
Gemini quota, or the crawler retry policy). `docker/frontend/nginx.prod.conf` has
no `limit_req` / `limit_conn` either, and it is installed as
`conf.d/default.conf` over the stock `nginx.conf`, so no zone is declared above
it. The `RateLimiter` in `http_client.py` throttles *our* calls to Wikimedia and
LOC; it is not an ingress control. So the unauthenticated write surfaces —
contact, subscribe, `educators/access` + `signup`, `auth/admin/request-access`,
`orders` — are open to repetition, and "add throttling" is new code or new nginx
config, not a setting.
The one exception is Ask PIC (`POST /ask`, #634/#635): `app/services/ask_pic_controls.py`
rate-limits and USD-caps it by `COUNT`/`SUM` over its own `ask_pic_queries`
ledger under an advisory lock (cross-worker), keyed by an HMAC of `X-Real-IP`
that rotates daily. It is per-feature, not ingress control — reuse its shape,
not its table, for another public endpoint. Besides `POST /api/v1/ask` (#635)
the `ask` router has `GET /api/v1/ask/status` → `{enabled,
max_question_chars}` (#636): settings only, no DB, no limit counted. It is
how the frontend hides its Ask PIC entry points (blog list, article page)
while the kill switch is off — never probe the POST for that, it is
rate-limited and logged. The page (`/ask` EN, `/preguntar` ES, lazy) still
renders "not available" on a 503, and renders `answer` as a plain text node.
