import logging

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# This app (database `defaultdb`) shares a managed Postgres cluster with feliu-dev
# (`feliudev`), atelier-loyalty (`loyalty`) and the managed-agents orchestrator
# (`orchestrator`): <your-db-cluster>, PG 17, 1 vCPU / 2 GB,
# max_connections = 50. Measured 2026-08-08: 24 of 50 in use — this app 6,
# atelier-loyalty 5, feliu-dev 4, ~9 DigitalOcean/background, orchestrator 0.
# The orchestrator reads 0 because it connects only during a tick (capped at 2),
# so a point-in-time count under-reports it; still budget for it.
#
# Each backend runs 2 uvicorn workers, so 2 + 3 caps this app at 10 connections.
# The old 5 + 10 let one app burst to ~30 and starve the cluster of slots for
# migrations/admin ops — under the 22-connection ceiling this cluster had before
# it was upsized on 2026-07-20, not the current 50. Keep the per-worker pool small
# and bounded; pool_recycle drops idle connections so they don't linger at the cap.
# Changing these numbers is a human capacity call, but make it against the budget
# above rather than against the 1 GB cluster this comment used to describe.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=3,
    pool_recycle=1800,
    pool_timeout=30,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def _release_session(db: Session) -> None:
    """Close the request session ahead of the request's other background tasks (#649).

    Never raises: Starlette runs background tasks in sequence and stops at the
    first exception, so a failed close here would silently drop the reindex /
    job that follows it. ``get_db``'s ``finally`` still closes again afterwards.
    """
    try:
        db.close()
    except Exception:
        logger.exception("Failed to release the request DB session before background tasks")


def get_db(background_tasks: BackgroundTasks):
    db = SessionLocal()
    # FastAPI >= 0.118 runs this dependency's `finally` only AFTER the response's
    # BackgroundTasks have finished, so without this the request's pooled
    # connection sits idle-in-transaction through every background reindex /
    # fact-check / crawl / edu job (#649) — one of only 5 slots per worker (see
    # the pool comment above). Queued before the endpoint body runs, this is
    # always the first background task; endpoints pass ids to their tasks, never
    # this session, and every runner opens its own SessionLocal().
    background_tasks.add_task(_release_session, db)
    try:
        yield db
    finally:
        db.close()
