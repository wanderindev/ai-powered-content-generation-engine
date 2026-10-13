from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import MediaSource, MediaType

if TYPE_CHECKING:
    from app.models.tag import Tag

__all__ = ["Media", "MediaSource", "MediaType", "media_tags"]


# Association table for many-to-many relationship between media and tags
media_tags = Table(
    "media_tags",
    Base.metadata,
    Column("media_id", Integer, ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(512))
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    file_size: Mapped[int]
    mime_type: Mapped[str] = mapped_column(String(127))
    media_type: Mapped[str] = mapped_column(
        SAEnum(*MediaType.values(), name="media_type_type", create_type=False)
    )
    source: Mapped[str] = mapped_column(
        SAEnum(*MediaSource.values(), name="media_source_type", create_type=False)
    )
    title: Mapped[str | None] = mapped_column(String(255))
    caption: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(String(255))
    attribution: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(100))
    license_url: Mapped[str | None] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(512))
    commons_id: Mapped[str | None] = mapped_column(String(255))
    search_terms: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None]
    height: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tags: Mapped[list["Tag"]] = relationship(secondary=media_tags, back_populates="media")
