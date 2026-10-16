# Operational Commands and the Droplet Edge

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## Operational Commands

All backend commands run inside Docker containers.

```bash
# Start services
docker-compose up -d              # Start all services (incl. minio + minio-init)
docker-compose up -d backend db   # Start backend + database only — no object store
docker-compose logs -f backend    # View backend logs

# The dev stack carries a local object store: MinIO stands in for DO Spaces so
# media pipelines never write to the live `panamaincontext` bucket. `minio-init`
# is a one-shot that creates that bucket with anonymous-read (mirroring the prod
# public-read ACL) and exits 0; objects land in `./local-storage/`, console on
# :9001 (minioadmin/minioadmin), S3 API on :9000. `docker-compose up -d backend db`
# deliberately omits both services — check what `DO_SPACES_*` resolves to before
# running an image/media pipeline started that way.

# The dev backend's DB is NOT pinned to the local one. docker-compose.yml sets
# `DATABASE_URL=${DATABASE_URL:-postgresql://panama:localdev@db:5432/...}` and
# marks the `db` dependency `required: false`, precisely so the operator can
# `source .env.prod-readonly` in the shell and bring the same stack up against
# **production**. So "inside the dev backend container" is not evidence of
# "against the local database" — read the env before running anything in
# `backend/scripts/` that writes, and prefer its `--dry-run`. (The container
# also runs `user: "0:0"` with the host docker socket mounted, so testcontainers
# can spawn the pytest Postgres; both are dev-only and commented as such.)

# Run Tests — canonical path (works from any worktree; no stack required)
# scripts/run-tests.sh spins a throwaway container that mounts THIS worktree's
# code + the docker socket (for testcontainers) and publishes no ports, so it
# never collides with the operator's running stack. Use this in agent worktrees.
scripts/run-tests.sh                                         # Full suite (pytest -q)
scripts/run-tests.sh pytest tests/test_articles.py -q        # Specific file
scripts/run-tests.sh pytest -k "test_name"                   # By name pattern
scripts/run-tests.sh --build                                 # Force a rebuild (rarely needed)
PIC_TEST_IMAGE=<image> scripts/run-tests.sh ...              # Use exactly this image, no hash check

# Test image (#707): the script hashes backend/requirements.txt + backend/Dockerfile
# and runs `pic-backend-test:<hash>`, so a dependency change rebuilds by itself
# (one ">> run-tests.sh: building … — <reason>" stderr line) and a current image
# costs only the hash check. A missing tag first adopts the compose-built
# `panama-in-context-backend` if its baked-in requirements.txt matches (no build).
# Tags are content-addressed, so concurrent worktrees never retag a running image;
# old `pic-backend-test:<hash>` tags accumulate and are safe to `docker rmi`.

# Empty-mount guard (#448): the bind mounts are resolved by the DOCKER DAEMON's
# filesystem, not the calling shell's. Where the repo path exists only locally
# (e.g. a sandbox workspace the host daemon cannot see) docker mounts empty
# directories, so an in-container check exits 2 with "…is EMPTY inside the
# container" BEFORE pytest/ruff run. That exit 2 is an environment problem, not a
# code failure — never report it as a passing or failing test run.

# Run Tests — convenience when the operator's `docker-compose up` stack is live
docker-compose exec backend pytest                           # All tests (tests MAIN checkout)

# Linting (CI gates on BOTH `ruff check app` and `ruff format --check app`)
scripts/run-tests.sh ruff check app                          # Check for issues (canonical)
scripts/run-tests.sh ruff format --check app                 # Formatting gate (canonical)
docker-compose exec backend ruff check /app --fix            # Auto-fix issues (live stack)
docker-compose exec backend ruff format /app                 # Format code (live stack)

# Database Migrations (inside container)
docker-compose exec backend alembic upgrade head             # Run migrations
docker-compose exec backend alembic revision -m "message"    # Create migration
docker-compose exec backend alembic downgrade -1             # Rollback one

# Knowledge reindex (scripts.reindex_knowledge; #624, contextual sentences #697)
# Idempotent and resumable: chunks are matched by content hash, each article is
# swapped in one transaction, so it is safe while the site is live. With an
# ANTHROPIC_API_KEY every article chunk lacking meta["context"] gets a Haiku
# situating sentence and is re-embedded — the first --all after #697 re-embeds
# the whole article corpus (2,665 chunks, ~$0.3 Haiku + ~$0.07 Voyage). On the
# droplet: docker exec pic-backend python -m scripts.reindex_knowledge --all --dry-run
#           docker exec pic-backend python -m scripts.reindex_knowledge --all --workers 3
docker-compose exec backend python -m scripts.reindex_knowledge --all --dry-run

# Merging to main DEPLOYS automatically (#421). .github/workflows/deploy.yml runs
# on `workflow_run` after CI, guarded on conclusion == 'success', path-filters
# backend/ vs frontend/ from the commit diff, and pushes `latest` + the commit SHA
# to registry.digitalocean.com/wanderindev. Watchtower on the droplet polls every
# 300s. The `/deploy` skill is now the manual escape hatch (branch deploys,
# pic-cert-watcher, re-pushing after a failed/skipped run).
#
# That registry is SHARED with other projects (feliu-backend, feliu-frontend,
# pic-cert-watcher). deploy.yml's `prune-tags` job keeps the newest **2**
# commit-SHA tags per pic-* repo — the tag just pushed plus one rollback target —
# and never touches `latest` or another project's repo. KEEP was 10 until
# 2026-08, which filled the 5 GB Basic plan to 222% (backend images are ~1 GB
# each). The untagged manifests it leaves behind are now reclaimed by the
# registry's own automated garbage collection (enabled account-wide since
# 2026-08); manual `doctl registry garbage-collection start` returns 412 and is
# no longer the operator task it used to be.
#
# Deploys do NOT run migrations — neither the workflow nor `/deploy`. The prod
# image's CMD is uvicorn directly (docker/backend/Dockerfile.prod),
# docker-compose.prod.yml has no migration step, and deploy.yml has no alembic
# job. After merging a migration someone must run `alembic upgrade head` on the
# droplet (the shipping agent does it — see "## Shipping" below) — otherwise
# code that references the new table fails in production
# while the repo looks correct
# (e.g. the Instagram scheduler loop, started from the FastAPI lifespan and
# gated by INSTAGRAM_SCHEDULER_ENABLED, raised UndefinedTable every 60s tick).
#
# An un-applied ADD COLUMN is broader than an un-applied CREATE TABLE, and that
# is the shape to recognize in triage. A SQLAlchemy entity query emits *every*
# mapped column, so one missing column breaks every read of that table — not
# just the feature that added it. Migration 0031 (`articles.audio_manifest_es`,
# merged 2026-09-01 in 91bb613) shipped ahead of its upgrade and produced two
# separate Sentry issues within 21 seconds: `POST .../articles/{id}/generate-audio`
# (PIC-PYTHON-FASTAPI-1X) *and* `GET /admin/dashboard/articles`
# (PIC-PYTHON-FASTAPI-1Y), whose `db.query(Article)` in `list_articles` has
# nothing to do with narration. So an `UndefinedColumn` on an unrelated endpoint
# is schema drift, not a bug at the throw site — the same "victim, not cause"
# rule as `QueuePool` surfacing in `validate_admin_token`. Two triage runs
# (112, 113) each bottomed out at NEEDS_HUMAN here because a bare `alembic
# upgrade` and the DB host are deny-listed. The droplet path needs neither:
# verify read-only with `docker exec pic-backend alembic current` over ssh, and
# apply the upgrade the way "## Shipping" does.

# Rebuild container after requirements.txt changes
docker-compose build backend
docker-compose up -d backend

# Query Production (read-only)
# Use the database-ops skill for safe production queries
```

### The Droplet's Edge Is PIC's Own Frontend Container
`pic-frontend` is not just this site's static server — it is the **only ingress
on the droplet**, and it fronts three projects. `docker-compose.prod.yml` gives
the backend `expose: 8000` (never `ports:`), while the frontend binds `80:80` /
`443:443`, mounts `/opt/letsencrypt` and terminates TLS; `docker/frontend/
nginx.prod.conf` then serves `panamaincontext.com` and reverse-proxies
`feliu.dev` / `api.feliu.dev` to the `trd-*` containers and
`loyalty.atelierm.store` to `loyalty-app`, all over the external `shared-web`
network. So a broken `nginx.prod.conf` or a failed pic-frontend deploy takes
**feliu.dev and the loyalty app down with it** — the same shared-blast-radius
rule as the DO registry and the Postgres cluster above. Five properties:

- **Upstreams go through nginx *variables* plus a Docker-DNS resolver
  (`resolver 127.0.0.11`), deliberately.** A literal `proxy_pass
  http://trd-backend:8000` is resolved once at config load, so when Watchtower
  recreates an upstream container on a new IP every new connection 502s until a
  manual `nginx -s reload` — that is what took feliu.dev down on 2026-07-22.
  The variable form also lets nginx boot with an upstream absent (requests just
  502 until it is up) instead of refusing to start.
- **The browser talks to `api.panamaincontext.com`, not to the site's own
  `/api/`.** `docker/frontend/Dockerfile.prod` bakes
  `VITE_API_URL=https://api.panamaincontext.com` and `deploy.yml` passes **no**
  build-args (its comment says so), while every frontend module reads
  `import.meta.env.VITE_API_URL` into an `API_BASE` — there is not one relative
  `fetch('/api/...')` in `frontend/src`. Consequence for #277: the proxy ceiling
  a prod admin action actually hits is the **api. vhost's 300s**, *not* the
  1200s on the site's `/api/` location — and that 1200s was raised precisely
  because 3-band study-guide generation used to 504, on a path prod browsers
  never take. It also means every API call is **cross-origin**, so
  `settings.cors_origins` (defaulting to localhost only) must name the site in
  the droplet's `.env`.
- **`client_max_body_size` is 20M at the edge, below the app's own caps.** The
  75 MB `MAX_MATERIAL_UPLOAD_BYTES` in `research_common.py` is real but
  unreachable in production: nginx 413s an edu-material upload over 20 MB before
  FastAPI sees a byte. Raising an app-side cap is only half the change.
- **`admin.panamaincontext.com` 302s to the public site.** The admin SPA is
  served at `panamaincontext.com/admin` by the SPA fallback (`try_files $uri
  $uri/ /index.html`); the subdomain is not an entry point.
- **`/locales/` is served `max-age=0, must-revalidate`** while hashed assets get
  `immutable` for a year — the server-side half of the `VITE_BUILD_HASH`
  cache-busting described under Copy and Build-Time Configuration.

Certificates renew from the `certbot` sidecar, whose `--deploy-hook` `docker
kill -s HUP pic-frontend`s so nginx reloads the new cert (it otherwise keeps the
old one in memory) — which is why that container has the docker socket and the
docker CLI. `frontend/netlify.toml` and `frontend/Dockerfile` are **not** this
path: the former is a leftover SPA-redirect config for a deploy target nothing
references, the latter is the Vite dev-server image.
