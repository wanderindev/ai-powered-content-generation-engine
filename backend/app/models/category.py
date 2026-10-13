from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.article import Article


class Taxonomy(Base):
    __tablename__ = "taxonomies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    name_es: Mapped[str | None] = mapped_column(String(200))
    description_es: Mapped[str | None] = mapped_column(Text)

    # Relationships
    categories: Mapped[list["Category"]] = relationship(back_populates="taxonomy")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    taxonomy_id: Mapped[int] = mapped_column(ForeignKey("taxonomies.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    is_long_form: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    name_es: Mapped[str | None] = mapped_column(String(200))
    description_es: Mapped[str | None] = mapped_column(Text)

    # Relationships
    taxonomy: Mapped["Taxonomy"] = relationship(back_populates="categories")
    articles: Mapped[list["Article"]] = relationship(back_populates="category")
