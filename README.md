# AI-Powered Content Generation Engine

> **📌 This is a snapshot, as of October 16, 2026, of an active private project.**
> The live repository stays private for security reasons; this snapshot exists
> so the architecture and code can be discussed openly. It is not developed
> here and accepts no pull requests.

## What is this?

The production codebase behind [Panama In Context](https://panamaincontext.com) —
a bilingual (Spanish/English) platform for a Panama-based educational-tourism
business: academic field trips for schools, private guided tours, and a
long-form history blog. Underneath the marketing site sits an **AI-powered
content engine**: deep research → long-form article series → claim-level
fact-checking → educational study guides, narrated slide-deck videos and
Instagram cards. A knowledge layer (pgvector + Voyage) indexes everything the
engine has written and verified; a deterministic task graph lets an external
agent drive the article pipeline over a narrowly scoped token; and an MCP
server gives other agents scoped, audited access, with anything that would
publish routed to an approvals queue a person confirms.

**📖 Read the full project overview at
[feliu.dev/projects/panama-in-context](https://feliu.dev/projects/panama-in-context)** —
or browse the same 14-part system write-up right here in [`docs/`](docs/README.md),
with architecture diagrams and screenshots.

The research, fact-checking and Grammarly proofreading loops are driven by a companion
Chrome extension with its own public snapshot:
[**research-companion-extension**](https://github.com/wanderindev/research-companion-extension)
— it wraps an API around Claude's deep-research mode by treating the browser
as the runtime.

Also of note: [`.claude/`](.claude/) contains the real agentic-development
configuration used to build this project — permission fences (deny rules for
force-pushes, history rewrites and destructive deletes), format/quality hooks,
and 20 project-specific skills for everything from deploys to research-prompt
generation. [`CLAUDE.md`](CLAUDE.md) is the project constitution those agents
operate under.

## Tech Stack

- **Frontend**: React 19, Vite, Tailwind CSS 4, i18next, React Router
- **Backend**: FastAPI, SQLAlchemy 2.0 (sync), Pydantic 2, Alembic
- **Database**: PostgreSQL 17 + pgvector
- **AI**: Anthropic (Opus, Sonnet, Haiku), Voyage embeddings + rerank, Gemini feature images, DeepL translation, ElevenLabs narration
- **Agents**: an MCP server (official Python SDK) mounted inside the FastAPI app
- **Infrastructure**: Docker Compose, DigitalOcean (Droplet + Managed Postgres + Spaces), Sentry

## How the Project Is Organized

```
frontend/            React SPA (Vite + Tailwind)
  src/components/    UI components (public site + admin CMS)
  src/pages/         Route-level pages
  public/locales/    i18next translations (es = default, en = fallback)
backend/             FastAPI API server
  app/api/           Routers, grouped by domain (articles, tours, edu, admin, …)
  app/models/        SQLAlchemy models (+ enums.py as the status vocabulary)
  app/schemas/       Pydantic request/response models
  app/services/      Business logic: LLM orchestration, media crawler, email, PayPal
  app/services/knowledge/  Knowledge layer: chunking, embeddings, hybrid retrieval
  app/mcp/           MCP server: scoped tools, audit, approval requests
  tests/             pytest suite (runs against a throwaway Postgres container)
  alembic/           Database migrations
docker/              Production Dockerfiles (backend, frontend, cert-watcher)
scripts/             Operational scripts (incl. scripts/run-tests.sh)
docs/                The 14-part system write-up, plus docs/claude/ (agent reference docs)
.claude/             Agentic development config: skills, hooks, permission fence
CLAUDE.md            Project constitution for AI-assisted development
```

## Getting Started

### 1. Clone and configure environment

```bash
git clone git@github.com:wanderindev/ai-powered-content-generation-engine.git
cd ai-powered-content-generation-engine

# Copy environment templates
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

The templates document every variable the app reads. For local development the
defaults mostly work as-is: Docker Compose provides a local PostgreSQL and a
MinIO object-storage sidecar, so at minimum you only need `DATABASE_URL`
(already pointed at the compose db). Add real keys only for the features you
want to exercise:

| Variable | Enables |
|----------|---------|
| `ANTHROPIC_API_KEY` | Article generation, fact-checking, media scoring, Ask PIC |
| `VOYAGE_API_KEY` | Knowledge layer: embeddings, rerank, related articles |
| `GEMINI_API_KEY` | Feature-image generation |
| `DEEPL_API` | Spanish ↔ English translation |
| `ELEVENLABS_API_KEY` | Narrated slide-deck videos |
| `PAYPAL_CLIENT_ID` / `SECRET` | Checkout + webhook verification (sandbox by default) |
| `INSTAGRAM_ACCESS_TOKEN` / `ACCOUNT_ID` | Instagram publishing (disabled when empty) |

### 2. Start all services

```bash
docker-compose up -d
```

- **frontend** at `http://localhost:5173` (hot reload)
- **backend** at `http://localhost:8000` (hot reload; Swagger UI at `/docs`)
- **db** (PostgreSQL) on port `5433` (mapped to avoid conflicts)
- **minio** (S3-compatible storage) standing in for DigitalOcean Spaces

### 3. Run database migrations

```bash
docker-compose exec backend alembic upgrade head
```

## Tests and Linting

The canonical path is `scripts/run-tests.sh`, which spins up a throwaway
container mounting the current tree (tests use a Postgres testcontainer, so
the Docker socket is mounted too):

```bash
scripts/run-tests.sh                                    # full suite
scripts/run-tests.sh pytest tests/test_articles.py -q   # one file
scripts/run-tests.sh pytest -k "test_name"              # by name
scripts/run-tests.sh ruff check app                     # lint
```

Or, with the compose stack already running:

```bash
docker-compose exec backend pytest
docker-compose exec backend ruff check /app --fix
docker-compose exec backend ruff format /app
```

A [pre-commit](https://pre-commit.com/) hook runs **gitleaks** against staged
changes so secrets never land in a commit (same config as the CI job):

```bash
pip install pre-commit && pre-commit install
```

## Deploying to DigitalOcean

The production setup this codebase runs on (see the
[architecture & operations write-up](docs/14-architecture-data-and-operations.md)
for the full picture):

1. **Managed PostgreSQL** — a DO Managed Database cluster (`sslmode=require`);
   point `DATABASE_URL` at it.
2. **Spaces** — an S3-compatible bucket for media, plus its CDN endpoint
   (`DO_SPACES_*` variables).
3. **Container Registry** — build the production images and push:
   ```bash
   docker build -f docker/backend/Dockerfile.prod  -t registry.digitalocean.com/<registry>/backend:latest .
   docker build -f docker/frontend/Dockerfile.prod -t registry.digitalocean.com/<registry>/frontend:latest .
   docker build -t registry.digitalocean.com/<registry>/cert-watcher:latest docker/cert-watcher
   docker push  # each image
   ```
4. **Droplet** — copy `docker-compose.prod.yml` and a filled-in `.env`
   (template: [`.env.example`](.env.example), `chmod 600`), then
   `docker-compose -f docker-compose.prod.yml up -d`. The stack includes
   [Watchtower](https://containrrr.dev/watchtower/), which polls the registry
   and auto-deploys new images within ~5 minutes of a push.
5. **TLS + monitoring** — the frontend container terminates TLS (certbot);
   the `cert-watcher` sidecar probes certificate expiry daily and reports to a
   Sentry cron monitor. Run `alembic upgrade head` on the droplet after any
   deploy that ships a migration — there is deliberately no auto-migrate on
   startup.

## License

Released under the [MIT No Attribution License](LICENSE) (MIT-0): use, copy,
modify, and redistribute this code however you wish, commercially or not, with
no attribution required. (Brand assets — the Panama In Context name, logo, and
photography — are not covered and remain property of their owners.)
