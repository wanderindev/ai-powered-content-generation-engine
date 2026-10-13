"""Attraction model with ticket pricing."""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.zone_attraction_rate import ZoneAttractionRate


class Attraction(Base):
    """
    Places visited with ticket pricing.

    Ticket prices vary by:
    - Age category: adult, senior, child 5-10, child 10-17, infant under 5
    - Residency status (tourist vs. Panamanian/resident)
    """

    __tablename__ = "attractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_es: Mapped[str] = mapped_column(String(200), nullable=False)

    # Ticket prices by category - Tourist
    ticket_adult_tourist: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    ticket_senior_tourist: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )
    ticket_child_5_10_tourist: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )  # ages 5-10
    ticket_child_10_17_tourist: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )  # ages 10-17
    ticket_infant_tourist: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )  # under 5

    # Ticket prices by category - Resident/Panamanian
    ticket_adult_resident: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )
    ticket_senior_resident: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )
    ticket_child_5_10_resident: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )  # ages 5-10
    ticket_child_10_17_resident: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )  # ages 10-17
    ticket_infant_resident: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )

    # Minimum age for full access (e.g., 10 for bell tower)
    min_age: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    zone_rates: Mapped[list["ZoneAttractionRate"]] = relationship(
        "ZoneAttractionRate", back_populates="attraction"
    )
