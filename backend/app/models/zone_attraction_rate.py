"""Zone to Attraction transport rate model."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.attraction import Attraction
    from app.models.zone import Zone


class ZoneAttractionRate(Base):
    """Transport rate from a zone to an attraction based on group size."""

    __tablename__ = "zone_attraction_rates"
    __table_args__ = (UniqueConstraint("zone_id", "attraction_id", name="uq_zone_attraction_rate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(Integer, ForeignKey("zones.id"), nullable=False)
    attraction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("attractions.id"), nullable=False, index=True
    )
    rate_small: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # 1-3 pax
    rate_medium: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # 4-6 pax
    rate_large: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # 7+ pax

    # Relationships
    zone: Mapped["Zone"] = relationship("Zone", back_populates="attraction_rates")
    attraction: Mapped["Attraction"] = relationship("Attraction", back_populates="zone_rates")

    def get_rate(self, passenger_count: int) -> float:
        """Get the appropriate rate based on passenger count."""
        if passenger_count <= 3:
            return float(self.rate_small)
        elif passenger_count <= 6:
            return float(self.rate_medium)
        else:
            return float(self.rate_large)
