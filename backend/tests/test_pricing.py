"""Tests for the pricing engine."""

from decimal import Decimal

import pytest
from app.models import (
    Attraction,
    PricingConfig,
    Tour,
    TourAttraction,
    Zone,
    ZoneTransportRate,
)
from app.schemas.booking import ParticipantBreakdown, ParticipantCategory, TransportRequest
from app.services.pricing import PricingEngine, calculate_fixed_price


@pytest.fixture
def pricing_config(test_db):
    """Seed pricing configuration."""
    configs = [
        PricingConfig(key="base_fee", value="10.00", description="Base fee per tour"),
        PricingConfig(key="hourly_rate_pax_1", value="10.00"),
        PricingConfig(key="hourly_rate_pax_2_3", value="6.00"),
        PricingConfig(key="hourly_rate_pax_4_5", value="5.00"),
        PricingConfig(key="hourly_rate_pax_6_7", value="4.00"),
        PricingConfig(key="hourly_rate_pax_8_9", value="3.00"),
        PricingConfig(key="hourly_rate_pax_10_15", value="2.00"),
        PricingConfig(key="senior_discount_percent", value="20"),
        PricingConfig(key="transport_hourly_rate_5_8", value="20.00"),
        PricingConfig(key="transport_hourly_rate_9_15", value="30.00"),
        PricingConfig(key="transport_extra_hours", value="1"),
    ]
    for config in configs:
        test_db.add(config)
    test_db.commit()
    return configs


@pytest.fixture
def zone(test_db):
    """Create a test zone."""
    zone = Zone(
        id=1,
        name="Test Zone",
        name_es="Zona de Prueba",
        description="Test zone for testing",
        display_order=1,
        is_active=True,
    )
    test_db.add(zone)
    test_db.commit()
    return zone


@pytest.fixture
def attraction(test_db):
    """Create a test attraction."""
    attraction = Attraction(
        id=1,
        name="Test Attraction",
        name_es="Atracción de Prueba",
        ticket_adult_tourist=Decimal("20.00"),
        ticket_senior_tourist=Decimal("18.00"),
        ticket_child_5_10_tourist=Decimal("10.00"),
        ticket_child_10_17_tourist=Decimal("10.00"),
        ticket_infant_tourist=Decimal("0.00"),
        ticket_adult_resident=Decimal("5.00"),
        ticket_senior_resident=Decimal("4.00"),
        ticket_child_5_10_resident=Decimal("3.00"),
        ticket_child_10_17_resident=Decimal("3.00"),
        ticket_infant_resident=Decimal("0.00"),
        is_active=True,
    )
    test_db.add(attraction)
    test_db.commit()
    return attraction


@pytest.fixture
def tour(test_db, zone, attraction):
    """Create a test tour with attraction."""
    tour = Tour(
        id=1,
        slug="test-tour",
        name="Test Tour",
        name_es="Tour de Prueba",
        duration_hours=Decimal("3.0"),
        max_group_size=15,
        zone_id=zone.id,
        is_active=True,
    )
    test_db.add(tour)
    test_db.flush()

    tour_attraction = TourAttraction(
        tour_id=tour.id,
        attraction_id=attraction.id,
        is_optional=False,
        visit_order=1,
    )
    test_db.add(tour_attraction)
    test_db.commit()
    test_db.refresh(tour)
    return tour


class TestPricingEngine:
    """Tests for PricingEngine class."""

    def test_calculate_service_cost_single_person(
        self, test_db, pricing_config, tour
    ):
        """Test service cost calculation for single person."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=1)
        )

        service_total, per_pax, senior_discount = engine.calculate_service_cost(
            tour, participants
        )

        # Base fee ($10) + 3 hours * $10/hour = $40
        assert service_total == Decimal("40.00")
        assert per_pax == Decimal("40.00")
        assert senior_discount == Decimal("0")

    def test_calculate_service_cost_two_people(
        self, test_db, pricing_config, tour
    ):
        """Test service cost calculation for two people."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=2)
        )

        service_total, per_pax, senior_discount = engine.calculate_service_cost(
            tour, participants
        )

        # Base fee ($10) + 3 hours * ($10 + $6) = $10 + $48 = $58
        assert service_total == Decimal("58.00")
        assert per_pax == Decimal("29.00")
        assert senior_discount == Decimal("0")

    def test_calculate_service_cost_with_seniors(
        self, test_db, pricing_config, tour
    ):
        """Test service cost with senior discount."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            senior_female=ParticipantCategory(resident=0, tourist=1),
            adult=ParticipantCategory(resident=0, tourist=1),
        )

        service_total, per_pax, senior_discount = engine.calculate_service_cost(
            tour, participants
        )

        # Base fee ($10) + 3 hours * ($10 + $6) = $10 + $48 = $58
        # Senior discount: $29 * 1 * 0.20 = $5.80
        assert service_total == Decimal("58.00")
        assert per_pax == Decimal("29.00")
        assert senior_discount == Decimal("5.80")

    def test_calculate_service_cost_group_over_max(
        self, test_db, pricing_config, tour
    ):
        """Test that groups over max_group_size (15) require a quote."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=16)
        )

        service_total, per_pax, senior_discount = engine.calculate_service_cost(
            tour, participants
        )

        # Should return -1 to indicate quote required
        assert service_total == Decimal("-1")
        assert per_pax == Decimal("-1")

    def test_calculate_service_cost_eight_people(
        self, test_db, pricing_config, tour
    ):
        """Eight people are now bookable (cap is 15) and use the new ladder."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=8)
        )

        service_total, per_pax, senior_discount = engine.calculate_service_cost(
            tour, participants
        )

        # Base $10 + 3h * (10 + 6 + 6 + 5 + 5 + 4 + 4 + 3) = 10 + 3*43 = 139
        assert service_total == Decimal("139.00")

    def test_calculate_service_cost_excludes_under_5(
        self, test_db, pricing_config, tour
    ):
        """Children under 5 are not charged and do not occupy a position."""
        engine = PricingEngine(test_db)

        # 2 adults + 2 toddlers should price identically to 2 adults alone.
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=2),
            child_under_5=ParticipantCategory(resident=0, tourist=2),
        )

        service_total, per_pax, senior_discount = engine.calculate_service_cost(
            tour, participants
        )

        # Base $10 + 3h * (10 + 6) = $58, averaged over the 2 billable pax.
        assert service_total == Decimal("58.00")
        assert per_pax == Decimal("29.00")

    def test_calculate_tickets_cost_all_tourists(
        self, test_db, pricing_config, tour
    ):
        """Test ticket calculation for all tourists."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=2),
            child_10_17=ParticipantCategory(resident=0, tourist=1),
        )

        total, breakdown = engine.calculate_tickets_cost(tour, participants)

        # 2 adults at $20 + 1 child at $10 = $50
        assert total == Decimal("50.00")
        assert len(breakdown) == 1  # One attraction

    def test_calculate_tickets_cost_mixed_group(
        self, test_db, pricing_config, tour
    ):
        """Test ticket calculation for mixed resident/tourist group."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=1, tourist=1),
        )

        total, breakdown = engine.calculate_tickets_cost(tour, participants)

        # 1 resident at $5 + 1 tourist at $20 = $25
        assert total == Decimal("25.00")

    def test_priced_infant_ticket_is_billed(
        self, test_db, pricing_config, tour, attraction
    ):
        """When an attraction charges under-5s (e.g. Panama Viejo $1), they bill."""
        attraction.ticket_infant_tourist = Decimal("1.00")
        attraction.ticket_infant_resident = Decimal("1.00")
        test_db.commit()

        engine = PricingEngine(test_db)
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=1),
            child_under_5=ParticipantCategory(resident=1, tourist=1),
        )

        total, breakdown = engine.calculate_tickets_cost(tour, participants)

        # 1 adult tourist ($20) + 1 infant resident ($1) + 1 infant tourist ($1)
        assert total == Decimal("22.00")

    def test_calculate_full_price(self, test_db, pricing_config, tour):
        """Test complete price calculation."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=1, tourist=1),
        )

        price = engine.calculate_full_price(tour, participants)

        # Service: $10 + 3 * ($10 + $6) = $58
        # No senior discount
        # Tickets: $5 + $20 = $25
        # No transport
        # Total: $83
        assert price.service_total == Decimal("58.00")
        assert price.senior_discount == Decimal("0")
        assert price.tickets_total == Decimal("25.00")
        assert price.transport_total == Decimal("0")
        assert price.grand_total == Decimal("83.00")
        assert not price.requires_quote

    def test_calculate_full_price_requires_quote_for_large_groups(
        self, test_db, pricing_config, tour
    ):
        """Test that large groups require a quote."""
        engine = PricingEngine(test_db)

        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=16),
        )

        price = engine.calculate_full_price(tour, participants)

        assert price.requires_quote is True
        assert price.grand_total == Decimal("0")

    def test_under_5_counts_toward_max_group_size(
        self, test_db, pricing_config, tour
    ):
        """Under-5 children are free for service but still count toward the cap."""
        engine = PricingEngine(test_db)

        # 14 adults + 2 toddlers = 16 total > 15 cap -> quote required.
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=14),
            child_under_5=ParticipantCategory(resident=0, tourist=2),
        )

        price = engine.calculate_full_price(tour, participants)

        assert price.requires_quote is True


class TestTransportCost:
    """Tests for tiered transport pricing."""

    def test_small_group_uses_zone_matrix(self, test_db, pricing_config, tour):
        """1-4 pax use the zone rate matrix (round trip doubles one-way)."""
        rate = ZoneTransportRate(
            from_zone_id=tour.zone_id,
            to_zone_id=tour.zone_id,
            rate_small_group=Decimal("25.00"),
            rate_large_group=Decimal("40.00"),
        )
        test_db.add(rate)
        test_db.commit()

        engine = PricingEngine(test_db)
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=2)
        )
        transport = TransportRequest(direction="BOTH", hotel_id=1)

        cost, quote_required = engine.calculate_transport_cost(
            tour, participants, transport, hotel_zone_id=tour.zone_id
        )

        # Small group (<=3) round trip: 25 * 2 = 50
        assert cost == Decimal("50.00")
        assert quote_required is False

    def test_four_pax_uses_large_group_rate(self, test_db, pricing_config, tour):
        """4 pax still use the zone matrix, at the large-group rate."""
        rate = ZoneTransportRate(
            from_zone_id=tour.zone_id,
            to_zone_id=tour.zone_id,
            rate_small_group=Decimal("25.00"),
            rate_large_group=Decimal("40.00"),
        )
        test_db.add(rate)
        test_db.commit()

        engine = PricingEngine(test_db)
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=4)
        )
        transport = TransportRequest(direction="BOTH", hotel_id=1)

        cost, quote_required = engine.calculate_transport_cost(
            tour, participants, transport, hotel_zone_id=tour.zone_id
        )

        # Large group round trip: 40 * 2 = 80
        assert cost == Decimal("80.00")
        assert quote_required is False

    def test_mid_group_flat_hourly(self, test_db, pricing_config, tour):
        """5-8 pax: flat $20/hr * (duration + 1h), zone-independent."""
        engine = PricingEngine(test_db)
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=6)
        )
        transport = TransportRequest(direction="BOTH", hotel_name_manual="Unknown Inn")

        cost, quote_required = engine.calculate_transport_cost(
            tour, participants, transport, hotel_zone_id=None
        )

        # 20 * (3 + 1) = 80, no quote required even without a hotel zone.
        assert cost == Decimal("80.00")
        assert quote_required is False

    def test_large_group_flat_hourly(self, test_db, pricing_config, tour):
        """9-15 pax: flat $30/hr * (duration + 1h)."""
        engine = PricingEngine(test_db)
        participants = ParticipantBreakdown(
            adult=ParticipantCategory(resident=0, tourist=10)
        )
        transport = TransportRequest(direction="BOTH", hotel_name_manual="Unknown Inn")

        cost, quote_required = engine.calculate_transport_cost(
            tour, participants, transport, hotel_zone_id=None
        )

        # 30 * (3 + 1) = 120
        assert cost == Decimal("120.00")
        assert quote_required is False

# The exact fixed_price_config stored in production for the siete-iglesias tour,
# which already took real bookings under the original two-key format.
LEGACY_CONFIG = {
    "adult": 20.00,
    "child_5_10": 12.50,
    "child_under_5": 0,
    "senior_discount_pct": 20,
}

# The same price grid written with the explicit keys: 20% off $20 is a flat $16,
# and 10-17-year-olds paid the adult price. Pricing a booking either way must
# come out to the same cent.
EXPLICIT_EQUIVALENT_CONFIG = {
    "adult": 20.00,
    "senior": 16.00,
    "child_10_17": 20.00,
    "child_5_10": 12.50,
    "child_under_5": 0,
}

# Panama Viejo 507th anniversary: a grid the legacy format cannot express,
# because 10-17-year-olds pay the child rate rather than the adult rate.
PANAMA_VIEJO_507_CONFIG = {
    "adult": 15.00,
    "senior": 10.00,
    "child_10_17": 5.00,
    "child_5_10": 5.00,
    "child_under_5": 0,
}

# Mixes covering every age category alone and in combination.
PARTICIPANT_MIXES = [
    {"adult": 1},
    {"adult": 2, "child_5_10": 1},
    {"senior_female": 1},
    {"senior_male": 2},
    {"senior_female": 1, "senior_male": 1, "adult": 2},
    {"child_10_17": 3},
    {"child_under_5": 2, "adult": 1},
    {"child_under_5": 1},
    {
        "senior_female": 2,
        "senior_male": 1,
        "adult": 3,
        "child_10_17": 2,
        "child_5_10": 2,
        "child_under_5": 1,
    },
]


def _pax(**counts):
    """Build a ParticipantBreakdown from plain counts, e.g. _pax(adult=2)."""
    return ParticipantBreakdown(
        **{key: ParticipantCategory(resident=0, tourist=n) for key, n in counts.items()}
    )


class TestFixedPrice:
    """Tests for calculate_fixed_price (open-group tours)."""

    @pytest.mark.parametrize("mix", PARTICIPANT_MIXES)
    def test_legacy_and_explicit_configs_agree(self, mix):
        """
        The two config formats are two spellings of one price grid.

        This is the guard against the newer `senior` / `child_10_17` keys quietly
        repricing a tour that was configured before they existed: every field of
        the breakdown must match, not just the grand total.
        """
        participants = _pax(**mix)

        legacy = calculate_fixed_price(LEGACY_CONFIG, participants)
        explicit = calculate_fixed_price(EXPLICIT_EQUIVALENT_CONFIG, participants)

        assert legacy.grand_total == explicit.grand_total
        assert legacy.service_total == explicit.service_total
        assert legacy.service_per_pax == explicit.service_per_pax
        assert legacy.senior_discount == explicit.senior_discount
        assert legacy.tickets_total == explicit.tickets_total
        assert legacy.transport_total == explicit.transport_total

    def test_legacy_config_totals_unchanged(self):
        """
        Golden totals for the production siete-iglesias config.

        Hand-computed against the original implementation, so a future edit that
        changes what that tour charges has to fail here first.
        """
        participants = _pax(
            senior_female=2,
            senior_male=1,
            adult=3,
            child_10_17=2,
            child_5_10=2,
            child_under_5=1,
        )

        price = calculate_fixed_price(LEGACY_CONFIG, participants)

        # 3 seniors + 3 adults + 2 teens at $20 = $160, 2 children at $12.50 = $25,
        # 1 infant free. Pre-discount $185, less 20% on the 3 seniors ($12).
        assert price.service_total == Decimal("185.00")
        assert price.senior_discount == Decimal("12.00")
        assert price.grand_total == Decimal("173.00")
        assert price.service_per_pax == Decimal("20.00")

    def test_panama_viejo_507_totals(self):
        """Golden totals for the anniversary grid: $15 / $10 / $5 / free."""
        participants = _pax(adult=2, senior_female=1, child_10_17=1, child_5_10=1, child_under_5=1)

        price = calculate_fixed_price(PANAMA_VIEJO_507_CONFIG, participants)

        # 2 adults $30 + 1 jubilado $10 + 2 menores $10 + 1 infant free = $50.
        assert price.grand_total == Decimal("50.00")
        # The $5 the senior rate saved is reported, not folded silently into the total.
        assert price.senior_discount == Decimal("5.00")
        assert price.service_total == Decimal("55.00")

    def test_teens_pay_the_child_rate_only_when_configured(self):
        """`child_10_17` is opt-in: without it teens keep paying the adult price."""
        participants = _pax(child_10_17=2)

        assert calculate_fixed_price(PANAMA_VIEJO_507_CONFIG, participants).grand_total == Decimal(
            "10.00"
        )
        assert calculate_fixed_price(LEGACY_CONFIG, participants).grand_total == Decimal("40.00")

    def test_flat_senior_price_wins_over_percentage(self):
        """When a config carries both, the flat `senior` price is authoritative."""
        both = {**LEGACY_CONFIG, "senior": 5.00}

        price = calculate_fixed_price(both, _pax(senior_female=1))

        assert price.grand_total == Decimal("5.00")
        assert price.senior_discount == Decimal("15.00")

    def test_infants_only_is_free(self):
        """
        A party of only under-5s costs nothing and reports no per-pax rate.

        (A wholly empty breakdown cannot reach pricing — ParticipantBreakdown
        rejects it — so this is the real zero-payer case.)
        """
        price = calculate_fixed_price(PANAMA_VIEJO_507_CONFIG, _pax(child_under_5=2))

        assert price.grand_total == Decimal("0")
        assert price.service_per_pax == Decimal("0")


class TestRegularTourPricingUnaffected:
    """
    Regular (non-fixed-price) tours must be untouched by the fixed-price changes.

    Regular tours price age through attraction *tickets* and a per-position guide
    ladder, and never reach calculate_fixed_price at all — this pins that with a
    group spanning every age category.
    """

    def test_mixed_age_group_full_price(self, test_db, pricing_config, tour):
        engine = PricingEngine(test_db)
        participants = _pax(
            senior_female=1, adult=1, child_10_17=1, child_5_10=1, child_under_5=1
        )

        price = engine.calculate_full_price(tour, participants)

        # 4 billable (the under-5 is not charged and takes no ladder position):
        # base $10 + 3h * ($10 + $6 + $6 + $5) = $91, per-pax $22.75.
        assert price.service_total == Decimal("91.00")
        assert price.service_per_pax == Decimal("22.75")
        # Senior discount is a percentage of the per-pax rate, not a flat price.
        assert price.senior_discount == Decimal("4.55")
        # Tickets: adult $20 + senior $18 + teen $10 + child $10 + infant free.
        assert price.tickets_total == Decimal("58.00")
        assert price.grand_total == Decimal("144.45")
