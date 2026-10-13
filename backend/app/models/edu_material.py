"""EduMaterial model for educational deliverables."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ContentStatus, MaterialType, status_check

if TYPE_CHECKING:
    from app.models.edu_research import EduResearch
    from app.models.edu_slide import EduSlide
    from app.models.research import Research


class EduMaterial(Base):
    __tablename__ = "edu_materials"
    __table_args__ = (
        status_check("edu_materials", "material_type", MaterialType),
        # Natural key of a generated material; backstops the read-then-write
        # upsert in edu_material_generation against concurrent duplicate inserts
        # (#97). Uploaded rows have research_id NULL (they key off
        # blog_research_id) so, under Postgres' default NULLS DISTINCT, they
        # never collide on this constraint.
        UniqueConstraint(
            "research_id",
            "material_type",
            "grade_band",
            name="uq_edu_materials_research_type_band",
        ),
        # Discriminated union: exactly one research FK is set (#95). Generated
        # materials use research_id; uploaded materials use blog_research_id.
        CheckConstraint(
            "num_nonnulls(research_id, blog_research_id) = 1",
            name="ck_edu_materials_one_research_fk",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    research_id: Mapped[int | None] = mapped_column(ForeignKey("edu_research.id"), index=True)
    blog_research_id: Mapped[int | None] = mapped_column(ForeignKey("research.id"), index=True)
    material_type: Mapped[str] = mapped_column(String(20))
    grade_band: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str | None] = mapped_column(Text)
    content_es: Mapped[str | None] = mapped_column(Text)
    # Structured study-guide blocks (#340): the canonical source for the branded
    # PDF (cover/section/paragraph/bullets/ordered_list/quote/map/timeline/image).
    # ``content``/``content_es`` hold a markdown projection of these for the admin
    # UI, search, and back-compat. NULL for non-guide materials and legacy guides.
    content_blocks: Mapped[list | None] = mapped_column(JSONB)
    content_blocks_es: Mapped[list | None] = mapped_column(JSONB)
    # For slides materials: the condensed research briefing the deck was generated
    # from, persisted so the narration pass works from the SAME facts the human
    # approved (no re-condense drift). NULL for other types and legacy decks.
    source_briefing: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(String(500))
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    # Playback length for video materials (#323); NULL for non-video types.
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        SAEnum(*ContentStatus.values(), name="content_status_type", create_type=False),
        default=ContentStatus.PENDING.value,
    )
    # Publication is separate from approval (mirrors Article.published_at, #326):
    # a material is public only when it is APPROVED *and* published_at is set and
    # in the past. NULL = not published; a future value = scheduled. The admin
    # Publish/Unpublish action sets/clears this; the public delivery API filters
    # on it. Indexed because that public query filters on it.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    research: Mapped[EduResearch | None] = relationship()
    blog_research: Mapped[Research | None] = relationship()
    # Structured slides for a slides-type material (#320). Empty for other types.
    slides: Mapped[list[EduSlide]] = relationship(
        back_populates="material",
        cascade="all, delete-orphan",
        order_by="EduSlide.position",
    )

    @property
    def suggestion_title(self) -> str:
        """Get the suggestion title from whichever research is linked."""
        if self.research and self.research.suggestion:
            return self.research.suggestion.title
        if self.blog_research and self.blog_research.suggestion:
            return self.blog_research.suggestion.title
        return "Unknown"
