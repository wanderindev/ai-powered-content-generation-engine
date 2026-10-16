# Tech Stack

Moved verbatim out of `CLAUDE.md` (size guard, 2026-10-12). Same
authority as CLAUDE.md; read this file before changing this area.

## Backend Tech Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Runtime | Python | 3.12 (dev container) / 3.11 (prod image `docker/backend/Dockerfile.prod`); pyproject requires ≥3.11 |
| Framework | FastAPI | 0.109+ |
| ORM | SQLAlchemy | 2.0+ (sync mode) |
| Database | PostgreSQL | 17 |
| Validation | Pydantic | 2.5+ |
| Testing | pytest + pytest-asyncio | 7.4+ |
| Linting | ruff | **pinned** `ruff==0.15.15` (`backend/requirements.txt`) — formatter output is version-sensitive, so a different ruff can fail the `ruff format --check` gate on untouched code |
| Migrations | Alembic | 1.13+ |
| Error reporting | sentry-sdk[fastapi] | 2.0+ |
| LLM clients | anthropic SDK (Claude); Gemini image API called over plain `httpx`, no SDK (`app/services/gemini_image.py`) | — |
| Text-to-speech | ElevenLabs SDK — `eleven_multilingual_v2`, one "Diego" voice (`app/services/audio_tts.py`, #317) | 1.0+ |
| Slide / PDF rendering | Playwright headless Chromium, **sync** API; PyMuPDF for PDF thumbnails | playwright 1.44+ |
| Video assembly | ffmpeg / ffprobe system binaries via `subprocess`; pydub (also ffmpeg-backed) for per-slide durations | — |
| Translation | deepl — `app/services/translation.py` deliberately adds **no** retry/timeout wrapper (the SDK already backs off on 429/5xx, so a wrapper double-retries); every call logs its character count, because DeepL bills per character | 1.16+ |
| Embeddings / rerank | Voyage AI (`voyage-4`, 1024 dims; `rerank-2.5`) over plain `httpx`, no SDK. **Every Voyage call goes through `app/services/embeddings.py`** (#622), which owns the model ids, batching limits, price table and `feature=` usage logging; empty `VOYAGE_API_KEY` raises `EmbeddingsDisabledError` (callers degrade or 503) | — |
| Object storage | boto3 (DO Spaces, S3-compatible) | 1.34+ |
| Email | SMTP relay (Google Workspace `smtp-relay.gmail.com:587`, IP-allowlisted, stdlib `smtplib`) | stdlib |
| Mailing list (subscribe / confirm) | ORM against `educators` DB table (UNIQUE on email) | — |
| Payments | PayPal **Invoicing** API v2 over plain `httpx` — no SDK dependency (`app/services/paypal.py`) | — |

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
