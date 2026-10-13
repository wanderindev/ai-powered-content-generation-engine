"""Hotel model for transport zone assignment."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.zone import Zone


class Hotel(Base):
    """
    Pre-populated hotels with zone assignments.

    Used for autocomplete in booking form to determine transport pricing.
    Hotels not in the list require manual transport quote.
    """

    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_es: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    address_es: Mapped[str | None] = mapped_column(String(500), nullable=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    zone: Mapped["Zone"] = relationship("Zone", back_populates="hotels")
