"""Crawl job model — tracks async media-acquisition crawls off the request path.

A crawl (Wikimedia / LOC) or scoring run is kicked off by an admin endpoint that
returns ``202`` + a job id, then executes in a FastAPI ``BackgroundTask``. Job
state lives here (not in process memory) so that any of the uvicorn workers can
serve the status-poll request and so progress survives the originating request
(#277).
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import CrawlJobStatus, CrawlJobType, status_check

__all__ = ["CrawlJob", "CrawlJobStatus", "CrawlJobType"]


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Closed vocabularies — String + named CHECK (issue #24 convention).
    job_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20),
        default=CrawlJobStatus.PENDING.value,
        server_default=CrawlJobStatus.PENDING.value,
    )

    # The request payload that kicked off the job (seed_categories, queries, etc.).
    params: Mapped[dict | None] = mapped_column(JSON)

    # Progress counters, updated incrementally as the crawl walks categories/queries.
    units_total: Mapped[int | None] = mapped_column(Integer)
    units_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    current_unit: Mapped[str | None] = mapped_column(String(500))

    # Final result dict (CrawlResponse / LOCCrawlResponse / ScoreResponse shape).
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        status_check("crawl_jobs", "status", CrawlJobStatus),
        status_check("crawl_jobs", "job_type", CrawlJobType),
    )
