from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ContentStatus, status_check
from app.utils.slug import generate_slug

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.media import Media
    from app.models.tag import Tag

__all__ = ["Article", "ContentStatus"]


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        status_check("articles", "outline_status", ContentStatus),
        UniqueConstraint("slug", name="uq_articles_slug"),
        UniqueConstraint("slug_es", name="uq_articles_slug_es"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    research_id: Mapped[int] = mapped_column(ForeignKey("research.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    feature_image_id: Mapped[int | None] = mapped_column(ForeignKey("media.id"), index=True)

    # English content (original)
    title: Mapped[str] = mapped_column(String(255))
    # Persisted slugs (#474). String(255) mirrors the title columns — slugifying
    # never lengthens a string. The insert defaults derive both from the titles in
    # the same INSERT, so a plain ``Article(title=...)`` construction still works;
    # *edits* are explicit and go through ``app.services.article_slugs``, which
    # freezes the slug once the article is live.
    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=lambda ctx: generate_slug(ctx.get_current_parameters()["title"]),
    )
    content: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)

    # Spanish content (translated via DeepL)
    title_es: Mapped[str | None] = mapped_column(String(255))
    # NULL when there is no Spanish title. The ES->EN fallback lives in the
    # response layer, not in the column.
    slug_es: Mapped[str | None] = mapped_column(
        String(255),
        default=lambda ctx: (
            generate_slug(t) if (t := ctx.get_current_parameters().get("title_es")) else None
        ),
    )
    content_es: Mapped[str | None] = mapped_column(Text)
    excerpt_es: Mapped[str | None] = mapped_column(Text)
    # Diego-voice narration of the Spanish article (ElevenLabs, generated from
    # the admin drawer; DO Spaces URL). Spanish-only surface by design.
    audio_url_es: Mapped[str | None] = mapped_column(String(500))
    # Ordered per-segment narration manifest ({hash, key, is_heading,
    # duration_ms} dicts) — lets a regeneration re-synthesize only edited
    # paragraphs (see app/services/article_audio.py).
    audio_manifest_es: Mapped[list | None] = mapped_column(JSONB)
    # Voice check of the last narration run (#601): pitch baseline, re-taken
    # high-pitch segments, segments to listen to, OK / FIXED / NEEDS_REVIEW.
    # Overwritten on every run (see app/services/article_audio.py).
    audio_qa_es: Mapped[dict | None] = mapped_column(JSONB)

    ai_summary: Mapped[str | None] = mapped_column(Text)
    outline: Mapped[str | None] = mapped_column(Text)
    outline_status: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        SAEnum(*ContentStatus.values(), name="content_status_type", create_type=False),
        default=ContentStatus.PENDING.value,
    )

    # Series support
    series_parent_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"))
    series_order: Mapped[int | None]

    # Timestamps
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last successful grammar-check pass per language (issue #409). Stamped even
    # when the proofread returned no fixes — "checked" is the fact recorded.
    grammar_checked_en_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grammar_checked_es_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    category: Mapped["Category"] = relationship(back_populates="articles")
    feature_image: Mapped[Optional["Media"]] = relationship()
    series_parent: Mapped[Optional["Article"]] = relationship(
        remote_side=[id], back_populates="series_children"
    )
    series_children: Mapped[list["Article"]] = relationship(back_populates="series_parent")
    tags: Mapped[list["Tag"]] = relationship(secondary="article_tags", back_populates="articles")

    @property
    def is_series_parent(self) -> bool:
        """Check if this article is the parent of a series."""
        return self.series_parent_id is None and len(self.series_children) > 0

    @property
    def is_part_of_series(self) -> bool:
        """Check if this article belongs to a series."""
        return self.series_parent_id is not None or self.is_series_parent
