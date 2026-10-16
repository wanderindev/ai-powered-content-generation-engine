import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    admin,
    agent_tasks,
    articles,
    ask,
    auth,
    booking_admin,
    bookings,
    categories,
    contact,
    dashboard,
    edu,
    educators,
    instagram,
    knowledge,
    mcp_admin,
    mcp_approvals,
    media_library,
    orders,
    public_edu,
    public_media,
    search,
    subscribe,
    tours,
    webhooks,
    zones,
)
from app.core.config import get_settings
from app.core.observability import scrub_breadcrumb, scrub_pii_event
from app.mcp.server import mcp_http, mount_mcp
from app.services.instagram_scheduler import instagram_scheduler_loop

settings = get_settings()


def _configure_logging(level_name: str) -> None:
    """Install a root stdout/stderr handler and raise only the ``app`` namespace.

    Without this, nothing configures logging for the app: uvicorn's own config
    touches only the ``uvicorn*`` loggers and leaves the root logger bare, so
    every ``logger.info`` in ``app.*`` fell through to ``logging.lastResort``
    (WARNING and above only) and was discarded before reaching ``docker logs``
    (issue #424).

    The root handler is deliberately installed at WARNING: ``basicConfig(
    level=logging.INFO)`` would also switch on INFO for httpx, the anthropic
    SDK, botocore and deepl, which is a real log-volume problem on a 1GB
    droplet. Records are gated by the *originating* logger's effective level,
    so raising ``app`` alone is enough for ``app.services.llm`` et al. to emit
    and propagate to the root handler, while third-party loggers stay at
    WARNING.

    ``force=True`` is intentionally NOT passed: under pytest the root logger
    already has handlers, and tearing them out would break the suite's caplog
    assertions. There, ``basicConfig`` is a documented no-op and only the
    ``app``-level line below takes effect — which is all the tests need.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    logging.getLogger("app").setLevel(level)


_configure_logging(settings.log_level)

# Sentry DSN is read from the environment (set in the droplet's .env; unset in
# CI/dev/forks). When unset, init is a no-op so non-prod runs stay silent. The
# ENVIRONMENT != "test" gate is belt-and-suspenders against test-suite pollution.
sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn and os.environ.get("ENVIRONMENT") != "test":
    # send_default_pii=False keeps Sentry from auto-capturing request bodies,
    # cookies, IPs, and user identity. scrub_pii_event is a belt-and-suspenders
    # before_send hook that strips the same fields explicitly. See issue #9 and
    # app/core/observability.py.
    sentry_sdk.init(
        dsn=sentry_dsn,
        send_default_pii=False,
        before_send=scrub_pii_event,
        before_breadcrumb=scrub_breadcrumb,
    )


def _check_paypal_config(s) -> None:
    """Fail closed at startup if PayPal is wired up but the webhook id is missing.

    Defense in depth for issue #32: ``verify_webhook_signature`` already rejects
    unsigned webhooks when ``paypal_webhook_id`` is empty, but a deploy that drops
    that env var would then silently 401 every real PayPal webhook with no clear
    signal. Catching it at boot turns a confusing runtime symptom into an obvious
    startup failure. Only enforced in production and only when PayPal is otherwise
    configured (``paypal_client_id`` set), so dev/test/CI without PayPal still boot.
    """
    if s.environment == "production" and s.paypal_client_id and not s.paypal_webhook_id:
        raise RuntimeError(
            "PAYPAL_WEBHOOK_ID is not set but PayPal is configured. Webhook "
            "signature verification would fail closed and reject every PayPal "
            "webhook. Set PAYPAL_WEBHOOK_ID or unset the PayPal credentials."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _check_paypal_config(settings)
    # Instagram scheduler: the one place the app acts at a *future* time
    # (publishing scheduled posts). Off in tests; safe under multiple workers
    # (SKIP LOCKED claims). See app/services/instagram_scheduler.py.
    scheduler_task = None
    scheduler_stop = asyncio.Event()
    if settings.instagram_scheduler_enabled and settings.environment != "test":
        scheduler_task = asyncio.create_task(instagram_scheduler_loop(scheduler_stop))
    # MCP server (#638): the SDK's Streamable HTTP session manager must be
    # running for /mcp to answer (see app/mcp/server.py).
    async with mcp_http.run():
        yield
    if scheduler_task is not None:
        scheduler_stop.set()
        await scheduler_task


app = FastAPI(
    title=settings.app_name,
    description="API for Panama In Context - Historical articles and educational content about Panama",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# Static files for generated images
static_dir = Path(__file__).parent.parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Health check endpoints (root and API prefixed)
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": settings.app_name}


@app.get("/api/v1/health")
def api_health_check():
    return {"status": "healthy", "service": settings.app_name, "version": "1.0.0"}


# API info endpoint
@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


# Include routers
app.include_router(admin.router, prefix="/api/v1")
app.include_router(articles.router, prefix="/api/v1")
app.include_router(ask.router, prefix="/api/v1")  # public Ask PIC (#635)
app.include_router(categories.router, prefix="/api/v1")
app.include_router(contact.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(subscribe.router, prefix="/api/v1")

# Tour booking routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(tours.router, prefix="/api/v1")
app.include_router(zones.router, prefix="/api/v1")
app.include_router(bookings.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(booking_admin.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(edu.router, prefix="/api/v1")
app.include_router(instagram.router, prefix="/api/v1")
app.include_router(agent_tasks.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(mcp_admin.router, prefix="/api/v1")  # MCP audit log (#639)
app.include_router(mcp_approvals.router, prefix="/api/v1")  # MCP approval gates (#642)
app.include_router(webhooks.router, prefix="/api/v1")

# Media library
app.include_router(media_library.router, prefix="/api/v1")

# Public media (read-only)
app.include_router(public_media.router, prefix="/api/v1")
app.include_router(public_edu.router, prefix="/api/v1")

# Educator access
app.include_router(educators.router, prefix="/api/v1")

# MCP server for LLM agents (#638, epic #620): exact routes /mcp and its
# RFC 9728 metadata URL, outside /api/v1. Token auth is its own (mcp_tokens),
# not the admin or driver token. See docs/claude/mcp.md.
mount_mcp(app)
