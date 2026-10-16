# Panama In Context - Project Constitution

## Project Overview

Panama In Context is a bilingual (Spanish/English) website for a Panama-based business offering:
- **Academic Excursions**: Field trips for schools with pre-visit Zoom, guided tours, post-visit study guides
- **Guided Tours**: Private tours for tourists, families, corporate visitors
- **History Blog**: Long-form articles (~2000 words) about Panama's history

> **Brand/design skill:** A `panama-in-context-design` skill lives at `.claude/skills/panama-in-context-design/` — invoke it ("use the panama-in-context-design skill") whenever building UI, marketing pages, slides/decks, mocks, or prototypes to stay on-brand (tokens, React components, website + slides UI kits, brand assets).

## Architecture → `docs/claude/repo-layout.md`

```
Frontend (React 19 + Vite 6)  →  Backend (FastAPI + PostgreSQL 17)
     ↓                              ↓
  Tailwind v4                    SQLAlchemy 2.0 (sync)
  i18next, Sentry                Markdown, Sentry, SMTP relay (stdlib)
```

Monorepo: `frontend/`, `backend/`, `docker/`, `scripts/`, `backend/scripts/`,
`docs/`. Read `docs/claude/repo-layout.md` for the traps:

- **`backend/scripts/` is a real Python package** of maintenance / backfill /
  eval modules, run as `python -m scripts.<name>` from `backend/` with
  `--dry-run` where it writes; root `scripts/one_off/` is for single-use jobs.
  `auto_approve_candidates.py` is broken history (no `Authorization` header).
- **`ai_generated_content/` is gitignored** — absent in every worktree.
- **`docs/agent-fixes/prompts/fix-issue-<N>.md` + `INDEX.md` are written by an
  agent run; `docs/agent-fixes/agent-friendly-outcomes.md` never is** (gitignored,
  orchestrator-owned — don't commit it).

**`docs/claude/` holds the deep dives, same authority as this file.** Topic
files: `repo-layout`, `tech-stack`, `frontend`, `database`, `config`,
`operations`, `api`, `email`, `background-jobs`, `llm`, `translation`,
`outbound-http`, `observability`, `research-workflow`, `testing-and-ci`, plus
the subsystems `blog`, `booking-and-payments`, `auth`, `agent-driver`,
`instagram`, `media`, `edu`, `fact-check`, `mcp`, `knowledge`. Read the matching file before
changing that area.

## Tech Stack → `docs/claude/tech-stack.md`

Backend: Python 3.12 dev / 3.11 prod image, FastAPI, SQLAlchemy 2.0 **sync**,
PostgreSQL 17 (+pgvector), Pydantic 2, Alembic, pytest. **ruff is pinned
`ruff==0.15.15`** — the format gate is version-sensitive. Integrations: Anthropic
SDK (via `llm.py`), Gemini / Voyage / PayPal over plain `httpx`, ElevenLabs,
DeepL, boto3 (DO Spaces), Playwright **sync**, ffmpeg, stdlib SMTP relay.
Frontend: React 19, Vite 6, **Tailwind v4** (directive syntax differs from v3),
i18next, react-router 7, TipTap 3, AG Grid 35, Sentry.

### Frontend, Build-Time Config and the Admin CMS → `docs/claude/frontend.md`
**Read `docs/claude/frontend.md` before changing the frontend.** The traps most
often hit:

- **No UI string is bundled** — copy (and on tour/excursion pages, the content
  arrays) lives in `frontend/public/locales/{en,es}/translation.json`; edit both.
  A missing array key renders an empty heading silently. Page photos are the
  static `data/tourImages.js` / `excursionImages.js`, not the media library.
- **Every env value is a build-time `VITE_*` arg** (rebuild, not restart), and
  **`VITE_API_URL`'s fallback differs per module — the public modules fall back
  to production** (`https://api.panamaincontext.com`), admin/booking/contact to
  localhost.
- **There is no meta/SEO layer** (no head manager, no prerender) — "add a meta
  description" is new infrastructure.
- **ESLint is a gate** (no frontend test suite): `only-export-components` is why
  every context is two modules; `set-state-in-effect` is why fetch effects use
  an async IIFE. Don't add AG Grid / TipTap to `manualChunks`.
- **The admin CMS is nine grids over one polymorphic `EditDrawer.jsx`**; an
  unknown `recordType` falls to `PlaceholderEditor` silently. The editor
  round-trips markdown → HTML → markdown on every save (tables are dropped) and
  **`ArticleUpdateRequest` is a full replace** — a partial body blanks the
  Spanish article and the tags.

## Database

**Production Database** (Digital Ocean Managed PostgreSQL):
- Host: `<your-db-host>.db.ondigitalocean.com`
- Port: `25060`
- Database: `defaultdb`
- User: `doadmin`
- SSL Required: Yes

**Key Tables:**
- `articles` - Long-form content with series relationships
- `categories` - Article categories
- `taxonomies` - Category groupings (7 total)
- `media` - Feature images

**Excluded Content:** Taxonomy ID 5 (Notable Figures) contains bio articles with quality issues - exclude from API.

### Migrations, Connection Pool, Column Widths → `docs/claude/database.md`
**Read `docs/claude/database.md` before adding a migration or touching the
engine.** The traps most often hit:

- **The chain starts at `0000_baseline_schema.py`** (replays a `pg_dump`);
  `alembic/versions_archive/` is history, not the chain, and the baseline SQL
  includes tables `0002` dropped (`social_media_posts` is Flask residue, not
  Instagram; `users` survives but nothing reads it — there is no password auth).
- **Filenames and revision ids are hand-written `NNNN_slug`** — `alembic
  revision` emits a timestamped hex name you must rewrite; insert between two
  numbers with a letter suffix. Tests reference migrations by filename.
- **The pool is deliberately `pool_size=2` + `max_overflow=3` per worker** on a
  shared 50-connection cluster; raising it is a human capacity decision (state
  the arithmetic). Do external I/O outside an open session; `QueuePool` errors
  in `validate_admin_token` are the victim, not the cause. **Never hand a
  background task the request `db`** — `get_db` closes it first (#649); pass ids
  and open `SessionLocal()`.
- New email columns `String(254)`, entity names `String(200)`, tag names
  `String(100)`; the existing `String(255)` emails are not a defect.

## Coding Conventions

### FastAPI Endpoints
```python
@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
):
    """Docstring explains purpose."""
    # Implementation
```

### Pydantic Schemas
- Request/Response models in `app/schemas/`
- Use `response_model` parameter on endpoints
- Inherit from common base classes when appropriate

### Database Queries
- Use SQLAlchemy ORM, not raw SQL
- Use `joinedload` for eager loading relationships
- Filter on `status == "APPROVED"` for public content

### Status / Type Columns (issue #24)
Every status/type column with a **closed vocabulary** must have a DB-level
constraint and reference a Python enum in `app/models/enums.py` (the single
source of truth). Never add a bare `String` status column. Two sanctioned
patterns, both fed from an enum:

1. **PG `ENUM` type** — only the three pre-existing types
   (`content_status_type`, `media_type_type`, `media_source_type`). Reuse them
   via `SAEnum(*MyEnum.values(), name="content_status_type", create_type=False)`.
   Do **not** create new PG enum types (adding a value needs a fragile `ALTER TYPE`).
2. **`String(N)` + named `CHECK`** — everything else, and **all new** status/type
   columns. Add `status_check("table", "column", MyEnum)` to the model's
   `__table_args__`, and mirror the same `ck_<table>_<column>` constraint in an
   Alembic migration (see `0006_status_check_constraints.py`). Pass several enums
   for a polymorphic column (e.g. the booking status log spans booking + order
   vocabularies). The stable constraint name makes adding a value a clean
   drop-and-recreate migration (`0026`/`0027` each dropped and recreated
   `ck_fact_check_claims_subject_type` to add one `FactCheckSubject` value).

Exception: genuinely **open** provenance fields (e.g. `educators.source`, which
holds UI CTA identifiers that grow over time) stay plain `String` — a closed
CHECK would be the wrong tool. Document the choice in a comment.

The test DB is built from model metadata (`Base.metadata.create_all`), so model
`CheckConstraint`s are enforced in tests; `tests/test_status_constraints.py`
guards model ↔ enum ↔ migration agreement and invalid-insert rejection.

### DateTime Columns (issue #26)
All datetime columns are **timezone-aware** (`timestamptz`). Declare every
`Mapped[datetime]` column with an explicit `DateTime(timezone=True)` type — never
bare (the annotation-derived default is naive `timestamp without time zone`):

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now()
)
```

Application code produces aware UTC with `datetime.now(UTC)` (`from datetime import
UTC, datetime`) — **never** `datetime.utcnow()` (deprecated and naive). Comparing a
naive datetime against an aware column raises `TypeError`, so a stray
`utcnow()` will fail loudly at runtime/tests. The prod DB session is GMT/UTC, so the
0010 migration converted existing naive values with `... AT TIME ZONE 'UTC'` (no
clock shift).

### Error Handling
- Use `HTTPException` with appropriate status codes
- 404 for not found, 400 for bad requests, 500 for server errors

### Rendering Markdown Is the Sanitization Boundary (#111)
`markdown_to_html` (`app/utils/markdown.py`) is the **single** place stored
markdown becomes HTML, for every `content_html` consumer — articles, edu research
and materials, the admin dashboard, the guide PDF. It renders with python-markdown
and then runs `nh3.clean` against a closed allowlist: `_ALLOWED_TAGS` /
`_ALLOWED_ATTRIBUTES` are the union of what the configured extensions emit,
`url_schemes` is `{http, https, mailto}` (which is what kills `javascript:` and
`data:` links), and links get `rel="noopener noreferrer"`.

What is absent is absent on purpose: `<script>`, `<iframe>`, `<style>`, event
handlers and **inline `style`** (tables keep the semantic `align` attribute
instead). So "supporting an embed" or "preserving the author's inline styles" is
a stored-XSS change, not a formatting gap — raw HTML in the markdown source is
exactly the vector #111 closed. Rendering a `content` field to HTML on any other
path re-opens it.

### Configuration & Startup → `docs/claude/config.md`
One pydantic-settings `Settings` (`app/core/config.py`, `@lru_cache`d
`get_settings()`). **Read `docs/claude/config.md` before adding a setting.**

- **`extra = "ignore"` is deliberate** — an unknown env key once crash-looped
  prod. Don't tighten it.
- **Empty credentials disable a feature, they don't crash it** (503 / degrade /
  skip); Instagram publishing also needs its kill switch on. **PayPal is the one
  fail-closed exception** (`_check_paypal_config` at boot).
- **Booking prices and lifecycle timings live in the `pricing_config` DB table**,
  not `.env`; every read has its own hardcoded fallback (an empty table prices at
  defaults silently) and only the 11 price keys are admin-editable.

## Operational Commands → `docs/claude/operations.md`
Read `docs/claude/operations.md` for the full command list and its caveats.

```bash
scripts/run-tests.sh                              # Full suite — canonical, any worktree
scripts/run-tests.sh pytest tests/test_x.py -q    # One file (-k for a name)
scripts/run-tests.sh ruff check app               # Lint gate
scripts/run-tests.sh ruff format --check app      # Format gate
scripts/run-tests.sh --build                      # Force a rebuild (deps changes auto-rebuild, #707)
```

- **An exit 2 "…is EMPTY inside the container" is an environment problem**
  (the daemon cannot see the path), never a pass or a fail.
- **The dev backend's DB is not pinned to the local one** — `DATABASE_URL` can
  be prod; read the env and prefer `--dry-run` before running a writing script.
  `docker-compose up -d backend db` omits MinIO, so check `DO_SPACES_*`.
- **Merging to main deploys automatically** (`deploy.yml` → DO registry →
  Watchtower); the registry is shared and pruned to 2 SHA tags per repo.
  **Deploys do not run migrations** — an un-applied ADD COLUMN breaks *every*
  read of that table (`UndefinedColumn` on an unrelated endpoint = schema drift).
- **`pic-frontend` is the only ingress on the droplet** and also fronts
  feliu.dev and the loyalty app: a broken `nginx.prod.conf` takes all three
  down. Upstreams use nginx variables + Docker DNS deliberately; prod browsers
  call `api.panamaincontext.com` (300s proxy ceiling, cross-origin → CORS);
  `client_max_body_size` is 20M, below the app's own upload caps.

## Shipping

PRs land unattended: pre-PR QA (`personal:pre-pr-qa`), then GitHub auto-merge
once the required checks pass — **Secrets scan (gitleaks)**, **Backend (pytest +
ruff)**, **Frontend (build)**. The `personal:ship` skill then follows this
section. Merge only with `gh pr merge <N> --auto --merge --delete-branch`; never
`--admin`, never the REST merge endpoint.

**Before the PR** (from the worktree): `scripts/run-tests.sh` (full suite,
always — a shared fixture/auth helper change breaks tests far from the diff), `scripts/run-tests.sh ruff
check app`, `scripts/run-tests.sh ruff format --check app`; for `frontend/`
changes `npm ci && npm run lint && npm run build` in `frontend/`. An exit 2
"…is EMPTY inside the container" is an environment problem, not a result.

**What merging does**: green CI on `main` triggers `deploy.yml`, which builds
only what the merge touched (`pic-backend` for `backend/` + `docker/backend/`, `pic-frontend` for
`frontend/` + `docker/frontend/`); a docs/scripts-only merge builds nothing and
its skipped deploy jobs are expected. Watchtower on the droplet
(`wanderindev@<droplet-ip>`, compose dir `~/pic`) polls every 300s.

**Verify the deploy** (read-only, all allowed unattended):
1. `gh run list --workflow deploy.yml --branch main -L 3` → wait for the run on
   the merge commit to succeed; its log's `pushing manifest … sha256:` line is
   the pushed digest.
2. Within ~5 min, the container should run it:
   `ssh wanderindev@<droplet-ip> 'docker image inspect --format "{{index .RepoDigests 0}}" $(docker inspect --format "{{.Image}}" pic-backend)'`
   (or `pic-frontend`). Same digest = deployed.
3. `curl -s https://api.panamaincontext.com/api/v1/health` → `"healthy"`;
   `ssh … docker logs --since 10m pic-backend` shows no startup traceback.
   For a frontend change, load `https://panamaincontext.com`; if
   `docker/frontend/nginx.prod.conf` changed, also `https://feliu.dev` and
   `https://loyalty.atelierm.store` (pic-frontend is the shared edge).

**Migrations**: if the PR added a file under `backend/alembic/versions/`, apply
it as soon as `pic-backend` runs the new image (the code is already live, so
every minute un-applied is the 0031 failure mode above):
`ssh wanderindev@<droplet-ip> 'cd ~/pic && docker compose -f docker-compose.prod.yml run --rm backend alembic upgrade head'`,
then confirm `docker exec pic-backend alembic current` shows the new head.
The routine step is an upgrade; a `downgrade` or `stamp` is a deliberate
recovery action, never a side effect. A CHECK-vocabulary migration (new
job type / status value) 500s its kickoff endpoint in that window; that is
expected and ends with the upgrade.

**No operator gate on production for this repo.** Agents may write to the
production DB, run any data script or alembic command, and change droplet /
DigitalOcean state, including `.env` edits, restarts and branch deploys. The
operator accepts the risk; recovery is a redeploy or a point-in-time restore.
Still say what you did in the ship report, and name any new env key in the PR
body.

## API Structure → `docs/claude/api.md`
Base URL `/api/v1/`; routers registered in `backend/app/main.py` — see
`docs/claude/api.md` for the router table. The traps most often hit:

- **"Admin" is three routers at three prefixes**: `/admin` (`admin.py`, the
  pipeline-step translate / grammar endpoints), `/admin/dashboard`
  (`dashboard.py`, the CMS, plus `knowledge.py` / `mcp_admin.py`), and
  `/admin/bookings` (`booking_admin.py`, plain `validate_admin_token` — the
  driver token can never reach it).
- `/health`, `/api/v1/health`, `/` and `/static` are declared inline in
  `main.py`; `/docs` and `/redoc` are public in production (the schema, not the
  routes).
- **Nothing rate-limits inbound requests** (app or nginx) except Ask PIC's own
  ledger-based limiter (`ask_pic_controls.py`); "add throttling" is new code.
  Use `GET /api/v1/ask/status` to learn whether Ask PIC is on — never probe the
  POST.

### Blog Articles — Visibility, Publishing, Slugs, Generation, Narration, Grammarly → `docs/claude/blog.md`
Detail moved out of this file; **read `docs/claude/blog.md` before changing
this area.** The traps most often hit:

- **"Public" is three filters and only `GET /articles` and `/{id}/related` apply
  all of them** (`APPROVED` + `published_at <= now()` + taxonomy-5 exclusion); `/search`,
  `/articles/{id}`, `by-slug`, series blocks and category counts filter less.
  `EXCLUDED_TAXONOMY_ID` is one shared constant in `app/models/category.py`
  (#626, guard-tested) — import it, but a new router still has to apply it.
- **`GET /articles` sorts by historical chronology, and `BlogList.jsx` groups
  consecutive items by `suggestion_id`** — re-sorting at either end shatters the
  groups. Reorder after grouping, never before.
- **Publishing is a write-side gate** (`PATCH .../publish` 400s without
  APPROVED, `content_es`, a feature image and, in a series, the About marker);
  with no `published_at` the server picks the series' shared slot (#558);
  blog articles are fact-checked but **not** fact-check-gated.
- **Article slugs are persisted and freeze once live**; collisions raise
  `SlugCollisionError` (no `-2` suffixing). Category slugs are still computed.
- **Generation**: outline is never condensed; condense/outline/content on Opus;
  tag cap and no-bold/italics are enforced in code, not just the prompt.
- **Narration is Spanish-only**, timestamped final key + content-hash segment
  keys, and generation is an `ARTICLE_NARRATION` background job (202 + poll).
  Segments are post-processed (blip trim, loudness, pace) through disk before
  concat; the stored segments stay raw and the manifest holds processed
  durations (#547). Every run pitch-checks the raw clips and re-takes
  newly synthesized ones > +15 % over the article baseline (#601,
  `articles.audio_qa_es`); the estimator and thresholds are calibrated
  together, so don't "improve" one without re-measuring.
- **The Grammarly flow is body-only**, fingerprint-guarded, and `stamp=False`
  writes corrections without marking the article checked.

### Tour Pricing, Checkout, Payments and Customer Order Links → `docs/claude/booking-and-payments.md`
Detail moved out of this file; **read `docs/claude/booking-and-payments.md` before changing
this area.** The traps most often hit:

- **`Tour.fixed_price_config` flips three behaviours at once** (flat pricing,
  availability check skipped, `allow_multiple_per_slot=True`) — it is not a
  price override. `POST /bookings/calculate-price` never consults it.
- **A private-tour price comes from three DB sources**: the `pricing_config`
  ladder, `attractions` ticket columns (resident vs tourist), and
  `zone_transport_rates` for 1–4 pax — where a missing zone pair ships **$0
  transport** with `transport_quote_required`, not an error.
- **Double-booking is resolved by a partial UNIQUE index** on
  `(tour_date, time_slot)` — one global AM/PM calendar, not per tour. Slot holds
  expire in the availability query but **not** in the index, and nothing sweeps
  them. The 90-day availability range is up to ~360 queries on one connection.
- **The public tour catalogue is hand-written React**, not the `tours` table.
- **The cart is client-side only and the client cannot send a price** —
  `create_order` reprices everything; one order = the whole cart.
- **PayPal is the only integrated method** (CUANTO is the default and manual).
  `apply_paid_transition` is the single paid transition (caller holds
  `with_for_update(of=Order)`); outbound PayPal writes go through
  `_commit_or_capture_orphan`.
- **Customer order links** use their own validator (`_validate_magic_link`) and
  carry the token in the URL **fragment**, like the admin link.

### Admin & Educator Authentication → `docs/claude/auth.md`
Detail moved out of this file; **read `docs/claude/auth.md` before changing
this area.** The traps most often hit:

- **Admin is `Authorization: Bearer` only**: a magic link joined against an
  **active** `admin_emails` row; `?token=` was removed (#263); the `dev-token`
  bypass needs `debug` **and** `environment == "development"`.
- **Links carry the token in the URL fragment**; logout deletes the row.
- **Silent traps**: `request-access` matches email **case-sensitively** while
  the allowlist create de-dupes case-insensitively, and `ADMIN_EMAILS` in the env
  is dead config — both fail invisibly behind the no-enumeration response.
- **Frontend**: `authFetch(token, …)` threads the token explicitly, nothing
  handles 401, the token lives in `sessionStorage`, `import.meta.env.DEV`
  bypasses the bootstrap, and every admin route must stay `lazy()`.
- **Educator gate is removed**; `/educators/access`'s `granted` enumeration
  oracle is deliberate (#58); one `educators` row carries gate access *and* the
  double-opt-in list (PENDING forever is correct); there are **two** unsubscribe
  endpoints with different scope.

### Transactional Email → `docs/claude/email.md`
`app/services/email_sender.py` is the single transport and **raises
`EmailDeliveryError`** (never a silent bool); `notifications.py` propagates
customer-facing failures and absorbs admin-side ones. Bodies are autoescaped
Jinja2 templates — **except `app/api/contact.py`'s f-string HTML body**, which
does not escape. Read `docs/claude/email.md` before adding a sender.

### Weekly-Series Agent Driver (epic #422) → `docs/claude/agent-driver.md`
Detail moved out of this file; **read `docs/claude/agent-driver.md` before changing
this area.** The traps most often hit:

- **`plan_week` is an insert-only ensure-pass**; `weekly_plan_daily_cadence`
  switches the goal key between `weekly_series:YYYY-Www` and
  `daily_series:YYYY-MM-DD` — don't hardcode the prefix.
- **A new goal is capped by `weekly_plan_max_in_flight`** (#559): at the cap
  plan-week answers 200 `at_capacity: true` (not 404); expansion
  (`PLAN_ARTICLES`) and an explicit `research_id` are never capped, and a goal
  with a `FAILED` row holds its slot until skipped from the admin queue.
- **`SKIPPED` satisfies a dependency exactly like `DONE`; `FAILED` never
  cascades; the lease sweep runs at the top of `next`.** Steps blocked on the
  operator are wait kinds that report `WAITING` (attempt refunded, re-claimable
  after `next_check_at`), not `FAILED` (#553). Stalled weeks are
  unstuck via `POST .../agent-tasks/{id}/action` (`retry` resets `attempts`).
- **Every `KIND_CALLS` entry needs an anchored, method-qualified regex in
  `DRIVER_ALLOWED_CALLS`** (bound by `tests/test_agent_task_driver.py`).
- **A driver-called endpoint must converge on replay** — return the existing
  result with 200, not 400 "already done" (#495).

### Instagram — Generation, Scheduler, Publishing → `docs/claude/instagram.md`
Detail moved out of this file; **read `docs/claude/instagram.md` before changing
this area.** The traps most often hit:

- **Generation writes PENDING drafts only**; the parser drops malformed blocks
  rather than inventing, and `sanitize_fact` is a sanitization boundary.
- **The scheduler is the only thing that acts at a future time**; it claims one
  row at a time with `FOR UPDATE SKIP LOCKED`, and **FAILED rows are never
  re-picked** — don't add automatic retry.
- **Publishing needs a public JPEG URL** from the renderer (timestamped key),
  containers are created at publish time (24h expiry), `publish_post` never
  raises, and publish requires APPROVED + rendered.
- **Publishing is off by default** (`instagram_publishing_enabled`, #576 — an
  agency runs the account); `POST .../instagram/verify` exercises the real
  token/account/container without ever calling `media_publish`. A promo's
  schedule time is derived from its article's `published_at`.

### Long Admin Work Runs Off the Request Path → `docs/claude/background-jobs.md`
Anything that would outlive the proxy timeout is a **`crawl_jobs` row +
FastAPI `BackgroundTask`** returning 202 + job id. **Read
`docs/claude/background-jobs.md` before adding a job type.** Traps:

- `crawl_jobs` is the generic job table despite its name — don't add another.
  A new type is a drop-and-recreate of `ck_crawl_jobs_job_type`, `job_type` is
  `String(20)`, and the migration must be applied right after deploy.
- The runner opens its own `SessionLocal()`, reports via `progress(...)`, and
  **never raises**.
- In-flight refusal differs by caller: crawls 409 per type; fact-check joins
  (dashboard) or 409s (edu); edu jobs join an identical request and 409 others.

### Media Catalog and Article Feature Images → `docs/claude/media.md`
Detail moved out of this file; **read `docs/claude/media.md` before changing
this area.** The traps most often hit:

- **`media_candidates` is the crawl inbox; approving creates the `Media` row**
  (and APPROVED with `media_id` NULL is possible after an upload failure).
- **`/public-media` INNER JOINs the candidate** and filters the *candidate's*
  tags, not `Media.tags`. **`Media.search_terms` is written at import / PATCH /
  approve time by `app/services/media_metadata.py`** (best-effort, NULL on
  failure) and by `scripts.backfill_search_terms` for the rows that missed it.
- **Media search is ranked full-text over stored generated `search_vector`
  columns** (`app/services/media_search.py`, one helper for all three search
  endpoints). The columns are on the `Table` but **not ORM attributes**
  (`exclude_properties`; `deferred` was not enough — it still renders inside
  the `select(Model).subquery()` count idiom), so only the search helper names
  them, via `__table__.c`, and an un-applied 0041 breaks only search. Never map
  them. A new searchable field means editing
  `app/models/search_sql.py` **and** a new migration.
- **`image-proxy` is the one media fetch that bypasses `http_client.py`.**
- **Feature images live on the `backend_static` volume** with a relative
  `/static/...` `file_path`; consumers must branch on `startsWith('http')`.

## Knowledge Index → `docs/claude/knowledge.md`
**The vector stage scans HNSW iteratively** (`hnsw.iterative_scan =
relaxed_order`, `SET LOCAL`, #722): without it `ef_search` applies before the
`WHERE`, filtered-out and dead entries starve the pool, and Ask PIC silently
abstains (#711). On pgvector < 0.8 it warns once and falls back. The indexer
never rewrites an unchanged row (#728), and `scripts.reindex_knowledge`
vacuums the table itself past 1,000 dead tuples; background reindexes don't.

## LLM Access Layer → `docs/claude/llm.md`
**Every Anthropic call goes through `create_message` in
`backend/app/services/llm.py`** — never `client.messages.create` directly (that
includes `backend/scripts/`). The seam owns model ids (`SONNET_MODEL` /
`OPUS_MODEL` / `HAIKU_MODEL`, all bare 5.5 ids with no dated snapshot), prices,
usage logging and the budget backstop. **Read `docs/claude/llm.md` before
adding or changing a call site.** The traps most often hit:

- **Thinking tokens count toward `max_tokens`**; `reject_on_truncation` defaults
  to `True`. Give headroom and set `output_config={"effort": ...}` explicitly.
- **Never index `message.content[0]`** — use `first_text()` / `parse_llm_json()`.
- `temperature` / `top_p` / `top_k`, `budget_tokens` thinking and prefill are
  400s on all three models; effort is the only thinking control.
- **`max_tokens >= 8000` streams with a 600s timeout; below it is 120s.**
- The budget backstop is per-worker, input-only, never resets, and counts
  cache writes but not cache reads. Prompt caching is opt-in per call site
  (`cached_text` / `cache_control`, #716). Refusals raise `RefusalError` after
  a server-side fallback (none for Haiku).
- Opus 5.5 runs the blog article pipeline and every edu generation call
  (`EDU_MODEL`); everything else is Sonnet 5.5 deliberately. Changing a model or
  a calibrated prompt (grammar verdicts) is an evidence-backed decision.
- Gemini feature images deliberately sit outside the seam (plain `httpx`), but
  their prompt comes through it.

## Translation → `docs/claude/translation.md`
Every DeepL call in `app/` goes through `TranslationService`
(`app/services/translation.py`, `ES-419`, no retry wrapper — the SDK retries).
**Read `docs/claude/translation.md` before changing translation.** Traps:

- Pick the method by content: `translate_text` (plain strings),
  `translate_markdown` (markdown round-trip), `translate_blocks` (structured
  guides). Markdown through `translate_text` mangles markup.
- All three normalise the Spanish thousands separator (`normalize_es_thousands`).
  An optional EN→ES glossary rides on the calls when enabled.
- **`translate_blocks` has no default branch** — a new `GuideBlockType` ships in
  English silently. It is one of **four** per-type dispatches (also
  `guide_pdf._block_to_frag`, `blocks_to_markdown`, `GuideBlocksEditor.jsx`),
  all fail-soft; a new block type needs a branch in each.
- `backend/scripts/translate_frontend.py` writes the locale JSON, fills only
  absent/blank ES keys, and needs an interactive `yes`.

## Outbound HTTP to Semi-Trusted Hosts → `docs/claude/outbound-http.md`
**`app/services/http_client.py` is the single seam for crawler / media
fetches** (`SSRFValidatingTransport`, `make_hardened_client`, `run_with_retry`,
per-process `RateLimiter`); a new call site belongs on it. Validation lives in
the transport and re-runs on every redirect hop — a pre-flight `urlparse` check
is not equivalent. Upload content type comes from the decoded bytes, and SVG is
excluded on purpose (stored XSS). A 502 from `import-url` on an unreachable
pasted URL is designed behaviour, not a bug. Read
`docs/claude/outbound-http.md` before adding a fetch.

## Edu Content Generation, Media Production, Taxonomy and Public Deliverables → `docs/claude/edu.md`
Detail moved out of this file; **read `docs/claude/edu.md` before changing
this area.** The traps most often hit:

- **`edu_materials` is a discriminated union** (generated → `research_id`,
  uploaded → `blog_research_id`); guide bands commit independently, and a
  band-less suggestion is a **silent no-op**.
- **Decks are two-stage** (content, then narration against the final slides +
  the persisted briefing); `force` on slides replaces every row.
- **Four long steps are `crawl_jobs` background jobs, and anything running
  Playwright must stay a sync `def`.**
- **Rendered artifacts overwrite deterministic Spaces keys** where `{band}` is
  slugged (`4th_8th`), and the public academic page hardcodes two of them.
- **Brand assets are vendored** into `backend/app/slide_assets/` and
  `ig_assets/` — editing the design skill changes no render.
- **`app/core/excursions.py` is the taxonomy**; key mappings are irregular and a
  band id must be spelled identically in six places (unknown bands fall back
  silently).
- **Public deliverables: publish the slides deck; the video is inherited.**

## Fact-Check Pipeline (epic #373; edu extension #459) → `docs/claude/fact-check.md`
Detail moved out of this file; **read `docs/claude/fact-check.md` before changing
this area.** The traps most often hit:

- **The subject decides the tier**: external (`SUGGESTION` / `RESEARCH` /
  `EDU_RESEARCH`, extract-only + deep research; the web-search tier is gone,
  #751) vs grounding
  (`ARTICLE` / `EDU_MATERIAL`, against the full research). Malformed output
  degrades to UNVERIFIABLE / NEEDS_DEEP_RESEARCH, never VERIFIED. Grounding's
  `ABSENT` maps per subject (#555): `NEEDS_DEEP_RESEARCH` for an article,
  `UNVERIFIABLE` for an edu material.
- **`EduMaterial.content` is a derived projection** — read and write the real
  container (guide blocks / slide rows) and call `invalidate_material_claims`
  on any rewrite.
- **Gates**: `INACCURATE` blocks every gated step, NEEDS_DEEP_RESEARCH /
  DISPUTED only public ones (409); soft warnings live in
  `frontend/src/hooks/useFactCheck.js`.
- **`CORRECTED` comes only from the preview → apply FIND/REPLACE flow**; the
  verified-claims KB takes only externally resolved verdicts, and its
  `claim_key` normalization is mirrored in the `/fact-check-process` skill SQL.

## MCP Server for LLM Agents (epic #620) → `docs/claude/mcp.md`
Detail lives in the topic file; **read `docs/claude/mcp.md` before changing
this area.** The traps most often hit:

- **`mcp` 2.x, not 1.x**: `MCPServer` (`mcp.server.fastmcp` only raises),
  wire types in `mcp_types`, and the SDK's HTTP client is `httpx2`. Its
  constructor calls `logging.basicConfig(INFO)`; `build_mcp_server` undoes
  that so #424's root-at-WARNING config survives.
- **`/mcp` is two exact routes, not a `Mount`** (`mount_mcp`), plus the RFC
  9728 metadata URL at the host root; stateless + JSON, so nginx is unchanged.
- **The SDK session manager runs once per instance**: `mcp_http.run()` in the
  lifespan rebuilds it each entry; outside a running lifespan `/mcp` is 503.
- **Own token table** (`mcp_tokens`, sha256, `scopes` array CHECK via
  `array_subset_check`), minted by `python -m scripts.mcp_tokens`; separate
  from admin and driver auth. Any valid token connects; per-tool scope via
  `pic_tool` / `require_scope` (an `insufficient_scope` tool error), which
  also wraps every tool in the `mcp_tool_calls` audit log (#639).
- **Every tool opens and closes its own `SessionLocal()`** and never holds it
  across external I/O; sync tools run in a worker thread.
- **Read tools (#640) live in `app/mcp/tools/`** and are registered from
  `READ_TOOLS` in `server.py`: search/related use the retrieval service's
  `internal` visibility (APPROVED incl. scheduled, any taxonomy, no drafts),
  `get_article` returns any status, the pipeline tools read `agent_tasks` only
  and merely *suggest* retry/skip. Every result goes through `bound_result`
  (≤ 20k chars, `truncated` flag).
- **Pipeline write tools (#641, `PIPELINE_TOOLS`) call their HTTP twin's
  service function, never a copy**; every submit converges on replay
  (`replayed: true`); retry/skip reasons go to `agent_task_actions`. The
  retry/skip tools refuse (#686, `MCP_REFUSED_TASK_ACTIONS`) to skip a `WAIT_*`
  gate, a fact-check / corrections / grammar step, or a PENDING row with an
  unfinished dependency (#690), or to retry/skip
  `APPROVE_ARTICLE` / `SET_PUBLISH_DATE` / `APPROVE_IG_POST` /
  `SCHEDULE_IG_POST`; only the admin queue can.
- **Clients use the checked-in `.mcp.json`** (#643): server `pic`, bearer
  `${PIC_MCP_TOKEN:-}` — the `:-` default is load-bearing (an unset var with
  no default makes Claude Code reject the config; empty just 401s). Never put
  a token literal in the repo (gitleaks scans the tree).
- **Publish / unpublish / approve are requests, never actions (#642).** The
  `publish_request` tools only insert `mcp_approval_requests` rows; an admin
  approves on `/admin/approvals`, which runs `app/services/article_publishing.py`
  (the same functions `PATCH .../publish`, `.../unpublish` and `.../status`
  call) in a savepoint under a row lock, so a failed precondition is `FAILED`
  with nothing half-applied and a double approve executes once.

## Observability → `docs/claude/observability.md`
**Read `docs/claude/observability.md` before triaging or adding Sentry context.**

- Sentry (backend + frontend) no-ops when the DSN is unset. **No tracing** —
  stack traces, breadcrumbs and tags only.
- **Events carry no PII (#9)**: body, cookies, query string, auth headers and
  user context are scrubbed, credential URL params are redacted from
  breadcrumbs and frame `vars`, and `/api/v1/ask` and `/mcp` events lose every
  frame's `vars`. Never add PII to make triage easier; check `add_breadcrumb`
  `data={...}` — two sites already leak an email there.
- **Only the `app` logger namespace logs at INFO**; root is WARNING, so a
  `logger.info` outside `app.*` never reaches `docker logs`.

## NotebookLM and the Research Workflow → `docs/claude/research-workflow.md`
The `nlm` CLI and `/nlm-*` skills drive NotebookLM. Article research is the
`/research-prompt` → Claude deep research → `/research-process` loop, driven by
`scripts/research-cron.sh` from **file counts** in `ai_generated_content/` (one
prompt in flight; a stray `.md` stalls it). The fact-check pair
(`/fact-check-prompt`, `/fact-check-process`) is hand-run but also has an HTTP
mirror (`.../fact-check/deep-research/generate-prompt` and `.../verdicts`).
Read `docs/claude/research-workflow.md` before changing either loop.

## Agentic Protocols

### Memory
If you learn a new persistent pattern about this codebase, record it with Edit —
but **keep this file at or under 40,000 chars** (`wc -m CLAUDE.md`; it was
split twice for exceeding its limit, last on 2026-10-12 from ~124k). Short
cross-cutting rules and stubs for every area belong here; detail about one
subsystem goes in its `docs/claude/<topic>.md` file (add a new topic file plus a
short stub here if none fits), and a stub here should only gain a bullet when
the trap is one an agent must know before opening the topic file.

### Harness, Testing and CI → `docs/claude/testing-and-ci.md`
**Read `docs/claude/testing-and-ci.md` before changing tests, fixtures, hooks
or CI.** The rules most often hit:

- **The checked-in hooks silently no-op in an agent worktree** — a passing Stop
  hook is not evidence the suite ran; `scripts/run-tests.sh` is.
  `permissions.deny` blocks bare `alembic upgrade/downgrade/stamp`, force-push,
  `git reset --hard`, `rm -rf`, and every `.env` edit.
- New endpoints MUST have tests; mock external services. **`test_db` commits
  for real and TRUNCATEs at teardown** — don't restore rollback isolation. Seed
  through the chained factory fixtures. `conftest.py` forcing
  `ENVIRONMENT=test` is load-bearing; no test can send real email.
- **No frontend test suite** — eslint + build are the only gates. Binary-backed
  render tests skip silently in CI.
- **Never pin a migration as the newest or as the whole enum**;
  `test_edu_guide_grounding.py` pins the sole head by name and needs updating
  with every migration.
- All code must pass `ruff check` **and** `ruff format --check`. CI is three
  jobs — gitleaks (working tree, explicit path allowlist), backend, and
  **"Frontend (build)" (never rename it)**. `requirements.txt` floats with no
  lockfile, so a backend CI failure on untouched code may be a dependency move.
  Main runs are never cancelled (each one deploys).

### Tool Preferences
- Use `Grep` over `Read` for searching large files
- Use the Task tool with Explore agent for open-ended codebase questions
- Prefer editing existing files over creating new ones
