"""Tests for tour API endpoints."""

from decimal import Decimal

import pytest

from app.models import Attraction, Tour, TourAttraction, Zone


@pytest.fixture
def zone(test_db):
    """Create a test zone."""
    zone = Zone(
        id=1,
        name="Test Zone EN",
        name_es="Zona de Prueba ES",
        description="Test zone description",
        description_es="Descripción de zona de prueba",
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
        name="Test Attraction EN",
        name_es="Atracción de Prueba ES",
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
        name="Test Tour EN",
        name_es="Tour de Prueba ES",
        description="Test tour description",
        description_es="Descripción del tour de prueba",
        duration_hours=Decimal("3.0"),
        max_group_size=7,
        zone_id=zone.id,
        is_active=True,
        display_order=1,
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


class TestListTours:
    """Tests for GET /api/v1/tours endpoint."""

    def test_list_tours_empty(self, test_client):
        """Test listing tours when none exist."""
        response = test_client.get("/api/v1/tours")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_tours(self, test_client, tour):
        """Test listing tours."""
        response = test_client.get("/api/v1/tours")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "test-tour"
        assert data[0]["name"] == "Test Tour EN"
        assert data[0]["duration_hours"] == "3.00"

    def test_list_tours_spanish(self, test_client, tour):
        """Test listing tours in Spanish."""
        response = test_client.get("/api/v1/tours?lang=es")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["name"] == "Tour de Prueba ES"


class TestGetTour:
    """Tests for GET /api/v1/tours/{slug} endpoint."""

    def test_get_tour_not_found(self, test_client):
        """Test getting non-existent tour."""
        response = test_client.get("/api/v1/tours/non-existent")
        assert response.status_code == 404

    def test_get_tour(self, test_client, tour):
        """Test getting tour details."""
        response = test_client.get("/api/v1/tours/test-tour")
        assert response.status_code == 200
        data = response.json()

        assert data["slug"] == "test-tour"
        assert data["name"] == "Test Tour EN"
        assert data["description"] == "Test tour description"
        assert data["duration_hours"] == "3.00"
        assert data["max_group_size"] == 7

        # Check zone
        assert data["zone"]["name"] == "Test Zone EN"

        # Check attractions
        assert len(data["attractions"]) == 1
        assert data["attractions"][0]["name"] == "Test Attraction EN"
        assert data["attractions"][0]["ticket_adult_tourist"] == "20.00"
        # #45: infant prices are charged by the pricing engine, so they must be
        # present in the public schema (here zero) for an honest breakdown.
        assert data["attractions"][0]["ticket_infant_tourist"] == "0.00"
        assert data["attractions"][0]["ticket_infant_resident"] == "0.00"

    def test_get_tour_exposes_nonzero_infant_pricing(self, test_client, test_db, tour):
        """#45: a non-zero infant price (as on prod's Panama Viejo) must reach the client."""
        attraction = test_db.query(Attraction).filter_by(name="Test Attraction EN").one()
        attraction.ticket_infant_resident = Decimal("1.00")
        test_db.commit()

        response = test_client.get("/api/v1/tours/test-tour")
        assert response.status_code == 200
        assert response.json()["attractions"][0]["ticket_infant_resident"] == "1.00"

    def test_get_tour_spanish(self, test_client, tour):
        """Test getting tour details in Spanish."""
        response = test_client.get("/api/v1/tours/test-tour?lang=es")
        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "Tour de Prueba ES"
        assert data["description"] == "Descripción del tour de prueba"
        assert data["zone"]["name"] == "Zona de Prueba ES"
        assert data["attractions"][0]["name"] == "Atracción de Prueba ES"


class TestTourAvailability:
    """Tests for GET /api/v1/tours/{slug}/availability endpoint."""

    def test_get_availability_not_found(self, test_client):
        """Test availability for non-existent tour."""
        response = test_client.get("/api/v1/tours/non-existent/availability")
        assert response.status_code == 404

    def test_get_availability(self, test_client, tour):
        """Test getting availability."""
        response = test_client.get("/api/v1/tours/test-tour/availability")
        assert response.status_code == 200
        data = response.json()

        # Should return 90 days of availability by default
        assert len(data) <= 91

        # Each slot should have date, am_available, pm_available
        assert "date" in data[0]
        assert "am_available" in data[0]
        assert "pm_available" in data[0]

    def test_get_availability_custom_range(self, test_client, tour):
        """Test availability with custom date range."""
        response = test_client.get(
            "/api/v1/tours/test-tour/availability"
            "?start_date=2026-03-01&end_date=2026-03-07"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 7
