"""Pricing configuration key-value store."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PricingConfig(Base):
    """
    Key-value store for pricing parameters that may change.

    Keys include:
    - base_fee: Base fee per tour
    - hourly_rate_pax_1: Hourly rate for 1st person
    - hourly_rate_pax_2_3: Hourly rate for 2nd-3rd person
    - hourly_rate_pax_4_5: Hourly rate for 4th-5th person
    - hourly_rate_pax_6_7: Hourly rate for 6th-7th person
    - hourly_rate_pax_8_9: Hourly rate for 8th-9th person
    - hourly_rate_pax_10_15: Hourly rate for 10th-15th person
    - senior_discount_percent: Senior citizen discount percentage
    - transport_hourly_rate_5_8: Flat hourly transport rate for 5-8 pax groups
    - transport_hourly_rate_9_15: Flat hourly transport rate for 9-15 pax groups
    - transport_extra_hours: Extra hours added to tour duration for flat transport
    - payment_link_validity_hours: Hours before payment link expires
    - reservation_validity_days: Days to hold unpaid reservation
    - followup_1_hours: Hours after link expiry for 1st followup
    - followup_2_hours: Hours after link expiry for 2nd followup
    - followup_3_hours: Hours after link expiry for 3rd followup
    """

    __tablename__ = "pricing_config"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
