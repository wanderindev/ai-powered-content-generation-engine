"""Zone and hotel schemas."""

from pydantic import BaseModel, ConfigDict


class ZoneBase(BaseModel):
    """Zone data for transport pricing."""

    id: int
    name: str
    name_es: str
    description: str | None = None
    description_es: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ZoneListItem(BaseModel):
    """Zone data for list views (localized)."""

    id: int
    name: str
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class HotelBase(BaseModel):
    """Hotel data."""

    id: int
    name: str
    name_es: str | None = None
    address: str | None = None
    address_es: str | None = None
    zone_id: int

    model_config = ConfigDict(from_attributes=True)


class HotelSearchResult(BaseModel):
    """Hotel search result for autocomplete."""

    id: int
    name: str
    address: str | None = None
    zone_id: int
    zone_name: str

    model_config = ConfigDict(from_attributes=True)
