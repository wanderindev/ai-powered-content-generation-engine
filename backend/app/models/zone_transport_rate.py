"""Transport pricing between zones."""

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ZoneTransportRate(Base):
    """
    Transport pricing between zones.

    Rates vary by:
    - Origin zone (from_zone_id)
    - Destination zone (to_zone_id)
    - Group size (small: 1-3 pax, large: 4-7 pax)
    """

    __tablename__ = "zone_transport_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    to_zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    rate_small_group: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # 1-3 pax
    rate_large_group: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # 4-7 pax

    __table_args__ = (
        UniqueConstraint("from_zone_id", "to_zone_id", name="uq_zone_transport_rates"),
    )
