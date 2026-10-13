"""EduSuggestion model for educational content topics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, DateTime, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ContentStatus


class EduSuggestion(Base):
    __tablename__ = "edu_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    excursion_key: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    grade_bands: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    sub_topics: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    status: Mapped[str] = mapped_column(
        SAEnum(*ContentStatus.values(), name="content_status_type", create_type=False),
        default=ContentStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
