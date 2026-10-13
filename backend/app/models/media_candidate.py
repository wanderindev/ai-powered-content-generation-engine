"""Media candidate model — images discovered from external sources, pending review."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import CandidateSource, CandidateStatus, status_check

__all__ = ["CandidateSource", "CandidateStatus", "MediaCandidate"]


class MediaCandidate(Base):
    __tablename__ = "media_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Source identification
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255))
    external_url: Mapped[str] = mapped_column(String(1024))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))
    page_url: Mapped[str | None] = mapped_column(String(1024))

    # Metadata
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(500))
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)))

    # Licensing
    license: Mapped[str | None] = mapped_column(String(100))
    license_url: Mapped[str | None] = mapped_column(String(512))
    attribution: Mapped[str | None] = mapped_column(Text)

    # Technical
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(50))
    file_size: Mapped[int | None] = mapped_column(Integer)

    # Relevance scoring (from LLM batch scoring)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    relevance_notes: Mapped[str | None] = mapped_column(Text)

    # Review status
    status: Mapped[str] = mapped_column(String(20), default=CandidateStatus.PENDING.value)
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Provenance
    source_categories: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)))
    crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Link to Media (set when approved and downloaded)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_candidate_source_external_id"),
        Index("ix_candidate_status", "status"),
        Index("ix_candidate_source", "source"),
        Index("ix_candidate_relevance", "relevance_score"),
        status_check("media_candidates", "status", CandidateStatus),
        status_check("media_candidates", "source", CandidateSource),
    )
