# Panama In Context - Project Constitution

## Project Overview

Panama In Context is a bilingual (Spanish/English) website for a Panama-based business offering:
- **Academic Excursions**: Field trips for schools with pre-visit Zoom, guided tours, post-visit study guides
- **Guided Tours**: Private tours for tourists, families, corporate visitors
- **History Blog**: Long-form articles (~2000 words) about Panama's history

> **Brand/design skill:** A `panama-in-context-design` skill lives at `.claude/skills/panama-in-context-design/` — invoke it ("use the panama-in-context-design skill") whenever building UI, marketing pages, slides/decks, mocks, or prototypes to stay on-brand (tokens, React components, website + slides UI kits, brand assets).

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
- `scripts/` - One-off operational scripts
- `ai_generated_content/` - Research prompts and downloaded research docs (see Research Workflow)
- `docs/` - Documentation and reference materials

## Backend Tech Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Runtime | Python | 3.12 (container); pyproject requires ≥3.11 |
| Framework | FastAPI | 0.109+ |
| ORM | SQLAlchemy | 2.0+ (sync mode) |
| Database | PostgreSQL | 17 |
| Validation | Pydantic | 2.5+ |
| Testing | pytest + pytest-asyncio | 7.4+ |
| Linting | ruff | 0.1+ |
| Migrations | Alembic | 1.13+ |
| Error reporting | sentry-sdk[fastapi] | 2.0+ |
| LLM clients | anthropic, openai SDK | — |
| Translation | deepl | 1.16+ |
| Object storage | boto3 (DO Spaces, S3-compatible) | 1.34+ |
| Email | SMTP relay (Google Workspace `smtp-relay.gmail.com:587`, IP-allowlisted, stdlib `smtplib`) | stdlib |
| Mailing list (subscribe / confirm) | ORM against `educators` DB table (UNIQUE on email) | — |
| Payments | PayPal Checkout SDK | — |

## Frontend Tech Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Framework | React | 19.2 |
| Build | Vite | 6.4 |
| Styling | Tailwind CSS | **v4** (directive syntax differs from v3) |
| i18n | i18next + react-i18next | 23.x |
| Routing | react-router-dom | 7.x |
| Rich text (admin) | @tiptap/react | 3.x |
| Data grids (admin) | ag-grid-react | 35.x |
| Error reporting | @sentry/react | 10.x |

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
   drop-and-recreate migration.

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

### String Column Widths (issue #27)
Canonical widths, applied consistently across models:
- **Email** columns → `String(254)` (RFC 5321 max).
- **Entity `name` / `name_es`** (Taxonomy, Category, Zone, Tour, Hotel) → `String(200)`.
- **`Tag.name` / `Tag.name_es`** → `String(100)` (tags are genuinely shorter; English
  and Spanish stay the same width).

### Error Handling
- Use `HTTPException` with appropriate status codes
- 404 for not found, 400 for bad requests, 500 for server errors

## Operational Commands

All backend commands run inside Docker containers.

```bash
# Start services
docker-compose up -d              # Start all services
docker-compose up -d backend db   # Start backend + database only
docker-compose logs -f backend    # View backend logs

# Run Tests — canonical path (works from any worktree; no stack required)
# scripts/run-tests.sh spins a throwaway container that mounts THIS worktree's
# code + the docker socket (for testcontainers) and publishes no ports, so it
# never collides with the operator's running stack. Use this in agent worktrees.
scripts/run-tests.sh                                         # Full suite (pytest -q)
scripts/run-tests.sh pytest tests/test_articles.py -q        # Specific file
scripts/run-tests.sh pytest -k "test_name"                   # By name pattern
scripts/run-tests.sh --build                                 # Rebuild image first (deps changed)

# Run Tests — convenience when the operator's `docker-compose up` stack is live
docker-compose exec backend pytest                           # All tests (tests MAIN checkout)

# Linting (CI runs `ruff check app`)
scripts/run-tests.sh ruff check app                          # Check for issues (canonical)
docker-compose exec backend ruff check /app --fix            # Auto-fix issues (live stack)
docker-compose exec backend ruff format /app                 # Format code (live stack)

# Database Migrations (inside container)
docker-compose exec backend alembic upgrade head             # Run migrations
docker-compose exec backend alembic revision -m "message"    # Create migration
docker-compose exec backend alembic downgrade -1             # Rollback one

# Rebuild container after requirements.txt changes
docker-compose build backend
docker-compose up -d backend

# Query Production (read-only)
# Use the database-ops skill for safe production queries
```

## API Structure

Base URL: `/api/v1/`

Routers registered in `backend/app/main.py`, grouped by domain:

| Router | Surface |
|--------|---------|
| `articles`, `categories`, `search` | Blog content (public) |
| `tours`, `zones`, `bookings`, `orders` | Tour booking flow (public + checkout) |
| `booking_admin`, `dashboard` | Admin views for bookings & ops |
| `edu`, `media_library`, `public_media`, `educators` | Educational excursions, media library, educator access gate |
| `auth`, `admin` | Admin authentication and CMS |
| `contact`, `subscribe` | Forms & mailing list |
| `webhooks` | Inbound webhooks (PayPal) |

See `backend/app/api/*.py` for the exhaustive endpoint list within each router.

## Observability

- **Sentry** is configured for both backend (`backend/app/main.py`, DSN hardcoded) and frontend (`frontend/src/main.jsx`).
- **cert-watcher** sidecar (`docker/cert-watcher/`) runs daily, probes TLS endpoints, reports to Sentry under cron monitor `cert-watcher`.
- Production logs: `docker-compose logs -f <service>` on the droplet.

## NotebookLM Integration

The `nlm` CLI (`notebooklm-mcp-cli`) is available for creating and querying Google NotebookLM notebooks. Use the `/nlm-*` skills:

| Skill | Purpose |
|-------|---------|
| `/nlm-create-notebook` | Create a new notebook, optionally with initial markdown source |
| `/nlm-add-content` | Add text, URLs, or files as sources to an existing notebook |
| `/nlm-fetch-content` | Retrieve source content, notes, or AI summaries from a notebook |
| `/nlm-query` | Send a question/prompt and get AI answers grounded in notebook sources |

Key commands: `nlm login --check`, `nlm notebook list --json`, `nlm notebook create`, `nlm source add`, `nlm notebook query`. Always use `--json` for parseable output and `--wait` when adding sources.

## Research Workflow

Skills for producing article research documents via Claude's deep research. The workflow: generate a prompt from DB data → paste into Claude UI → download result → process into DB.

| Skill | Purpose |
|-------|---------|
| `/research-prompt` | Generate a research prompt for an article suggestion (saves to `ai_generated_content/research_prompts/`) |
| `/research-process` | Validate and insert downloaded research documents into the production `research` table |
| `/fact-check-prompt` | Compile `NEEDS_DEEP_RESEARCH` claims (from the web-search fact-check tier, epic #373) into a deep-research verification prompt (saves to `ai_generated_content/fact_check_prompts/`) |
| `/fact-check-process` | Parse downloaded verdict documents and write verdicts back to the production `fact_check_claims` table |

**Directory structure:**
```
ai_generated_content/
  research_prompts/           # Generated prompts
    processed/                # Moved here after research is processed
  research_documents/         # User downloads research here from Claude UI
    processed/                # Moved here after DB insertion
  fact_check_prompts/         # Generated claim-verification prompts
    processed/                # Moved here after verdicts are processed
  fact_check_documents/       # User downloads verdict docs here from Claude UI
    processed/                # Moved here after DB write
```

## Agentic Protocols

### Memory
If you learn a new persistent pattern about this codebase, update this file using Edit.

### Tool Preferences
- Use `Grep` over `Read` for searching large files
- Use the Task tool with Explore agent for open-ended codebase questions
- Prefer editing existing files over creating new ones

### Testing Requirements
- New endpoints MUST have corresponding tests
- Tests should use the PostgreSQL test container (see conftest.py)
- Mock external services (PayPal, SMTP relay, DO Spaces, DeepL, LLM providers)

### Quality Standards
- All code must pass `ruff check`
- All tests must pass before completing a task
- Follow existing patterns in the codebase