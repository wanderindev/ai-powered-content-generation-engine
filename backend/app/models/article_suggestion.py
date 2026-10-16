"""ArticleSuggestion model mapping to existing article_suggestions table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ContentStatus

if TYPE_CHECKING:
    from app.models.category import Category


# The #630 columns kept off the mapper (see ``__table_args__``).
DEDUP_COLUMNS = (
    "embedding",
    "duplicate_article_id",
    "duplicate_suggestion_id",
    "duplicate_similarity",
)


class ArticleSuggestion(Base):
    __tablename__ = "article_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    title: Mapped[str] = mapped_column(String(255))
    title_es: Mapped[str | None] = mapped_column(String(255))
    main_topic: Mapped[str | None] = mapped_column(Text)
    sub_topics: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    point_of_view: Mapped[str] = mapped_column(Text)
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

    # --- Possible-duplicate flag + dedup vector (#630, migration 0045) ---------
    # Advisory only: set by suggestion generation when SUGGESTION_DEDUP_ENABLED
    # is on and the suggestion's vector is close to an article centroid or an
    # existing suggestion. Never blocks or deletes anything. Two nullable FKs
    # (not a type + id pair) so each keeps referential integrity and its own
    # ON DELETE SET NULL; the CHECK keeps at most one set.
    #
    # All four columns are on the Table (create_all, tests) but are NOT ORM
    # attributes (``exclude_properties``): no entity query on ArticleSuggestion
    # names them, so every ORM read works against a database that has not run
    # 0045 yet (the deploy window before ``alembic upgrade head``). Only
    # app/services/suggestion_dedup.py and the admin ``_duplicate_fields`` read
    # or write them, through ``__table__.c`` with Core statements.
    #
    # They are declared in ``__table_args__`` rather than the class body so no
    # class attribute exists at all: an unmapped class-body Column would stay on
    # the class (and every instance) as a raw ``Column`` object, which breaks a
    # ``from_attributes`` schema naming one of these fields (#659).
    #
    # use_alter: articles -> research -> article_suggestions -> articles is a
    # metadata-level FK cycle (mapping does not matter); the name is Postgres'
    # default, i.e. what migration 0045 creates.
    __table_args__ = (
        Column(
            "duplicate_article_id",
            Integer,
            ForeignKey(
                "articles.id",
                ondelete="SET NULL",
                use_alter=True,
                name="article_suggestions_duplicate_article_id_fkey",
            ),
            nullable=True,
            index=True,
        ),
        Column(
            "duplicate_suggestion_id",
            Integer,
            ForeignKey("article_suggestions.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        Column("duplicate_similarity", Float, nullable=True),
        # voyage-4 document vector of title + main topic, written by the dedup step.
        Column("embedding", Vector(1024), nullable=True),
        CheckConstraint(
            "num_nonnulls(duplicate_article_id, duplicate_suggestion_id) <= 1",
            name="ck_article_suggestions_single_duplicate",
        ),
    )
    __mapper_args__ = {"exclude_properties": list(DEDUP_COLUMNS)}

    # Relationships
    category: Mapped[Category] = relationship()
