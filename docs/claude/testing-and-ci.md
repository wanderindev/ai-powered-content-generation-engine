# Harness, Testing and CI

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

### Harness Configuration (`.claude/settings.json`)
The repo checks in two hooks, so some things happen to your edits that you did
not do yourself:

- **PostToolUse on `Edit` → `.claude/hooks/format-python.sh`.** Runs
  `ruff format` + `ruff check --fix` on the edited file, but only for paths
  matching `*backend/*.py`, and only *inside the running backend container*
  (it rewrites the host path to `/app/...`). Frontend, docs and non-backend
  Python are untouched. Best-effort: it always exits 0.
- **Stop → `.claude/hooks/quality-gate.sh`.** Blocks task completion by exiting
  1 when `ruff check /app` or `pytest -x -q` fails, and re-runs up to
  `MAX_RETRIES=5` — after the 5th failure it prints "MAX RETRIES EXCEEDED" and
  **exits 0 anyway**, so a session can end green on code that never passed.

Both hooks gate on `docker-compose ps backend` reporting `Up`. In an agent
worktree the compose project name is the worktree directory, so the operator's
stack does not count and **both hooks silently no-op** — the quality gate then
prints "Docker not running - skipping tests" without setting an error (it does
still fall back to a local `ruff check .` if ruff is on PATH). A passing Stop
hook is therefore not evidence the suite ran; `scripts/run-tests.sh` is.

`permissions.deny` blocks a small set of Bash commands outright, all of them
about local git history and the working tree: every force-push form,
`git reset --hard`, `git clean -fd/-fx`, `git filter-branch`, `git rebase -i`,
`rm -rf`, plus DO credential literals (`*AVNS_*`, matched anywhere — use
`$DATABASE_URL`). Alembic in any direction and `.env` / secrets edits are **not**
denied (removed 2026-10-12, with the operator gate in Shipping). Merging and
pushing to `main` are not repo denies: the user-level `personal` plugin
(github.com/wanderindev/claude-personal) allows only `gh pr merge --auto` and
blocks direct pushes to main, and branch protection requires a PR with green
checks. Production DB writes and droplet changes are allowed by the user-level
auto-mode policy. Pushing an image to the DO registry is `ask`, not `deny`.
The matching `allow` list: `Edit(CLAUDE.md)`, `Edit(docs/claude/**)` and
`Edit(.claude/**)` are pre-approved, as is `docker build`.

### Testing Requirements
- New endpoints MUST have corresponding tests
- Tests should use the PostgreSQL test container (see conftest.py)
- Mock external services (PayPal, SMTP relay, DO Spaces, DeepL, ElevenLabs TTS,
  LLM providers)
- **The `test_db` fixture is not rollback-isolated.** It commits for real and the
  database is reset with `TRUNCATE ... RESTART IDENTITY CASCADE` at teardown — a
  deliberate design (#205 a service-layer `db.rollback()` no longer wipes seeded
  rows, #233 ids no longer drift across tests, #140 `onupdate=func.now()` is
  observable). Do not "restore" a single-transaction + rollback fixture.
  `admin_token` seeds an active `AdminEmail` alongside the `MagicLink` and sets the
  `Authorization` header on `test_client`; a hand-rolled token that skips the
  allowlist row will 401. `asyncio_mode = "auto"` is set in `pyproject.toml`, so
  async tests need no marker. DB objects the models depend on but `create_all`
  cannot make (the `unaccent` / `pg_trgm` / `vector` extensions and the
  `pic_unaccent` / `pic_array_to_text` functions) are created in `test_engine`
  (the first two plus the functions from `app/models/search_sql.py`) before
  `create_all`.
- **`conftest.py` forces `ENVIRONMENT=test` at import time, and that line is
  load-bearing.** It is set *before* `app.main` is imported, because main's
  module-level `sentry_sdk.init` is gated on `sentry_dsn and
  os.environ.get("ENVIRONMENT") != "test"` — without it, any shell that happens
  to export `SENTRY_DSN` would ship every test-fixture failure to production
  Sentry tagged `environment=production`. It is a plain assignment, not
  `setdefault`, so a stray `ENVIRONMENT=dev` in the shell cannot defeat the gate;
  that is also what keeps the Instagram scheduler from starting under pytest.
  Hence the `# noqa: E402` on every import below it — don't "tidy" the import
  order.
- **No test can send real email — an autouse guard in `conftest.py` sees to
  it (#563).** The relay authorizes by sender IP with no credentials, so an
  unguarded test once delivered mail as `@panamaincontext.com` from a
  managed-agents sandbox. Three layers: the autouse `smtp_outbox` fixture
  replaces `smtplib.SMTP` with a recorder (request it by name to assert on
  sent `Message`s); conftest forces `SMTP_RELAY_HOST=smtp.invalid`; and
  `send_email` raises `RealSendBlockedError` (a `RuntimeError`, deliberately
  not an `EmailDeliveryError`, so admin-side absorption can't swallow it) if
  it ever reaches the real class under `ENVIRONMENT=test`. A per-test patch
  of `smtplib.SMTP` / `send_email` still takes precedence.
- **Seed rows through the chained factory fixtures, not by hand.** `conftest.py`
  exposes `taxonomy_factory` → `category_factory` → `suggestion_factory` →
  `research_factory` → `article_factory`, and each creates its own parent when
  the argument is omitted, so `article_factory()` alone yields a complete
  APPROVED, published article. Two gotchas are already encoded in them:
  `article_factory` reuses the article's own category for the research's
  suggestion (a second category would pollute the category-listing tests), and
  `published_at` defaults to `now() - 1s` behind an `UNSET` sentinel — Postgres
  `now()` is fixed at transaction start, so a bare `now()` would not satisfy the
  list endpoint's `published_at <= now()`. Pass `published_at=None` explicitly
  for the not-yet-published case.
- **The frontend has no test suite.** `frontend/package.json` defines only
  `dev` / `build` / `lint` / `preview` — there is no vitest/jest dependency, no
  `test` script and no `*.test.*` / `*.spec.*` file under `frontend/src`. The
  only automated gates on frontend code are eslint and the Vite build (CI job
  "Frontend (build)"). The rules above are backend rules; don't go hunting for a
  frontend runner, and don't call a UI change "tested" because CI is green.
- **Binary-dependent tests skip silently in CI.** The real-render tests are
  `skipif`-gated on the binary being launchable: headless chromium
  (`tests/test_slide_render.py`, `tests/test_guide_pdf.py`) and ffmpeg/ffprobe
  (`tests/test_audio_generation.py`, `tests/test_video_assembly.py`). Both
  Dockerfiles bake the binaries in, but the CI backend job is pip-install-only, so
  a green CI run is **not** evidence that slide/PDF rendering or audio/video
  assembly works — exercise those paths with `scripts/run-tests.sh` (it rebuilds its
  image by itself when `requirements.txt` or the Dockerfile changed — #707).
- **Never pin a migration as "the newest" or as "the whole enum".** A test
  asserting that a migration's added value set equals the entire enum, or that it
  is the sole Alembic head, is true only until the next value lands: #461 had to
  rewrite two such tests from #460, and #464 two more from #461. Phrase them as
  what the migration *historically did* (see
  `test_migration_0026_widened_the_constraint_to_edu_research`), and leave
  present-tense model ↔ enum ↔ migration agreement to
  `tests/test_status_constraints.py`. `tests/test_edu_guide_grounding.py` still
  pins the sole Alembic head by name (`0051_mcp_approval_requests` since the
  MCP approval gates, #642; `0050_agent_task_actions` before that, for the
  retry/skip reason log, #641; `0049_mcp_tool_calls` before that, for the MCP
  audit log, #639; `0048_mcp_tokens` before that, for the MCP
  server's token table, #638; `0047_ask_pic_queries` before that, for the Ask
  PIC abuse / cost ledger, #634; `0046_consistency_findings` before that, for the
  cross-article consistency table, #631; `0045_suggestion_dedup` before that, for the
  suggestion dedup columns, #630; `0044_knowledge_chunks` before that, for the RAG
  chunk table, #623; `0043_pgvector` before that, for the pgvector
  extension, #621; `0042_article_audio_qa_es` before that, for the
  narration voice check's `articles.audio_qa_es`; `0041_media_search` before that, for the media
  search vectors; `0040_article_narration_job` before that, for the
  `ARTICLE_NARRATION` crawl-job type; `0039_es_accuracy_check` before that, for the
  `ES_ACCURACY_CHECK` agent-task kind and `ES_ACCURACY` grammar-fix source;
  `0038_grammar_waits` before that, for the
  `WAIT_GRAMMAR_CHECKED_EN` / `_ES` agent-task kinds; `0037_article_claims_gate` before that, for
  `WAIT_ARTICLE_CLAIMS_RESOLVED`; `0036_research_ready_gate` before that, for the
  `WAIT_RESEARCH_READY` agent-task kind; `0035_agent_task_waiting` before that;
  `0034_edu_job_types` before that; it was `0033_grammar_review_diffs` before that and
  `0032_grammar_fix_source` before that), so expect to update it with every new
  migration.
- **The drop-and-recreate CHECKs have one present-tense migration test (#572).**
  `tests/test_status_constraints.py` only covers `0006`'s columns, and the test DB
  is built from model metadata, so a typo in a later widening migration would
  only surface in prod. `tests/test_migration_check_vocabularies.py` walks the
  revision chain from the head, finds the **newest** revision whose source names
  each constraint, runs its `upgrade()` against a recording stand-in for
  `alembic.op`, and asserts the `'<value>'` list it creates equals the enum
  (`ck_agent_tasks_kind` ↔ `AgentTaskKind`, `ck_grammar_check_fixes_source` ↔
  `GrammarFixSource`, `ck_crawl_jobs_job_type` ↔ `CrawlJobType`). It names no
  migration file, so it is not the pinning the rule above forbids and needs no
  edit when a migration lands; a fourth drop-and-recreate constraint is one line
  in its `CONSTRAINT_ENUMS`. Its only convention: the migration spells the
  constraint name as a literal string (the `_CONSTRAINT = "ck_…"` pattern).

### Quality Standards
- All code must pass `ruff check` **and** `ruff format --check` (both are CI gates)
- All tests must pass before completing a task
- Follow existing patterns in the codebase

CI (`.github/workflows/ci.yml`) is **three** jobs, not just the backend one — a
change can pass every ruff gate and still redden CI:

1. **Secrets scan (gitleaks)** — gitleaks 8.21.2 over the *working tree*
   (`detect --source . --no-git`, config `.gitleaks.toml`). Tree-only on purpose:
   pre-Phase-0 commits hold now-rotated secrets. A token-shaped literal in a
   fixture, doc or `.env.example` fails it. **But `--no-git` means `.gitignore`
   is not honoured, so the config carries an explicit path allowlist instead**
   (`[extend] useDefault = true` plus `[allowlist]`), and knowing what is in it
   is what tells you whether a hit is real: the root `.env` / `.env.*` — which
   covers the committed root `.env.example` — and `backend/.env*`,
   `backend/.ruff_cache/`, `.claude/settings.local.json`, `frontend/dist/`,
   `frontend/node_modules/`, and `docs/tailwind-plus/` (vendored theme zips
   carrying example credentials). Note the anchors: `frontend/.env.example` is
   **not** covered by the root-anchored pattern. There is also one
   false-positive `regexes` entry for `pricing_config` key names
   (`base_fee`, `hourly_rate_pax_*`, `senior_discount_percent`,
   `reservation_validity_days`), because the generic-api-key rule fires on the
   value side of the `key="<config-name>"` literals in the Alembic seeds and
   tests — so adding a new pricing key to a seed may need a line here.
2. **Backend (pytest + ruff)** — `ruff check app`, then `ruff format --check app`,
   then `pytest --cov=app --cov-report=term -q`. Coverage is **reported but not
   gated** (baseline ~59%, #13). Docker Hub rate-limits unauthenticated pulls of
   non-official images on runners, so CI builds the pgvector test DB from
   `docker/test-db/Dockerfile` (the ECR mirror of the official postgres image),
   passes it in as `PIC_TEST_PG_IMAGE`, and sets `TESTCONTAINERS_RYUK_DISABLED`.
   Locally the default is still `pgvector/pgvector:pg17`.
   **The `postgres_container` teardown is non-fatal (#735).** testcontainers'
   `stop()` is a `remove(force=True, v=True)` whose Docker API read can time
   out (60 s) on a loaded runner *after* every test passed, which used to turn
   the run red (error attached to the session's last test). `conftest.py` now
   starts/stops the container explicitly and `_stop_test_container` turns a
   `requests` timeout/connection error or `docker.errors.APIError` into a
   `RuntimeWarning` in the warnings summary. A failure *starting* the container
   still errors every test; anything else raised by the stop still raises.
   Don't put the `with PostgresContainer(...)` form back.
   **`deploy.yml` follows the same rule (#647).** Every `FROM` in the repo's
   Dockerfiles (the two prod images it builds, `docker/cert-watcher/Dockerfile`
   that `/deploy` builds, and the dev `backend/Dockerfile` / `frontend/Dockerfile`
   for consistency) names
   `public.ecr.aws/docker/library/<image>:<tag>`, and both build jobs boot
   BuildKit from `public.ecr.aws/vend/moby/buildkit` (`BUILDKIT_IMAGE`) instead
   of the Docker Hub default. Same images, same digests, no login: a bare
   `FROM python:…` brings the random deploy 429s back. The mirror has its own
   per-second, per-IP anonymous limit, so two deploys starting together can get
   `toomanyrequests: Rate exceeded` at builder boot (#677). That is why the jobs
   create the builder in a retried `docker buildx create` step rather than
   `docker/setup-buildx-action`, which cannot retry. A pre-pull does not help:
   buildx contacts the registry on every bootstrap even when the image is
   cached. Don't "restore" the action.
3. **Frontend (build)** — `npm run lint` (eslint, errors-only per #273) then
   `npm run build`. **Do not rename this job**: its name is pinned to a required
   status check in branch protection, and renaming strands the check in a permanent
   "Expected — Waiting for status".

**The two halves differ in reproducibility, which is why a backend CI run can
redden with no code change.** `backend/requirements.txt` is ~36 floating `>=`
constraints with **no lockfile**, and both CI (`pip install -r
requirements.txt`) and `docker/backend/Dockerfile.prod` resolve them fresh at
install time — so a new upstream release lands in the next run or image build
unannounced. Exactly two lines are pinned, and both for that reason:
`ruff==0.15.15` (the `ruff format --check` gate is version-sensitive) and
`pytest-cov==7.1.0`, pinned in #13 explicitly "matching the ruff-pinning pattern
after the version-divergence incident". One more line is *capped* rather than
pinned: `sqlalchemy>=2.0.25,<2.1`, because SQLAlchemy 2.1 (released 2026-09-24)
resolves a bare `postgresql://` URL to the psycopg 3 driver, which is not
installed — the next CI run died in `conftest.py` with `No module named
'psycopg'`, and a freshly built prod image would have crashed the same way at
startup. Lifting that cap is an upgrade (add psycopg 3, or spell every URL
`postgresql+psycopg2://`), not a tidy-up. `mcp>=2.3,<3` is capped the same way
(#638): the server is written against the 2.x API (see `docs/claude/mcp.md`). The frontend has the opposite property —
`package-lock.json` plus `npm ci` — so its build is reproducible. Before
debugging a backend failure on untouched code, check whether a dependency moved.

**CI deliberately does not cancel superseded runs on `main`.**
`cancel-in-progress` is `${{ github.ref != 'refs/heads/main' }}` — on for PR
branches, off for main — because deploy fires from *each* green CI run on main
and path-filters that merge's own `git diff --name-only HEAD^ HEAD`
(`paths:` filters are unavailable on `workflow_run`). Cancelling a superseded
main run therefore drops that merge's deploy entirely: three PRs merged within
30s on 2026-09-05 left only the last, script-only merge with a green CI, and
the backend and frontend changes in the two cancelled runs never built until a
manual dispatch. Restoring a plain `cancel-in-progress: true` silently
re-opens that hole.
