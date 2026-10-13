from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ContentStatus

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.media import Media


# Association table for many-to-many relationship between articles and tags
article_tags = Table(
    "article_tags",
    Base.metadata,
    Column("article_id", Integer, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    name_es: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        SAEnum(*ContentStatus.values(), name="content_status_type", create_type=False),
        default=ContentStatus.PENDING.value,
    )
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    articles: Mapped[list["Article"]] = relationship(secondary=article_tags, back_populates="tags")
    media: Mapped[list["Media"]] = relationship(secondary="media_tags", back_populates="tags")
