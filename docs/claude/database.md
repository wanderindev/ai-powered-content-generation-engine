# Database: Migrations, Connection Pool, Column Widths

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

### The Migration Chain Starts at a Captured Baseline (#3)
The schema was originally bootstrapped by `Base.metadata.create_all()` in the
Flask era and never had a materialising migration, so the old migrations only
tracked deltas and `alembic upgrade head` against an empty DB could not
reproduce prod. `alembic/versions/0000_baseline_schema.py` (`down_revision =
None`) fixes that by replaying a `pg_dump --schema-only` capture from its
companion `0000_baseline_schema.sql`; prod and existing local DBs were
**stamped** at it rather than re-created. Three consequences:

- **`alembic/versions_archive/` is not part of the chain.** The 21 pre-baseline
  migrations live there, outside Alembic's version path, for history only — its
  README says not to move them back (they reference tables the baseline already
  creates and would no longer chain). A grep for "which migration created table
  X" will hit those files first and mislead you; for anything older than the
  baseline the answer is the baseline SQL, not a migration.
- **A table can exist with no live migration that creates it.** `versions/` is
  the delta history *since* 2026-05-30, not the whole story.
- **The baseline SQL is a pre-cleanup snapshot, so it also misleads in the other
  direction: finding a table there is not evidence it exists today.** It creates
  39 tables, and `0002_drop_flask_orphans.py` (#4) immediately drops nine of
  them — `agents`, `ai_models`, `api_usage`, `approved_languages`,
  `hashtag_groups`, `social_media_accounts`, `social_media_posts`,
  `social_media_post_media`, `translations` — plus five enum types
  (`agent_type`, `platform_type`, `post_type`, `provider_type`,
  `instagram_media_type`) and two `media` residue columns (`external_url`,
  `instagram_media_type`), with `0012` later dropping `edu_material_files`. All
  of it is Flask-era functionality never reimplemented, and `downgrade()`
  recreates the *structure* from `0002_restore_flask_orphans.sql` (the rows are
  gone for good). The trap to know: `social_media_posts` is that residue, **not**
  the Instagram pipeline's table, which is `instagram_posts` (`0023`).
  `users` is the one Flask-era table that *survived* the cleanup — `0001` still
  aligns its timestamp defaults and `0009` widened `users.email` to 254 — and
  `app/models/user.py` still maps it, `password_hash` and all. Nothing reads it:
  no query, no endpoint, no service, no script. There is no password
  authentication anywhere in this codebase (admin access is the magic-link +
  `admin_emails` allowlist gate below), so a `User` model in the tree is not
  evidence of a login to extend.

**A migration's filename and its revision id are hand-written to match, and
`alembic revision` does not produce either.** Every file in `versions/` is
`NNNN_slug.py` with `revision` set to that same stem and `down_revision` to the
previous one's — a readable, sortable chain (`0031_article_audio_manifest_es`
follows `0030_article_audio_url_es`). But `alembic.ini` never had its
`file_template` customized: it is still the timestamped
`%(year)d%(month).2d%(day).2d_%(hour).2d%(minute).2d_%(rev)s_%(slug)s`, and
`%(rev)s` is a random hex. So the documented `alembic revision -m "message"`
emits something like `20260920_1430_a3f9c21b4d0e_message.py` with a matching hex
`revision` — both the name and the id have to be rewritten by hand afterwards,
and the *id* matters because `down_revision` strings are what chain the graph.
Two related facts: the ids are long enough to have broken prod once —
`0003a_widen_alembic_version.py` exists solely because Alembic had created
`alembic_version.version_num` as `varchar(32)` while `0004_educator_verify_code_attempts`
is 34 chars, so the upgrade aborted on `StringDataRightTruncation` and prod sat
at `0002` until a 27-char revision could widen the column to 255 (a letter
suffix is therefore the sanctioned way to insert a migration between two
existing numbers); and the one stem/id mismatch in the tree is deliberate —
`0000_baseline_schema.py` declares `revision = "0000_baseline"`, which is the
string every `down_revision` chain actually starts from. Tests reference
migrations by exact filename (`test_article_slugs.py`, `test_agent_tasks.py`,
`test_edu_guide_grounding.py`), so a renamed file breaks them too.

### Connection Pool (deliberately small — raising it is a human decision)
`app/core/database.py` creates the engine with `pool_size=2` + `max_overflow=3`
(5 connections per uvicorn worker; prod runs `--workers 2`, so this app is capped
at 10) and `pool_timeout=30`, because it shares a managed Postgres cluster with
three other consumers.

**The shared budget, measured 2026-08-08 — use these numbers, not the old ones:**
cluster `<your-db-cluster>` (PG 17, 1 vCPU / **2 GB**, `max_connections =
50`). This app is `defaultdb`; feliu-dev is `feliudev`; atelier-loyalty is
`loyalty`; the managed-agents orchestrator is `orchestrator`. In use at the time
of measurement: **24 of 50** — this app 6, atelier-loyalty 5, feliu-dev 4, ~9
DigitalOcean/background, orchestrator 0. Roughly 26 free.

Note the orchestrator: it holds 0 connections most of the time because it only
connects during a tick (capped at `ORCHESTRATOR_DB_POOL_MAX`, currently 2), which
is exactly why a point-in-time count under-reports it. Count it as a consumer
when budgeting even though it rarely shows up in `pg_stat_activity`.

The cluster was **upsized 2026-07-20** from 1 vCPU / 1 GB / 22 connections. The
old 5 + 10 pool let one app burst to ~30 connections and starve the cluster of
slots for migrations and admin ops — that is the incident this small pool was a
response to, and it happened under the 22-connection ceiling, not the current 50.
Both this file and `database.py` went on claiming "1 GB" for nineteen days
afterwards, which is why four triage runs declined the pool-exhaustion issue
citing a budget they could not verify. Re-measure before reasoning from the
numbers above; do not re-derive them from this paragraph's history.

A SQLAlchemy `Session` pins its connection from its **first statement until
commit/rollback/close**, so any handler or background job that keeps a session open
across multi-second-to-multi-minute external I/O (Anthropic, DeepL, DO Spaces,
Wikimedia/LOC crawling, Playwright) holds one of the 5 slots for that entire duration.
Consequences worth designing around:

- New long-running admin endpoints / job runners should do their external I/O
  **outside** an open session (commit or close before the call, re-open after)
  rather than holding a connection through it.
- When the pool saturates, the error surfaces as `QueuePool limit of size 2 overflow 3
  reached` raised from `validate_admin_token` (`app/api/dependencies.py`) — the
  admin-auth dependency is the first thing to touch the DB on every admin request,
  so it is the *victim*, not the cause. Do not "fix" such a Sentry issue at the
  throw site.
- **`get_db` queues a guarded `db.close()` as the request's first background
  task** (#649): FastAPI ≥0.118 runs a yield-dependency's `finally` only after
  BackgroundTasks finish, which pinned the request connection idle-in-transaction
  through every reindex or job. So never hand a background task the request `db`
  or an ORM object loaded from it — pass ids and open `SessionLocal()` inside.
  `Depends(get_db, scope="function")` is not a substitute: it opens a second
  session alongside the auth dependency's.
- Raising `pool_size`/`max_overflow` is a **human capacity decision** about the shared
  cluster, not an agent-side bug fix (four separate triage runs stopped here). That
  rule stands — but it is now an *informed* decision rather than a blocked one: the
  measured budget above is the fact to reason from, so state the arithmetic against
  it (per-worker × workers × apps vs. 50) instead of declining for want of numbers.

### String Column Widths (issue #27)
Canonical widths for **new** columns:
- **Email** columns → `String(254)` (RFC 5321 max).
- **Entity `name` / `name_es`** (Taxonomy, Category, Zone, Tour, Hotel) → `String(200)`.
- **`Tag.name` / `Tag.name_es`** → `String(100)` (tags are genuinely shorter; English
  and Spanish stay the same width).

The existing tree is **not** uniformly 254: migration `0009_fk_indexes_and_widths.py`
deliberately migrated only the two undersized outliers (`users.email` 120→254,
`booking_status_log.changed_by` 100→254) and left the pre-existing `String(255)`
email columns alone (`magic_links.admin_email`, `admin_emails.email`,
`educators.email`, `contact_submissions.email`, `orders.customer_email`). 255 ≥ 254,
so they are not a defect to "fix" with a cleanup migration.
