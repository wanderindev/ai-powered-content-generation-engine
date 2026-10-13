"""Seed initial tour booking data.

Revision ID: 20260131_seed_data
Revises: 20260131_tour_booking
Create Date: 2026-01-31
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "20260131_seed_data"
down_revision = "20260131_tour_booking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Seed initial data for tours, zones, pricing config, and attractions."""

    # 1. Pricing configuration
    pricing_config = sa.table(
        "pricing_config",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(
        pricing_config,
        [
            {"key": "base_fee", "value": "10.00", "description": "Base fee per tour"},
            {
                "key": "hourly_rate_pax_1",
                "value": "10.00",
                "description": "Hourly rate for 1st person",
            },
            {
                "key": "hourly_rate_pax_2_3",
                "value": "6.00",
                "description": "Hourly rate for 2nd-3rd person",
            },
            {
                "key": "hourly_rate_pax_4_5",
                "value": "4.00",
                "description": "Hourly rate for 4th-5th person",
            },
            {
                "key": "hourly_rate_pax_6_7",
                "value": "2.00",
                "description": "Hourly rate for 6th-7th person",
            },
            {
                "key": "senior_discount_percent",
                "value": "20",
                "description": "Senior citizen discount percentage",
            },
            {
                "key": "payment_link_validity_hours",
                "value": "24",
                "description": "Hours before payment link expires",
            },
            {
                "key": "reservation_validity_days",
                "value": "7",
                "description": "Days to hold unpaid reservation",
            },
            {
                "key": "followup_1_hours",
                "value": "12",
                "description": "Hours after link expiry for 1st followup",
            },
            {
                "key": "followup_2_hours",
                "value": "48",
                "description": "Hours after link expiry for 2nd followup",
            },
            {
                "key": "followup_3_hours",
                "value": "120",
                "description": "Hours after link expiry for 3rd followup",
            },
        ],
    )

    # 2. Zones (6 zones)
    zones = sa.table(
        "zones",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("name_es", sa.String),
        sa.column("description", sa.String),
        sa.column("description_es", sa.String),
        sa.column("display_order", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        zones,
        [
            {
                "id": 1,
                "name": "Casco Antiguo / San Felipe",
                "name_es": "Casco Antiguo / San Felipe",
                "description": "Historic district and surrounding areas",
                "description_es": "Distrito histórico y áreas circundantes",
                "display_order": 1,
                "is_active": True,
            },
            {
                "id": 2,
                "name": "Calidonia / Bella Vista / Marbella",
                "name_es": "Calidonia / Bella Vista / Marbella",
                "description": "Central business and hotel district",
                "description_es": "Distrito central de negocios y hoteles",
                "display_order": 2,
                "is_active": True,
            },
            {
                "id": 3,
                "name": "Obarrio / El Cangrejo / Condado del Rey",
                "name_es": "Obarrio / El Cangrejo / Condado del Rey",
                "description": "Residential and commercial areas",
                "description_es": "Áreas residenciales y comerciales",
                "display_order": 3,
                "is_active": True,
            },
            {
                "id": 4,
                "name": "Costa del Este / Panama Pacifico",
                "name_es": "Costa del Este / Panamá Pacífico",
                "description": "Modern developments and beach areas",
                "description_es": "Desarrollos modernos y áreas de playa",
                "display_order": 4,
                "is_active": True,
            },
            {
                "id": 5,
                "name": "Tocumen / Airport Area",
                "name_es": "Tocumen / Área del Aeropuerto",
                "description": "Airport and eastern areas",
                "description_es": "Aeropuerto y áreas del este",
                "display_order": 5,
                "is_active": True,
            },
            {
                "id": 6,
                "name": "Clayton / Albrook / Amador",
                "name_es": "Clayton / Albrook / Amador",
                "description": "Former Canal Zone areas",
                "description_es": "Antiguas áreas de la Zona del Canal",
                "display_order": 6,
                "is_active": True,
            },
        ],
    )

    # 3. Attractions with ticket prices
    attractions = sa.table(
        "attractions",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("name_es", sa.String),
        sa.column("ticket_adult_tourist", sa.Numeric),
        sa.column("ticket_senior_tourist", sa.Numeric),
        sa.column("ticket_child_tourist", sa.Numeric),
        sa.column("ticket_infant_tourist", sa.Numeric),
        sa.column("ticket_adult_resident", sa.Numeric),
        sa.column("ticket_senior_resident", sa.Numeric),
        sa.column("ticket_child_resident", sa.Numeric),
        sa.column("ticket_infant_resident", sa.Numeric),
        sa.column("notes", sa.Text),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        attractions,
        [
            {
                "id": 1,
                "name": "Miraflores Visitors Center",
                "name_es": "Centro de Visitantes de Miraflores",
                "ticket_adult_tourist": Decimal("20.00"),
                "ticket_senior_tourist": Decimal("18.00"),
                "ticket_child_tourist": Decimal("15.00"),
                "ticket_infant_tourist": Decimal("0.00"),
                "ticket_adult_resident": Decimal("5.00"),
                "ticket_senior_resident": Decimal("4.00"),
                "ticket_child_resident": Decimal("3.00"),
                "ticket_infant_resident": Decimal("0.00"),
                "notes": "Includes observation deck and museum",
                "is_active": True,
            },
            {
                "id": 2,
                "name": "Panama Viejo",
                "name_es": "Panamá Viejo",
                "ticket_adult_tourist": Decimal("15.00"),
                "ticket_senior_tourist": Decimal("12.00"),
                "ticket_child_tourist": Decimal("10.00"),
                "ticket_infant_tourist": Decimal("0.00"),
                "ticket_adult_resident": Decimal("5.00"),
                "ticket_senior_resident": Decimal("4.00"),
                "ticket_child_resident": Decimal("3.00"),
                "ticket_infant_resident": Decimal("0.00"),
                "notes": "Museum and ruins",
                "is_active": True,
            },
            {
                "id": 3,
                "name": "Biomuseo",
                "name_es": "Biomuseo",
                "ticket_adult_tourist": Decimal("22.00"),
                "ticket_senior_tourist": Decimal("20.00"),
                "ticket_child_tourist": Decimal("11.00"),
                "ticket_infant_tourist": Decimal("0.00"),
                "ticket_adult_resident": Decimal("15.00"),
                "ticket_senior_resident": Decimal("12.00"),
                "ticket_child_resident": Decimal("8.00"),
                "ticket_infant_resident": Decimal("0.00"),
                "notes": "Designed by Frank Gehry",
                "is_active": True,
            },
            {
                "id": 4,
                "name": "Mi Pueblito",
                "name_es": "Mi Pueblito",
                "ticket_adult_tourist": Decimal("3.00"),
                "ticket_senior_tourist": Decimal("2.00"),
                "ticket_child_tourist": Decimal("1.00"),
                "ticket_infant_tourist": Decimal("0.00"),
                "ticket_adult_resident": Decimal("1.00"),
                "ticket_senior_resident": Decimal("1.00"),
                "ticket_child_resident": Decimal("0.50"),
                "ticket_infant_resident": Decimal("0.00"),
                "notes": "Traditional village replica",
                "is_active": True,
            },
            {
                "id": 5,
                "name": "Canal Museum (Casco Antiguo)",
                "name_es": "Museo del Canal (Casco Antiguo)",
                "ticket_adult_tourist": Decimal("10.00"),
                "ticket_senior_tourist": Decimal("8.00"),
                "ticket_child_tourist": Decimal("5.00"),
                "ticket_infant_tourist": Decimal("0.00"),
                "ticket_adult_resident": Decimal("5.00"),
                "ticket_senior_resident": Decimal("4.00"),
                "ticket_child_resident": Decimal("3.00"),
                "ticket_infant_resident": Decimal("0.00"),
                "notes": "History of the Panama Canal",
                "is_active": True,
            },
            {
                "id": 6,
                "name": "Interoceanic Canal Museum",
                "name_es": "Museo del Canal Interoceánico",
                "ticket_adult_tourist": Decimal("8.00"),
                "ticket_senior_tourist": Decimal("6.00"),
                "ticket_child_tourist": Decimal("4.00"),
                "ticket_infant_tourist": Decimal("0.00"),
                "ticket_adult_resident": Decimal("4.00"),
                "ticket_senior_resident": Decimal("3.00"),
                "ticket_child_resident": Decimal("2.00"),
                "ticket_infant_resident": Decimal("0.00"),
                "notes": None,
                "is_active": True,
            },
        ],
    )

    # 4. Tours (6 tours)
    tours = sa.table(
        "tours",
        sa.column("id", sa.Integer),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("name_es", sa.String),
        sa.column("description", sa.Text),
        sa.column("description_es", sa.Text),
        sa.column("duration_hours", sa.Numeric),
        sa.column("max_group_size", sa.Integer),
        sa.column("zone_id", sa.Integer),
        sa.column("is_active", sa.Boolean),
        sa.column("display_order", sa.Integer),
    )
    op.bulk_insert(
        tours,
        [
            {
                "id": 1,
                "slug": "canal-de-panama",
                "name": "Panama Canal Tour",
                "name_es": "Tour del Canal de Panamá",
                "description": "Visit the Miraflores Visitors Center and learn about the history and engineering of one of the world's greatest engineering achievements.",
                "description_es": "Visita el Centro de Visitantes de Miraflores y conoce la historia e ingeniería de una de las mayores obras de ingeniería del mundo.",
                "duration_hours": Decimal("3.0"),
                "max_group_size": 7,
                "zone_id": 6,
                "is_active": True,
                "display_order": 1,
            },
            {
                "id": 2,
                "slug": "casco-antiguo-plazas",
                "name": "Casco Antiguo Plazas Tour",
                "name_es": "Tour de las Plazas del Casco Antiguo",
                "description": "Explore the historic plazas of Panama's colonial district, a UNESCO World Heritage Site.",
                "description_es": "Explora las plazas históricas del distrito colonial de Panamá, Patrimonio de la Humanidad de la UNESCO.",
                "duration_hours": Decimal("2.5"),
                "max_group_size": 7,
                "zone_id": 1,
                "is_active": True,
                "display_order": 2,
            },
            {
                "id": 3,
                "slug": "casco-antiguo-museos",
                "name": "Casco Antiguo Museums Tour",
                "name_es": "Tour de los Museos del Casco Antiguo",
                "description": "Visit the museums of Casco Antiguo including the Canal Museum.",
                "description_es": "Visita los museos del Casco Antiguo incluyendo el Museo del Canal.",
                "duration_hours": Decimal("3.0"),
                "max_group_size": 7,
                "zone_id": 1,
                "is_active": True,
                "display_order": 3,
            },
            {
                "id": 4,
                "slug": "panama-viejo",
                "name": "Panama Viejo Tour",
                "name_es": "Tour de Panamá Viejo",
                "description": "Explore the ruins of the original Panama City, founded in 1519.",
                "description_es": "Explora las ruinas de la ciudad original de Panamá, fundada en 1519.",
                "duration_hours": Decimal("2.0"),
                "max_group_size": 7,
                "zone_id": 4,
                "is_active": True,
                "display_order": 4,
            },
            {
                "id": 5,
                "slug": "mi-pueblito",
                "name": "Mi Pueblito Tour",
                "name_es": "Tour de Mi Pueblito",
                "description": "Experience traditional Panamanian village life at this cultural complex.",
                "description_es": "Experimenta la vida tradicional de un pueblo panameño en este complejo cultural.",
                "duration_hours": Decimal("2.0"),
                "max_group_size": 7,
                "zone_id": 6,
                "is_active": True,
                "display_order": 5,
            },
            {
                "id": 6,
                "slug": "biomuseo",
                "name": "Biomuseo Tour",
                "name_es": "Tour del Biomuseo",
                "description": "Discover Panama's biodiversity at the Biomuseo, designed by Frank Gehry.",
                "description_es": "Descubre la biodiversidad de Panamá en el Biomuseo, diseñado por Frank Gehry.",
                "duration_hours": Decimal("2.5"),
                "max_group_size": 7,
                "zone_id": 6,
                "is_active": True,
                "display_order": 6,
            },
        ],
    )

    # 5. Tour-Attraction relationships
    tour_attractions = sa.table(
        "tour_attractions",
        sa.column("tour_id", sa.Integer),
        sa.column("attraction_id", sa.Integer),
        sa.column("is_optional", sa.Boolean),
        sa.column("visit_order", sa.Integer),
    )
    op.bulk_insert(
        tour_attractions,
        [
            # Panama Canal Tour -> Miraflores
            {"tour_id": 1, "attraction_id": 1, "is_optional": False, "visit_order": 1},
            # Casco Antiguo Museums Tour -> Canal Museum
            {"tour_id": 3, "attraction_id": 5, "is_optional": False, "visit_order": 1},
            # Casco Antiguo Museums Tour -> Interoceanic Museum (optional)
            {"tour_id": 3, "attraction_id": 6, "is_optional": True, "visit_order": 2},
            # Panama Viejo Tour -> Panama Viejo
            {"tour_id": 4, "attraction_id": 2, "is_optional": False, "visit_order": 1},
            # Mi Pueblito Tour -> Mi Pueblito
            {"tour_id": 5, "attraction_id": 4, "is_optional": False, "visit_order": 1},
            # Biomuseo Tour -> Biomuseo
            {"tour_id": 6, "attraction_id": 3, "is_optional": False, "visit_order": 1},
        ],
    )

    # 6. Zone transport rates (sample rates)
    zone_transport_rates = sa.table(
        "zone_transport_rates",
        sa.column("from_zone_id", sa.Integer),
        sa.column("to_zone_id", sa.Integer),
        sa.column("rate_small_group", sa.Numeric),
        sa.column("rate_large_group", sa.Numeric),
    )

    # Generate rates for all zone combinations
    rates_data = []
    # Rates matrix: from zone -> to zone
    # Prices vary by distance
    rate_matrix = {
        # From Zone 1 (Casco Antiguo)
        (1, 1): (0, 0),
        (1, 2): (15, 25),
        (1, 3): (20, 35),
        (1, 4): (30, 45),
        (1, 5): (40, 55),
        (1, 6): (15, 25),
        # From Zone 2 (Bella Vista)
        (2, 1): (15, 25),
        (2, 2): (0, 0),
        (2, 3): (15, 25),
        (2, 4): (20, 30),
        (2, 5): (35, 50),
        (2, 6): (20, 30),
        # From Zone 3 (El Cangrejo)
        (3, 1): (20, 35),
        (3, 2): (15, 25),
        (3, 3): (0, 0),
        (3, 4): (20, 30),
        (3, 5): (30, 45),
        (3, 6): (20, 30),
        # From Zone 4 (Costa del Este)
        (4, 1): (30, 45),
        (4, 2): (20, 30),
        (4, 3): (20, 30),
        (4, 4): (0, 0),
        (4, 5): (25, 40),
        (4, 6): (25, 40),
        # From Zone 5 (Tocumen)
        (5, 1): (40, 55),
        (5, 2): (35, 50),
        (5, 3): (30, 45),
        (5, 4): (25, 40),
        (5, 5): (0, 0),
        (5, 6): (40, 55),
        # From Zone 6 (Clayton/Albrook)
        (6, 1): (15, 25),
        (6, 2): (20, 30),
        (6, 3): (20, 30),
        (6, 4): (25, 40),
        (6, 5): (40, 55),
        (6, 6): (0, 0),
    }

    for (from_zone, to_zone), (small_rate, large_rate) in rate_matrix.items():
        rates_data.append(
            {
                "from_zone_id": from_zone,
                "to_zone_id": to_zone,
                "rate_small_group": Decimal(str(small_rate)),
                "rate_large_group": Decimal(str(large_rate)),
            }
        )

    op.bulk_insert(zone_transport_rates, rates_data)

    # 7. Sample hotels (starter set for development)
    hotels = sa.table(
        "hotels",
        sa.column("name", sa.String),
        sa.column("name_es", sa.String),
        sa.column("address", sa.String),
        sa.column("zone_id", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        hotels,
        [
            # Zone 1: Casco Antiguo
            {
                "name": "American Trade Hotel",
                "name_es": None,
                "address": "Plaza Herrera, Casco Antiguo",
                "zone_id": 1,
                "is_active": True,
            },
            {
                "name": "Las Clementinas",
                "name_es": None,
                "address": "Calle 11 y Avenida B, Casco Viejo",
                "zone_id": 1,
                "is_active": True,
            },
            {
                "name": "La Concordia Hotel",
                "name_es": None,
                "address": "Calle 12, Casco Antiguo",
                "zone_id": 1,
                "is_active": True,
            },
            {
                "name": "Magnolia Inn",
                "name_es": None,
                "address": "Calle 8a Este, Casco Viejo",
                "zone_id": 1,
                "is_active": True,
            },
            # Zone 2: Bella Vista/Marbella
            {
                "name": "Marriott Panama Hotel",
                "name_es": None,
                "address": "Calle 52 y Ricardo Arias",
                "zone_id": 2,
                "is_active": True,
            },
            {
                "name": "JW Marriott Panama",
                "name_es": None,
                "address": "Calle 47 y Aquilino de la Guardia",
                "zone_id": 2,
                "is_active": True,
            },
            {
                "name": "Hilton Panama",
                "name_es": None,
                "address": "Avenida Balboa y Aquilino de la Guardia",
                "zone_id": 2,
                "is_active": True,
            },
            {
                "name": "Hard Rock Hotel Panama Megapolis",
                "name_es": None,
                "address": "Via Israel, San Francisco",
                "zone_id": 2,
                "is_active": True,
            },
            {
                "name": "InterContinental Miramar Panama",
                "name_es": None,
                "address": "Avenida Balboa",
                "zone_id": 2,
                "is_active": True,
            },
            {
                "name": "Le Meridien Panama",
                "name_es": None,
                "address": "Calle Uruguay y Calle 48",
                "zone_id": 2,
                "is_active": True,
            },
            {
                "name": "Sortis Hotel",
                "name_es": None,
                "address": "Calle 56 y 57, Obarrio",
                "zone_id": 2,
                "is_active": True,
            },
            # Zone 3: El Cangrejo/Obarrio
            {
                "name": "Bristol Panama",
                "name_es": None,
                "address": "Calle Aquilino de la Guardia",
                "zone_id": 3,
                "is_active": True,
            },
            {
                "name": "Best Western Plus Panama Zen",
                "name_es": None,
                "address": "Via España, El Cangrejo",
                "zone_id": 3,
                "is_active": True,
            },
            {
                "name": "Crowne Plaza Panama",
                "name_es": None,
                "address": "Via España y Calle 49",
                "zone_id": 3,
                "is_active": True,
            },
            {
                "name": "El Panama Hotel",
                "name_es": None,
                "address": "Via España 111",
                "zone_id": 3,
                "is_active": True,
            },
            {
                "name": "Courtyard by Marriott Panama",
                "name_es": None,
                "address": "Via España, El Cangrejo",
                "zone_id": 3,
                "is_active": True,
            },
            # Zone 4: Costa del Este
            {
                "name": "Santa Maria Hotel & Golf Resort",
                "name_es": None,
                "address": "Costa del Este",
                "zone_id": 4,
                "is_active": True,
            },
            {
                "name": "Hotel & Suites Las Olas",
                "name_es": None,
                "address": "Costa del Este",
                "zone_id": 4,
                "is_active": True,
            },
            # Zone 5: Tocumen/Airport
            {
                "name": "Riande Aeropuerto Hotel",
                "name_es": None,
                "address": "Aeropuerto Internacional de Tocumen",
                "zone_id": 5,
                "is_active": True,
            },
            {
                "name": "Holiday Inn Express Panama",
                "name_es": None,
                "address": "Via Tocumen",
                "zone_id": 5,
                "is_active": True,
            },
            # Zone 6: Clayton/Albrook
            {
                "name": "Wyndham Panama Albrook Mall",
                "name_es": None,
                "address": "Albrook Mall",
                "zone_id": 6,
                "is_active": True,
            },
            {
                "name": "Country Inn & Suites Albrook",
                "name_es": None,
                "address": "Via Albrook",
                "zone_id": 6,
                "is_active": True,
            },
            {
                "name": "Waldorf Astoria Panama",
                "name_es": None,
                "address": "Calzada de Amador",
                "zone_id": 6,
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    """Remove all seeded data."""
    # Delete in reverse order of dependencies
    op.execute("DELETE FROM tour_attractions")
    op.execute("DELETE FROM hotels")
    op.execute("DELETE FROM zone_transport_rates")
    op.execute("DELETE FROM tours")
    op.execute("DELETE FROM attractions")
    op.execute("DELETE FROM zones")
    op.execute("DELETE FROM pricing_config")