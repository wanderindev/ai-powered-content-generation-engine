"""
Pricing engine for tour booking system.

Calculates:
- Guide service cost (base fee + hourly rates by position)
- Senior discounts (20% for seniors)
- Attraction ticket costs (by age category and residency)
- Transport costs (by zone)
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import PricingConfig, Tour, ZoneTransportRate
from app.schemas.booking import (
    AttractionTickets,
    ParticipantBreakdown,
    PriceBreakdown,
    TicketLineItem,
    TransportRequest,
)


class PricingEngine:
    """
    Calculate prices for tour bookings.

    Pricing rules:
    - Base fee: $10 per tour
    - Hourly rates by person position (children under 5 are not charged and do
      not occupy a position):
      - 1st person: $10/hour
      - 2nd-3rd person: $6/hour
      - 4th-5th person: $5/hour
      - 6th-7th person: $4/hour
      - 8th-9th person: $3/hour
      - 10th-15th person: $2/hour
    - Senior discount: 20% off service fee for seniors (women 55+, men 60+)
    - Tickets: Vary by attraction, age category, and residency
    - Transport:
      - 1-4 pax: zone matrix (origin zone -> tour zone, small/large group)
      - 5-8 pax: flat $20/hour x (tour duration + 1h), round-trip only
      - 9-15 pax: flat $30/hour x (tour duration + 1h), round-trip only
    - Direct booking is capped at tour.max_group_size (15 for private tours);
      larger groups require a custom quote.
    """

    def __init__(self, db: Session):
        self.db = db
        self._config: dict[str, Decimal] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load pricing configuration from database."""
        configs = self.db.query(PricingConfig).all()
        self._config = {c.key: Decimal(c.value) for c in configs}

    def get_config(self, key: str, default: Decimal = Decimal("0")) -> Decimal:
        """Get a config value."""
        return self._config.get(key, default)

    def calculate_service_cost(
        self,
        tour: Tour,
        participants: ParticipantBreakdown,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Calculate guide service cost.

        Args:
            tour: Tour being booked
            participants: Breakdown of participants

        Returns:
            Tuple of (service_total, per_pax_cost, senior_discount)
        """
        total_pax = participants.total
        if total_pax == 0:
            return Decimal("0"), Decimal("0"), Decimal("0")

        if total_pax > tour.max_group_size:
            # Requires custom quote - return -1 to indicate
            return Decimal("-1"), Decimal("-1"), Decimal("0")

        # Rates by position (1-indexed). Children under 5 are not charged and do
        # not occupy a billable position, so the ladder is indexed by the count
        # of billable participants (everyone aged 5+).
        rate_2_3 = self.get_config("hourly_rate_pax_2_3", Decimal("6"))
        rate_4_5 = self.get_config("hourly_rate_pax_4_5", Decimal("5"))
        rate_6_7 = self.get_config("hourly_rate_pax_6_7", Decimal("4"))
        rate_8_9 = self.get_config("hourly_rate_pax_8_9", Decimal("3"))
        rate_10_15 = self.get_config("hourly_rate_pax_10_15", Decimal("2"))
        hourly_rates = [
            self.get_config("hourly_rate_pax_1", Decimal("10")),  # 1st person
            rate_2_3,  # 2nd person
            rate_2_3,  # 3rd person
            rate_4_5,  # 4th person
            rate_4_5,  # 5th person
            rate_6_7,  # 6th person
            rate_6_7,  # 7th person
            rate_8_9,  # 8th person
            rate_8_9,  # 9th person
            rate_10_15,  # 10th person
            rate_10_15,  # 11th person
            rate_10_15,  # 12th person
            rate_10_15,  # 13th person
            rate_10_15,  # 14th person
            rate_10_15,  # 15th person
        ]

        billable_pax = total_pax - participants.child_under_5.total
        if billable_pax > len(hourly_rates):
            # More billable participants than the rate ladder supports.
            return Decimal("-1"), Decimal("-1"), Decimal("0")

        # Base fee + hourly rates by position
        base_fee = self.get_config("base_fee", Decimal("10"))
        hours = tour.duration_hours

        # Calculate total service cost (only billable participants are charged)
        service_total = base_fee
        for i in range(billable_pax):
            service_total += hourly_rates[i] * hours

        # Per-person cost (for display) - averaged over paying participants only
        per_pax_cost = service_total / billable_pax if billable_pax > 0 else Decimal("0")

        # Senior discount
        senior_count = participants.senior_count
        discount_percent = self.get_config("senior_discount_percent", Decimal("20")) / 100
        senior_discount = per_pax_cost * senior_count * discount_percent

        return service_total, per_pax_cost, senior_discount

    def calculate_tickets_cost(
        self,
        tour: Tour,
        participants: ParticipantBreakdown,
    ) -> tuple[Decimal, list[AttractionTickets]]:
        """
        Calculate ticket costs for all attractions.

        Handles mixed groups with both residents and tourists.

        Args:
            tour: Tour being booked
            participants: Breakdown of participants

        Returns:
            Tuple of (total, breakdown_list)
        """
        total = Decimal("0")
        breakdown: list[AttractionTickets] = []

        for tour_attraction in tour.attractions:
            attraction = tour_attraction.attraction
            if not attraction.is_active:
                continue

            attraction_total = Decimal("0")
            items: list[TicketLineItem] = []

            def add_ticket_item(
                category: str,
                resident_count: int,
                tourist_count: int,
                resident_price: Decimal,
                tourist_price: Decimal,
            ) -> None:
                nonlocal attraction_total

                if resident_count > 0 and resident_price > 0:
                    cost = resident_price * resident_count
                    attraction_total += cost
                    items.append(
                        TicketLineItem(
                            category=f"{category} (Resident)",
                            quantity=resident_count,
                            unit_price=resident_price,
                            total=cost,
                        )
                    )

                if tourist_count > 0 and tourist_price > 0:
                    cost = tourist_price * tourist_count
                    attraction_total += cost
                    items.append(
                        TicketLineItem(
                            category=f"{category} (Tourist)",
                            quantity=tourist_count,
                            unit_price=tourist_price,
                            total=cost,
                        )
                    )

            # Seniors (combine male and female for ticket purposes)
            add_ticket_item(
                "Senior",
                participants.senior_resident_count,
                participants.senior_tourist_count,
                attraction.ticket_senior_resident,
                attraction.ticket_senior_tourist,
            )

            # Adults
            add_ticket_item(
                "Adult",
                participants.adult.resident,
                participants.adult.tourist,
                attraction.ticket_adult_resident,
                attraction.ticket_adult_tourist,
            )

            # Children 10-17
            add_ticket_item(
                "Child (10-17)",
                participants.child_10_17.resident,
                participants.child_10_17.tourist,
                attraction.ticket_child_10_17_resident,
                attraction.ticket_child_10_17_tourist,
            )

            # Children 5-10
            add_ticket_item(
                "Child (5-10)",
                participants.child_5_10.resident,
                participants.child_5_10.tourist,
                attraction.ticket_child_5_10_resident,
                attraction.ticket_child_5_10_tourist,
            )

            # Infants (usually free, but handle if not)
            add_ticket_item(
                "Child (under 5)",
                participants.child_under_5.resident,
                participants.child_under_5.tourist,
                attraction.ticket_infant_resident,
                attraction.ticket_infant_tourist,
            )

            if items:
                breakdown.append(
                    AttractionTickets(
                        attraction=attraction.name,
                        items=items,
                        total=attraction_total,
                    )
                )
            total += attraction_total

        return total, breakdown

    def calculate_transport_cost(
        self,
        tour: Tour,
        participants: ParticipantBreakdown,
        transport: TransportRequest | None,
        hotel_zone_id: int | None,
    ) -> tuple[Decimal, bool]:
        """
        Calculate transport cost.

        Args:
            tour: Tour being booked
            participants: Breakdown of participants
            transport: Transport request details
            hotel_zone_id: Zone ID of the hotel

        Pricing tiers by group size:
        - 1-4 pax: zone matrix (hotel zone -> tour zone, small/large group rate),
          one-way or doubled for round trips.
        - 5-8 pax: flat hourly rate x (tour duration + 1h), round-trip only.
        - 9-15 pax: higher flat hourly rate x (tour duration + 1h), round-trip only.

        The flat tiers are distance-independent, so they do not require a hotel
        zone and never set quote_required.

        Returns:
            Tuple of (cost, quote_required)
        """
        if not transport:
            return Decimal("0"), False

        total_pax = participants.total

        if total_pax > 4:
            # Flat hourly tiers (round-trip only; direction is enforced upstream).
            extra_hours = self.get_config("transport_extra_hours", Decimal("1"))
            hours = tour.duration_hours + extra_hours
            if total_pax <= 8:
                hourly_rate = self.get_config("transport_hourly_rate_5_8", Decimal("20"))
            else:
                hourly_rate = self.get_config("transport_hourly_rate_9_15", Decimal("30"))
            return hourly_rate * hours, False

        # 1-4 pax: zone matrix
        if not hotel_zone_id:
            # Hotel not in database, quote required
            return Decimal("0"), True

        tour_zone_id = tour.zone_id
        is_small_group = total_pax <= 3

        # Get rate for hotel zone -> tour zone
        rate = (
            self.db.query(ZoneTransportRate)
            .filter(
                ZoneTransportRate.from_zone_id == hotel_zone_id,
                ZoneTransportRate.to_zone_id == tour_zone_id,
            )
            .first()
        )

        if not rate:
            return Decimal("0"), True  # Quote required

        one_way_rate = rate.rate_small_group if is_small_group else rate.rate_large_group

        if transport.direction == "BOTH":
            return one_way_rate * 2, False
        else:
            return one_way_rate, False

    def calculate_full_price(
        self,
        tour: Tour,
        participants: ParticipantBreakdown,
        transport: TransportRequest | None = None,
        hotel_zone_id: int | None = None,
    ) -> PriceBreakdown:
        """
        Calculate complete price breakdown for a booking.

        Args:
            tour: Tour being booked
            participants: Breakdown of participants
            transport: Transport request details (optional)
            hotel_zone_id: Zone ID of the hotel (required if transport)

        Returns:
            Complete price breakdown
        """
        if participants.total > tour.max_group_size:
            return PriceBreakdown(
                service_total=Decimal("0"),
                service_per_pax=Decimal("0"),
                senior_discount=Decimal("0"),
                tickets_total=Decimal("0"),
                tickets_breakdown=[],
                transport_total=Decimal("0"),
                transport_quote_required=False,
                grand_total=Decimal("0"),
                requires_quote=True,
            )

        service_total, per_pax, senior_discount = self.calculate_service_cost(tour, participants)
        tickets_total, tickets_breakdown = self.calculate_tickets_cost(tour, participants)
        transport_total, transport_quote_required = self.calculate_transport_cost(
            tour, participants, transport, hotel_zone_id
        )

        grand_total = service_total - senior_discount + tickets_total + transport_total

        return PriceBreakdown(
            service_total=service_total,
            service_per_pax=per_pax,
            senior_discount=senior_discount,
            tickets_total=tickets_total,
            tickets_breakdown=tickets_breakdown,
            transport_total=transport_total,
            transport_quote_required=transport_quote_required,
            grand_total=grand_total,
            requires_quote=False,
        )


def calculate_fixed_price(
    config: dict,
    participants: ParticipantBreakdown,
) -> PriceBreakdown:
    """
    Calculate price for a fixed-price tour (open group).

    Config format:
        {
            "adult": 20.00,        # price for 10+ (adults, children 10-17)
            "child_5_10": 12.50,
            "child_under_5": 0,
            "senior_discount_pct": 20
        }

    Seniors pay the adult price minus the discount percentage.
    """
    adult_price = Decimal(str(config.get("adult", 0)))
    child_5_10_price = Decimal(str(config.get("child_5_10", 0)))
    child_under_5_price = Decimal(str(config.get("child_under_5", 0)))
    senior_discount_pct = Decimal(str(config.get("senior_discount_pct", 0))) / 100

    # Count participants
    senior_count = participants.senior_count
    adult_count = participants.adult.total + participants.child_10_17.total
    child_5_10_count = participants.child_5_10.total
    child_under_5_count = participants.child_under_5.total

    # Calculate totals
    senior_subtotal = senior_count * adult_price
    adult_subtotal = adult_count * adult_price
    child_5_10_subtotal = child_5_10_count * child_5_10_price
    child_under_5_subtotal = child_under_5_count * child_under_5_price

    service_total = senior_subtotal + adult_subtotal + child_5_10_subtotal + child_under_5_subtotal
    senior_discount = senior_subtotal * senior_discount_pct
    grand_total = service_total - senior_discount

    paying_count = senior_count + adult_count + child_5_10_count
    per_pax = adult_price if paying_count > 0 else Decimal("0")

    return PriceBreakdown(
        service_total=service_total,
        service_per_pax=per_pax,
        senior_discount=senior_discount,
        tickets_total=Decimal("0"),
        tickets_breakdown=[],
        transport_total=Decimal("0"),
        transport_quote_required=False,
        grand_total=grand_total,
        requires_quote=False,
    )


def get_pricing_engine(db: Session) -> PricingEngine:
    """Get a pricing engine instance."""
    return PricingEngine(db)
