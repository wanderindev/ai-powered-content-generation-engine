"""Tour model and tour-attraction association."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.attraction import Attraction
    from app.models.zone import Zone


class Tour(Base):
    """
    Tour definitions.

    Each tour has:
    - Duration in hours (for pricing calculation)
    - Maximum group size (default 7)
    - Zone where the tour takes place (for transport calculation)
    - Associated attractions with ticket prices
    """

    __tablename__ = "tours"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_es: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_es: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    max_group_size: Mapped[int] = mapped_column(Integer, default=15)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    fixed_price_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    zone: Mapped["Zone"] = relationship("Zone")
    attractions: Mapped[list["TourAttraction"]] = relationship(
        "TourAttraction", back_populates="tour"
    )


class TourAttraction(Base):
    """
    Many-to-many linking tours to attractions.

    Allows specifying:
    - Whether the attraction is optional (add-on)
    - Visit order within the tour
    """

    __tablename__ = "tour_attractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tour_id: Mapped[int] = mapped_column(ForeignKey("tours.id"), nullable=False)
    attraction_id: Mapped[int] = mapped_column(
        ForeignKey("attractions.id"), nullable=False, index=True
    )
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False)
    visit_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    tour: Mapped["Tour"] = relationship("Tour", back_populates="attractions")
    attraction: Mapped["Attraction"] = relationship("Attraction")

    __table_args__ = (UniqueConstraint("tour_id", "attraction_id", name="uq_tour_attractions"),)
