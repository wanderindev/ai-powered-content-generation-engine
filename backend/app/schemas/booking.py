"""Booking and pricing schemas."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class ParticipantCategory(BaseModel):
    """Counts for a single age category, split by residency."""

    resident: int = Field(default=0, ge=0, description="Panamanian or Panama resident")
    tourist: int = Field(default=0, ge=0, description="Foreign visitor")

    @property
    def total(self) -> int:
        return self.resident + self.tourist


class ParticipantBreakdown(BaseModel):
    """
    Complete breakdown of participants by age category and residency.

    Age categories:
    - senior_female: Women 55+
    - senior_male: Men 60+
    - adult: 18 to senior age
    - child_10_17: Children 10-17
    - child_5_10: Children 5-10
    - child_under_5: Children under 5

    This supports mixed groups where some members are residents and others are tourists.
    """

    senior_female: ParticipantCategory = Field(
        default_factory=ParticipantCategory, description="Women 55+"
    )
    senior_male: ParticipantCategory = Field(
        default_factory=ParticipantCategory, description="Men 60+"
    )
    adult: ParticipantCategory = Field(
        default_factory=ParticipantCategory, description="18 to senior age"
    )
    child_10_17: ParticipantCategory = Field(
        default_factory=ParticipantCategory, description="Children 10-17"
    )
    child_5_10: ParticipantCategory = Field(
        default_factory=ParticipantCategory, description="Children 5-10"
    )
    child_under_5: ParticipantCategory = Field(
        default_factory=ParticipantCategory, description="Children under 5"
    )

    @property
    def total(self) -> int:
        return (
            self.senior_female.total
            + self.senior_male.total
            + self.adult.total
            + self.child_10_17.total
            + self.child_5_10.total
            + self.child_under_5.total
        )

    @property
    def total_residents(self) -> int:
        return (
            self.senior_female.resident
            + self.senior_male.resident
            + self.adult.resident
            + self.child_10_17.resident
            + self.child_5_10.resident
            + self.child_under_5.resident
        )

    @property
    def total_tourists(self) -> int:
        return (
            self.senior_female.tourist
            + self.senior_male.tourist
            + self.adult.tourist
            + self.child_10_17.tourist
            + self.child_5_10.tourist
            + self.child_under_5.tourist
        )

    @property
    def senior_count(self) -> int:
        return self.senior_female.total + self.senior_male.total

    @property
    def senior_resident_count(self) -> int:
        return self.senior_female.resident + self.senior_male.resident

    @property
    def senior_tourist_count(self) -> int:
        return self.senior_female.tourist + self.senior_male.tourist

    @model_validator(mode="after")
    def validate_at_least_one_participant(self) -> "ParticipantBreakdown":
        if self.total == 0:
            raise ValueError("At least one participant is required")
        return self

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict for storage."""
        return {
            "senior_female": {
                "resident": self.senior_female.resident,
                "tourist": self.senior_female.tourist,
            },
            "senior_male": {
                "resident": self.senior_male.resident,
                "tourist": self.senior_male.tourist,
            },
            "adult": {"resident": self.adult.resident, "tourist": self.adult.tourist},
            "child_10_17": {
                "resident": self.child_10_17.resident,
                "tourist": self.child_10_17.tourist,
            },
            "child_5_10": {
                "resident": self.child_5_10.resident,
                "tourist": self.child_5_10.tourist,
            },
            "child_under_5": {
                "resident": self.child_under_5.resident,
                "tourist": self.child_under_5.tourist,
            },
        }


class TransportRequest(BaseModel):
    """Transport request details."""

    direction: str = Field(..., pattern="^(TO|FROM|BOTH)$", description="TO, FROM, or BOTH")
    hotel_id: int | None = Field(default=None, description="Hotel ID if in database")
    hotel_name_manual: str | None = Field(
        default=None, max_length=200, description="Hotel name if not in database"
    )

    @model_validator(mode="after")
    def validate_hotel(self) -> "TransportRequest":
        if self.hotel_id is None and not self.hotel_name_manual:
            raise ValueError("Either hotel_id or hotel_name_manual is required")
        return self


class PriceCalculationRequest(BaseModel):
    """Request to calculate price for a booking."""

    tour_slug: str
    participants: ParticipantBreakdown
    transport: TransportRequest | None = None


class TicketLineItem(BaseModel):
    """Single ticket line item in breakdown."""

    category: str
    quantity: int
    unit_price: Decimal
    total: Decimal


class AttractionTickets(BaseModel):
    """Tickets breakdown for one attraction."""

    attraction: str
    items: list[TicketLineItem]
    total: Decimal


class PriceBreakdown(BaseModel):
    """Complete price breakdown for a booking."""

    service_total: Decimal = Field(description="Guide service cost before discount")
    service_per_pax: Decimal = Field(description="Per-person service cost")
    senior_discount: Decimal = Field(description="Discount for senior participants")
    tickets_total: Decimal = Field(description="Total cost for all attraction tickets")
    tickets_breakdown: list[AttractionTickets] = Field(description="Breakdown by attraction")
    transport_total: Decimal = Field(description="Transport cost")
    transport_quote_required: bool = Field(
        default=False, description="True if hotel not in database"
    )
    grand_total: Decimal = Field(description="Final total")
    requires_quote: bool = Field(
        default=False,
        description="True if group size exceeds the tour max or other custom pricing",
    )


class BookingItemRequest(BaseModel):
    """Single booking item in cart/checkout."""

    tour_slug: str
    tour_date: date
    time_slot: str = Field(..., pattern="^(AM|PM)$")
    preferred_time: str | None = Field(default=None, max_length=20, description="e.g., '9:00 AM'")
    participants: ParticipantBreakdown
    transport: TransportRequest | None = None


class CheckAvailabilityRequest(BaseModel):
    """Request to check slot availability."""

    tour_slug: str
    tour_date: date
    time_slot: str = Field(..., pattern="^(AM|PM)$")


class CheckAvailabilityResponse(BaseModel):
    """Response for availability check."""

    available: bool
    reason: str | None = None
