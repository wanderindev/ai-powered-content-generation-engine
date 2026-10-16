# Repository Layout

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## Architecture

```
Frontend (React 19 + Vite 6)  →  Backend (FastAPI + PostgreSQL 17)
     ↓                              ↓
  Tailwind v4                    SQLAlchemy 2.0 (sync)
  i18next, Sentry                Markdown, Sentry, SMTP relay (stdlib)
```

**Monorepo Structure:**
- `frontend/` - React SPA (Vite, Tailwind CSS, i18next)
- `backend/` - FastAPI API server
- `docker/` - Production Dockerfiles (backend, frontend, cert-watcher)
- `scripts/` - Operational scripts (`run-tests.sh`, `research-cron.sh`, `one_off/`)
- `backend/scripts/` - **a second, separate scripts tree** — a real Python package
  (`__init__.py`) of ~23 backfill / maintenance / seeding / eval modules plus the
  `eval_rag/` sub-package (`prune_magic_links.py`, `cleanup_tags.py`,
  `backfill_thumbnails.py`, `seed_local_db.py`, `reindex_knowledge.py`, …). Convention: a module docstring with a `Usage:` block,
  invoked as `python -m scripts.<name>` **from `backend/`**, `--dry-run` where it
  writes. Because `backend/pyproject.toml` sets `pythonpath = ["."]`, they are
  importable from tests like `app.*` is, and they can have real coverage
  (`tests/test_prune_magic_links.py`). Don't put a new maintenance script in the
  root `scripts/one_off/` by habit — that one is for genuinely single-use jobs.
  **They reach the database through the ORM, with one exception that is now
  broken.** Every other module opens `SessionLocal()`; `auto_approve_candidates.py`
  alone drives the app's *own HTTP API* (`httpx` against
  `http://backend:8000/api/v1/media-library/...`, paging the candidate list and
  POSTing `.../approve`) and sends **no `Authorization` header at all**, so since
  the header-only admin gate (#263) it 401s against the router-level
  `validate_admin_token` on `media_library`. It also documents itself as
  deployable via a `docker-compose.worker.yml` that has never existed in this
  repo. Treat it as unrun history, not a working bulk-approve path.
- `ai_generated_content/` - Research prompts and downloaded research docs (see Research Workflow).
  **Gitignored** — it exists only in the operator's local checkout, so a fresh clone/worktree
  (including every agent worktree) will not have it; create it when a skill needs it.
- `docs/claude/` - **Subsystem deep dives split out of this file** (blog, booking &
  payments, auth, agent driver, Instagram, media, edu, fact-check). Same authority
  as CLAUDE.md; each has a stub below listing its main traps. Read the matching
  file before changing that area.
- `docs/` - Documentation and reference materials. Mostly append-only audit and
  session history (`docs/pilot/`, `docs/phase-2/`, `docs/methodology/`), but one
  subtree is a **live convention an agent run is expected to write to**:
  `docs/agent-fixes/prompts/` holds one `fix-issue-<N>.md` brief per autonomous
  fix, plus an `INDEX.md` whose one-line-per-brief entries are part of the
  deliverable — that is the prompt-preservation rule from
  `docs/methodology/conventions.md`, continued past the Phase 2 experiment
  (whose own corpus stays at `docs/phase-2/prompts/`). The sibling
  `docs/agent-fixes/agent-friendly-outcomes.md` is the trap: it is **gitignored
  and written by the orchestrator, never by the agent in its worktree**, because
  every concurrent agent PR used to conflict on that one file. Don't commit it
  and don't put it in a PR.
