"""InstagramPost model for the social publishing pipeline.

One row per Instagram post draft. Two kinds (``post_type``):

* ``SABIAS_QUE`` — a "¿Sabías que…?" historical fact card generated from an
  approved blog research document (``research_id``); the operator picks the
  polaroid photo in the admin (``media_id``/``media_path`` — ``image_query`` is
  only the picker's search hint, mirroring the edu-slide flow).
* ``ARTICLE_PROMO`` — a "Nuevo en el blog" announcement for an article series
  (``article_id`` points at the series parent; template data like the series
  title and article count is derived from the article at render time).

Approval (``status``) reuses the shared content vocabulary; *publication* is a
separate, timestamp-driven axis handled by the publisher/scheduler:

* ``scheduled_at`` set (post APPROVED + rendered) → queued for the scheduler.
* ``publishing_started_at`` — transient claim marker while a worker publishes.
* ``published_at`` + ``ig_media_id`` — live on Instagram (terminal).
* ``publish_error`` — the publish failed; the row is parked (never auto-retried)
  until the operator retries or re-schedules, which clears the error.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import (
    ContentStatus,
    InstagramPostType,
    InstagramTemplateVariant,
    status_check,
)

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.media import Media
    from app.models.research import Research


class InstagramPost(Base):
    __tablename__ = "instagram_posts"
    __table_args__ = (
        status_check("instagram_posts", "post_type", InstagramPostType),
        status_check("instagram_posts", "template_variant", InstagramTemplateVariant),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_type: Mapped[str] = mapped_column(String(20))
    # Sabías-que card treatment (AZUL/CLARO); NULL for promos (CHECK passes NULL).
    template_variant: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(
        SAEnum(*ContentStatus.values(), name="content_status_type", create_type=False),
        default=ContentStatus.PENDING.value,
    )

    # Provenance — exactly one is set, by post_type (enforced in the service;
    # sabías-que rows carry research_id, promos carry article_id).
    research_id: Mapped[int | None] = mapped_column(ForeignKey("research.id"), index=True)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), index=True)

    # Content — Spanish only (the account posts in Spanish).
    title: Mapped[str] = mapped_column(String(255))  # internal grid label / promo headline
    # Sabías-que fact body. Sanitized on every write: plain text plus <b>…</b>
    # emphasis spans only (LLM emits them; anything else is escaped).
    fact: Mapped[str | None] = mapped_column(Text)
    # Handwritten (Caveat) caption under the polaroid photo.
    polaroid_caption: Mapped[str | None] = mapped_column(String(120))
    # Promo card lines: kicker above the headline ("Serie · 5 artículos") and
    # the author/meta line ("por Diego Raúl Feliu · 5 artículos").
    kicker: Mapped[str | None] = mapped_column(String(120))
    meta_text: Mapped[str | None] = mapped_column(String(200))
    # Full Instagram caption incl. hashtags (IG hard limit 2,200 chars).
    caption: Mapped[str | None] = mapped_column(Text)

    # Photo pick (edu pattern): image_query is only the picker's search hint;
    # the operator's pick lands in media_id + media_path (CDN URL embedded at
    # render — polaroid photo for facts, background for promos).
    image_query: Mapped[str | None] = mapped_column(String(255))
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id"), index=True)
    media_path: Mapped[str | None] = mapped_column(String(500))
    # Rendered 1080x1350 JPEG artifact on Spaces (public — the Graph API pulls it).
    image_url: Mapped[str | None] = mapped_column(String(500))

    # Publication (see module docstring). scheduled_at is indexed for the
    # scheduler's due-row scan.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publishing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_error: Mapped[str | None] = mapped_column(Text)
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ig_media_id: Mapped[str | None] = mapped_column(String(64))
    ig_permalink: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    research: Mapped[Research | None] = relationship()
    article: Mapped[Article | None] = relationship()
    media: Mapped[Media | None] = relationship()

    @property
    def derived_state(self) -> str:
        """Publication state computed from status + timestamps (see docstring)."""
        if self.published_at is not None:
            return "PUBLISHED"
        if self.publish_error:
            return "FAILED"
        if self.publishing_started_at is not None:
            return "PUBLISHING"
        if self.status != ContentStatus.APPROVED.value:
            return "DRAFT"
        if self.scheduled_at is not None:
            return "SCHEDULED"
        return "APPROVED"
