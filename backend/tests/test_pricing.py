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
from app.services.pricing import PricingEngine


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