"""Geographic zones for transport pricing."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.hotel import Hotel
    from app.models.zone_attraction_rate import ZoneAttractionRate


class Zone(Base):
    """
    Geographic zone for transport pricing.

    Zones:
    1. Casco Antiguo / San Felipe
    2. Calidonia / Bella Vista / Marbella
    3. Obarrio / El Cangrejo / Condado del Rey
    4. Costa del Este / Panama Pacifico
    5. Tocumen / Airport Area
    6. Clayton / Albrook / Amador
    """

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_es: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description_es: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    hotels: Mapped[list["Hotel"]] = relationship("Hotel", back_populates="zone")
    attraction_rates: Mapped[list["ZoneAttractionRate"]] = relationship(
        "ZoneAttractionRate", back_populates="zone"
    )
