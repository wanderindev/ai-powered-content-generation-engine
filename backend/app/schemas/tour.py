"""Tour and attraction schemas."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.zone import ZoneBase


class AttractionBase(BaseModel):
    """Attraction with ticket prices."""

    id: int
    name: str
    name_es: str
    ticket_adult_tourist: Decimal
    ticket_adult_resident: Decimal
    ticket_senior_tourist: Decimal
    ticket_senior_resident: Decimal
    ticket_child_5_10_tourist: Decimal
    ticket_child_5_10_resident: Decimal
    ticket_child_10_17_tourist: Decimal
    ticket_child_10_17_resident: Decimal
    ticket_infant_tourist: Decimal
    ticket_infant_resident: Decimal
    notes: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AttractionListItem(BaseModel):
    """Attraction data for list views (localized)."""

    id: int
    name: str
    ticket_adult_tourist: Decimal
    ticket_adult_resident: Decimal
    ticket_senior_tourist: Decimal
    ticket_senior_resident: Decimal
    ticket_child_5_10_tourist: Decimal
    ticket_child_5_10_resident: Decimal
    ticket_child_10_17_tourist: Decimal
    ticket_child_10_17_resident: Decimal
    ticket_infant_tourist: Decimal
    ticket_infant_resident: Decimal
    notes: str | None = None
    is_optional: bool = False

    model_config = ConfigDict(from_attributes=True)


class TourListItem(BaseModel):
    """Tour data for list views."""

    id: int
    slug: str
    name: str
    name_es: str
    duration_hours: Decimal
    max_group_size: int
    zone_id: int

    model_config = ConfigDict(from_attributes=True)


class TourDetail(BaseModel):
    """Full tour data for detail view."""

    id: int
    slug: str
    name: str
    name_es: str
    description: str | None = None
    description_es: str | None = None
    duration_hours: Decimal
    max_group_size: int
    zone_id: int
    zone: ZoneBase
    attractions: list[AttractionListItem]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AvailabilitySlot(BaseModel):
    """Availability for a single date."""

    date: date
    am_available: bool
    pm_available: bool
