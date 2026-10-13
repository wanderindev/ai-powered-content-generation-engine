"""Tests for the zones and hotels API endpoints.

GET /api/v1/zones
GET /api/v1/zones/hotels/search
GET /api/v1/zones/hotels/{hotel_id}
"""

from app.models import Hotel, Zone


def _make_zone(test_db, **overrides):
    """Insert a Zone row with sensible defaults."""
    defaults = {
        "name": "Test Zone EN",
        "name_es": "Zona de Prueba ES",
        "description": "Test zone description",
        "description_es": "Descripción de zona de prueba",
        "display_order": 1,
        "is_active": True,
    }
    defaults.update(overrides)
    zone = Zone(**defaults)
    test_db.add(zone)
    test_db.commit()
    test_db.refresh(zone)
    return zone


def _make_hotel(test_db, zone, **overrides):
    """Insert a Hotel row tied to the given zone."""
    defaults = {
        "name": "Test Hotel",
        "name_es": None,
        "address": "123 Main St",
        "address_es": None,
        "zone_id": zone.id,
        "is_active": True,
    }
    defaults.update(overrides)
    hotel = Hotel(**defaults)
    test_db.add(hotel)
    test_db.commit()
    test_db.refresh(hotel)
    return hotel


class TestListZones:
    """Tests for GET /api/v1/zones."""

    def test_list_empty(self, test_client):
        """No zones returns []."""
        response = test_client.get("/api/v1/zones")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_active_only(self, test_client, test_db):
        """is_active=False zones must be excluded."""
        _make_zone(test_db, name="Active", display_order=1, is_active=True)
        _make_zone(test_db, name="Inactive", display_order=2, is_active=False)

        response = test_client.get("/api/v1/zones")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Active"

    def test_list_ordered_by_display_order(self, test_client, test_db):
        """Results ordered by display_order ascending."""
        _make_zone(test_db, name="Second", display_order=2)
        _make_zone(test_db, name="First", display_order=1)

        response = test_client.get("/api/v1/zones")
        assert response.status_code == 200
        data = response.json()
        assert [z["name"] for z in data] == ["First", "Second"]

    def test_list_default_lang_english(self, test_client, test_db):
        """Default lang returns English name/description fields."""
        _make_zone(
            test_db,
            name="Casco",
            name_es="Casco ES",
            description="EN desc",
            description_es="ES desc",
        )

        response = test_client.get("/api/v1/zones")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["name"] == "Casco"
        assert data[0]["description"] == "EN desc"

    def test_list_spanish(self, test_client, test_db):
        """?lang=es returns Spanish name_es/description_es fields."""
        _make_zone(
            test_db,
            name="Casco",
            name_es="Casco ES",
            description="EN desc",
            description_es="ES desc",
        )

        response = test_client.get("/api/v1/zones?lang=es")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["name"] == "Casco ES"
        assert data[0]["description"] == "ES desc"


class TestSearchHotels:
    """Tests for GET /api/v1/zones/hotels/search."""

    def test_search_short_q_returns_422(self, test_client):
        """q with fewer than 2 chars fails validation."""
        response = test_client.get("/api/v1/zones/hotels/search?q=a")
        assert response.status_code == 422

    def test_search_missing_q_returns_422(self, test_client):
        """Missing q parameter fails validation."""
        response = test_client.get("/api/v1/zones/hotels/search")
        assert response.status_code == 422

    def test_search_matches_english_name(self, test_client, test_db):
        """ILIKE match on Hotel.name returns the hotel."""
        zone = _make_zone(test_db)
        _make_hotel(test_db, zone, name="Hilton Panama")

        response = test_client.get("/api/v1/zones/hotels/search?q=Hilton")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Hilton Panama"

    def test_search_matches_spanish_name(self, test_client, test_db):
        """ILIKE match on Hotel.name_es also returns the hotel (lang-agnostic search)."""
        zone = _make_zone(test_db)
        _make_hotel(test_db, zone, name="Generic Name", name_es="Hotel Panamá")

        response = test_client.get("/api/v1/zones/hotels/search?q=Panamá")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        # Default lang=en; name field returned is hotel.name
        assert data[0]["name"] == "Generic Name"

    def test_search_excludes_inactive(self, test_client, test_db):
        """is_active=False hotels excluded even if they match."""
        zone = _make_zone(test_db)
        _make_hotel(test_db, zone, name="Inactive Hilton", is_active=False)

        response = test_client.get("/api/v1/zones/hotels/search?q=Hilton")
        assert response.status_code == 200
        assert response.json() == []

    def test_search_returns_zone_info(self, test_client, test_db):
        """Response payload includes zone_id and zone_name."""
        zone = _make_zone(test_db, name="Casco Antiguo")
        _make_hotel(test_db, zone, name="Hilton Panama")

        response = test_client.get("/api/v1/zones/hotels/search?q=Hilton")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["zone_id"] == zone.id
        assert data[0]["zone_name"] == "Casco Antiguo"

    def test_search_respects_limit(self, test_client, test_db):
        """?limit=N caps result count."""
        zone = _make_zone(test_db)
        for i in range(3):
            _make_hotel(test_db, zone, name=f"Hilton {i}")

        response = test_client.get("/api/v1/zones/hotels/search?q=Hilton&limit=1")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_search_limit_out_of_range_returns_422(self, test_client):
        """limit > 50 fails validation."""
        response = test_client.get("/api/v1/zones/hotels/search?q=hilton&limit=100")
        assert response.status_code == 422

    def test_search_spanish_lang_swaps_fields(self, test_client, test_db):
        """?lang=es returns name_es / address_es / zone.name_es when present."""
        zone = _make_zone(test_db, name="Casco EN", name_es="Casco ES")
        _make_hotel(
            test_db,
            zone,
            name="Hilton EN",
            name_es="Hilton ES",
            address="EN address",
            address_es="ES address",
        )

        response = test_client.get("/api/v1/zones/hotels/search?q=Hilton&lang=es")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["name"] == "Hilton ES"
        assert data[0]["address"] == "ES address"
        assert data[0]["zone_name"] == "Casco ES"


class TestGetHotel:
    """Tests for GET /api/v1/zones/hotels/{hotel_id}."""

    def test_get_hotel_found(self, test_client, test_db):
        """Returns full HotelSearchResult payload."""
        zone = _make_zone(test_db, name="Bella Vista")
        hotel = _make_hotel(test_db, zone, name="Marriott", address="Calle 50")

        response = test_client.get(f"/api/v1/zones/hotels/{hotel.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == hotel.id
        assert data["name"] == "Marriott"
        assert data["address"] == "Calle 50"
        assert data["zone_id"] == zone.id
        assert data["zone_name"] == "Bella Vista"

    def test_get_hotel_not_found(self, test_client):
        """Unknown id returns 404."""
        response = test_client.get("/api/v1/zones/hotels/99999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_hotel_inactive_returns_404(self, test_client, test_db):
        """Inactive hotels are not retrievable."""
        zone = _make_zone(test_db)
        hotel = _make_hotel(test_db, zone, name="Closed", is_active=False)

        response = test_client.get(f"/api/v1/zones/hotels/{hotel.id}")
        assert response.status_code == 404

    def test_get_hotel_spanish(self, test_client, test_db):
        """?lang=es swaps to name_es / address_es / zone.name_es."""
        zone = _make_zone(test_db, name="Casco EN", name_es="Casco ES")
        hotel = _make_hotel(
            test_db,
            zone,
            name="Hilton EN",
            name_es="Hilton ES",
            address="EN address",
            address_es="ES address",
        )

        response = test_client.get(f"/api/v1/zones/hotels/{hotel.id}?lang=es")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Hilton ES"
        assert data["address"] == "ES address"
        assert data["zone_name"] == "Casco ES"
