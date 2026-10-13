"""EduSlide model — one row per slide of a generated narrated deck (#320).

The structured deck produced by ``generate_edu_slides`` lands here (one row per
slide) rather than as markdown on ``EduMaterial.content``, so the downstream
stages have a per-slide home: the renderer (#321) fills ``image_url`` from
``image_query``, and the audio stage (#322) fills ``audio_url`` + ``duration_ms``
from ``narration``. Rows belong to the ``slides`` ``EduMaterial`` row via
``material_id`` and are ordered by ``position``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import SlideTemplate, status_check

if TYPE_CHECKING:
    from app.models.edu_material import EduMaterial


class EduSlide(Base):
    __tablename__ = "edu_slides"
    __table_args__ = (
        status_check("edu_slides", "template", SlideTemplate),
        UniqueConstraint("material_id", "position", name="uq_edu_slides_material_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("edu_materials.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # 1-based slide order
    template: Mapped[str] = mapped_column(String(40))
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)  # on-screen content, newline-separated lines
    image_query: Mapped[str | None] = mapped_column(String(500))  # wanted-photo hint for the editor
    narration: Mapped[str | None] = mapped_column(Text)  # spoken script; fed to TTS in #322

    # Human-picked photo (same pattern as guide image blocks): generation only
    # ever emits image_query; an editor picks the real Media in the admin. The
    # renderer embeds media_path (an absolute CDN URL) — no auto-matching.
    media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL"), index=True
    )
    media_path: Mapped[str | None] = mapped_column(String(500))

    # Per-slide artifacts filled by later stages (nullable until then).
    image_url: Mapped[str | None] = mapped_column(String(500))  # #321 (render)
    audio_url: Mapped[str | None] = mapped_column(String(500))  # #322 (narration audio)
    duration_ms: Mapped[int | None] = mapped_column(Integer)  # #322 (for slide timing in #323)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    material: Mapped[EduMaterial] = relationship(back_populates="slides")
