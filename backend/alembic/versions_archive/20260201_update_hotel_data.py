"""Update hotel data from research.

Revision ID: 20260201_hotel_data
Revises: 20260131_seed_data
Create Date: 2026-02-01
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "20260201_hotel_data"
down_revision = "20260131_seed_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Update zones and hotels with comprehensive research data."""

    # 1. Add Zone 7 (Veracruz)
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
                "id": 7,
                "name": "Veracruz / Panama Pacifico",
                "name_es": "Veracruz / Panamá Pacífico",
                "description": "Beach resorts and master-planned community west of the Canal",
                "description_es": "Resorts de playa y comunidad planificada al oeste del Canal",
                "display_order": 7,
                "is_active": True,
            },
        ],
    )

    # 2. Add transport rates for Zone 7
    zone_transport_rates = sa.table(
        "zone_transport_rates",
        sa.column("from_zone_id", sa.Integer),
        sa.column("to_zone_id", sa.Integer),
        sa.column("rate_small_group", sa.Numeric),
        sa.column("rate_large_group", sa.Numeric),
    )

    # Zone 7 rates (furthest from most attractions, requires bridge crossing)
    zone_7_rates = [
        # From Zone 7 to all zones
        (7, 1, 45, 60),  # To Casco Antiguo
        (7, 2, 40, 55),  # To Bella Vista
        (7, 3, 40, 55),  # To El Cangrejo
        (7, 4, 45, 60),  # To Costa del Este
        (7, 5, 55, 70),  # To Tocumen
        (7, 6, 25, 40),  # To Clayton (closest, same side of canal)
        (7, 7, 0, 0),    # Same zone
        # From all zones to Zone 7
        (1, 7, 45, 60),
        (2, 7, 40, 55),
        (3, 7, 40, 55),
        (4, 7, 45, 60),
        (5, 7, 55, 70),
        (6, 7, 25, 40),
    ]

    rates_data = [
        {
            "from_zone_id": from_z,
            "to_zone_id": to_z,
            "rate_small_group": Decimal(str(small)),
            "rate_large_group": Decimal(str(large)),
        }
        for from_z, to_z, small, large in zone_7_rates
    ]
    op.bulk_insert(zone_transport_rates, rates_data)

    # 3. Delete existing hotels and insert new comprehensive list
    op.execute("DELETE FROM hotels")

    hotels = sa.table(
        "hotels",
        sa.column("name", sa.String),
        sa.column("name_es", sa.String),
        sa.column("address", sa.String),
        sa.column("zone_id", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )

    # All hotels from research CSV
    hotel_data = [
        # Zone 1: Casco Antiguo / San Felipe (47 hotels)
        ("American Trade Hotel", None, "Plaza Herrera, Casco Antiguo", 1),
        ("Hotel La Compañía", None, "Calle 8va y Avenida Central", 1),
        ("Sofitel Legend Casco Viejo", None, "Calle 1era Oeste", 1),
        ("Central Hotel Panama", None, "Plaza de la Independencia", 1),
        ("La Concordia Boutique Hotel", None, "Calle A y Avenida Central", 1),
        ("Hotel Plaza Herrera", None, "Calle 9na y Ave. A", 1),
        ("Las Clementinas", None, "Calle 11 y Avenida B, Casco Viejo", 1),
        ("Magnolia Inn", None, "Calle 8va y Avenida B", 1),
        ("Tantalo Hotel", None, "Calle 8va y Avenida B", 1),
        ("Villa Palma Boutique Hotel", None, "Calle 1era", 1),
        ("Amarla Boutique Hotel", None, "Calle Este y Ave. B", 1),
        ("Gatto Blanco Party Hotel", None, "Avenida B y Calle 8va", 1),
        ("Casa Sucre Boutique Hotel", None, "Calle 8va y Avenida B", 1),
        ("Hostal La Cigarra", None, "Avenida Central, La Peatonal, Edificio 1398", 1),
        ("Hotel República", None, "Plaza 5 de Mayo Final Avenida", 1),
        ("Hotel Doral", None, "Calle Monteserin", 1),
        ("Hotel Riazor", None, "Calle 16 Oeste", 1),
        ("La Manzana by Wynwood House", None, "Calle 15 Oeste", 1),
        ("Casco View Apartments", None, "Calle 16 Oeste", 1),
        ("Viajero Casco Viejo Hostel", None, "Calle 6ta y Avenida A", 1),
        ("Selina Casco Viejo", None, "Calle B, Casco Antiguo", 1),
        ("Hotel San Felipe", None, "Calle 5ta Este, Casco Antiguo", 1),
        ("Los Diamantes Aparthotel", None, "Calle 9na Plaza Herrera", 1),
        ("Hotel Casa Panamá", None, "Calle 11 Este, Casco Viejo", 1),
        ("Oasis Hotel", None, "Calle 9na Este", 1),
        ("AmazINN Casa Marroqui", None, "Calle 6a Oeste, Edificio Casa Marroqui", 1),
        ("AmazINN Casa Guogu", None, "Calle 13 Este", 1),
        ("Casa Mallet", None, "Casco Viejo", 1),
        ("Casa Lourdes", None, "Casco Viejo", 1),
        ("La Isabela Suites", None, "Calle 9 Este", 1),
        ("Hotel Caracas", None, "Calle 12 Este y Calle C", 1),
        ("Art Casco The Hotel", None, "Calle 16 Oeste", 1),
        ("Hotel HSR", None, "Avenida Justo Arosemena", 1),
        ("Hotel Faranda Express Soloy", None, "Avenida Peru y Calle 30", 1),
        ("Hotel Acapulco", None, "Calle 30 y Ave. Cuba", 1),
        ("Hotel Marparaiso", None, "Calle 34 y Ave. Cuba", 1),
        ("Hotel Pension Corona", None, "Calle 35 y Ave. Cuba", 1),
        ("Hotel Latino", None, "Calle 31 y Ave. Peru", 1),
        ("Hotel Discovery", None, "Ave. Ecuador y Calle 34", 1),
        ("Hotel Dos Mares", None, "Calle 30 y Ave. Peru", 1),
        ("Hotel Caribe", None, "Avenida Peru y Calle 28", 1),
        ("Hotel Sevilla", None, "Avenida Justo Arosemena", 1),
        ("Hotel Lisboa", None, "Ave. Cuba y Calle 31", 1),
        ("Hotel Benidorm", None, "Ave. Peru y Calle 29", 1),
        ("Hotel Costa Inn", None, "Ave. Peru y Calle 39", 1),
        ("Hotel San Thomas Inn", None, "Calle 39", 1),

        # Zone 2: Calidonia / Bella Vista / Marbella (72 hotels)
        ("Hotel Riu Plaza Panama", None, "Calle 50 y 53 Este, Urbanización Marbella", 2),
        ("Hilton Panama", None, "Avenida Balboa y Calle 47 Este", 2),
        ("Waldorf Astoria Panama", None, "Calle 47 y Calle Uruguay", 2),
        ("InterContinental Miramar Panama", None, "Avenida Balboa y Federico Boyd", 2),
        ("Le Méridien Panama", None, "Avenida Balboa y Calle Uruguay", 2),
        ("Sheraton Grand Panama", None, "Via Israel y Calle 77", 2),
        ("Aloft Panama", None, "Via Israel y Calle 76", 2),
        ("Marriott Panama Hotel", None, "Calle 52 y Ricardo Arias, Área Bancaria", 2),
        ("Hyatt Regency Panama City", None, "Calle 50 y 54 Este", 2),
        ("Bristol Panama", None, "Avenida Aquilino de la Guardia", 2),
        ("W Panama", None, "Calle Aquilino de la Guardia y Calle 50", 2),
        ("Eurostars Panama City", None, "Calle 50 y Calle 53", 2),
        ("Las Americas Golden Tower", None, "Avenida Balboa con Calle 53", 2),
        ("Megapolis Hotel Panama", None, "Avenida Balboa", 2),
        ("Decapolis Hotel Panama City", None, "Avenida Balboa y Calle 53", 2),
        ("Plaza Paitilla Inn", None, "Via Italia y Avenida Balboa", 2),
        ("Hotel El Panama by Faranda Grand", None, "Via España 111", 2),
        ("Torres de Alba Hotel & Suites", None, "Calle Eusebio A. Morales y Calle 55", 2),
        ("Best Western Plus Panama Zen", None, "Calle 58 y Via Argentina", 2),
        ("Novotel Panama City", None, "Via España y Calle 52", 2),
        ("TRYP by Wyndham Panama Centro", None, "Calle 52 Este", 2),
        ("Hampton by Hilton Panama", None, "Calle 54 y Eusebio Morales", 2),
        ("AC Hotel by Marriott", None, "Calle 52 y Ricardo Arias", 2),
        ("Crowne Plaza Panama", None, "Via España y Manuel E. Batista", 2),
        ("Holiday Inn Panama Distrito Financiero", None, "Calle 52 y Ricardo Arias", 2),
        ("Ramada Plaza by Wyndham Punta Pacifica", None, "Calle Elias del Valle", 2),
        ("Courtyard by Marriott Multiplaza Mall", None, "Via Israel", 2),
        ("Residence Inn by Marriott San Francisco", None, "Via Israel y Calle 77", 2),
        ("Hotel Principe", None, "Via Argentina", 2),
        ("Riande Urban Hotel", None, "Calle 48 y Ricardo Arias", 2),
        ("Waymore Hotel Spa & Casino", None, "Calle Augusto Samuel Boyd", 2),
        ("Hotel Milan", None, "Calle Eusebio Morales", 2),
        ("Hotel Ojos del Rio", None, "Avenida Justo Arosemena", 2),
        ("Hotel Innfiniti", None, "Calle 37 y Ave. Cuba", 2),
        ("Hotel Bahia Suites", None, "Calle 31 y Ave. Cuba", 2),
        ("Euro Hotel", None, "Via España y Calle 33", 2),
        ("PH Window Tower", None, "Calle 74 Este", 2),
        ("PH Emporium Tower", None, "Calle 76 Este", 2),
        ("PH Sol Marina", None, "Marbella", 2),
        ("PH Marabierto", None, "Calle Juan de la Guardia", 2),
        ("PH Torres del Pacifico", None, "Avenida Balboa", 2),
        ("PH Yoo Panama", None, "Avenida Balboa", 2),
        ("PH Arts Tower", None, "Avenida Balboa", 2),
        ("PH Rivage", None, "Avenida Balboa", 2),
        ("PH Yacht Club Tower", None, "Avenida Balboa", 2),
        ("PH Allure", None, "Ave. Central España", 2),
        ("PH Villa del Mar", None, "Avenida Balboa", 2),
        ("PH Grandbay", None, "Avenida Balboa", 2),
        ("PH Waters on the Bay", None, "Avenida Balboa", 2),
        ("PH Twin Towers", None, "Calle Uruguay", 2),
        ("PH Luxor 400", None, "El Cangrejo", 2),
        ("PH Victory Sport", None, "San Francisco", 2),
        ("PH Sky 66", None, "San Francisco", 2),
        ("PH Park Loft", None, "San Francisco", 2),
        ("PH San Francisco Bay", None, "San Francisco", 2),
        ("PH Waterfalls", None, "San Francisco", 2),
        ("PH Nautica Tower", None, "San Francisco", 2),
        ("PH Kolosal", None, "San Francisco", 2),
        ("PH Tao Tower", None, "San Francisco", 2),
        ("Hotel Montreal", None, "Via España y Calle 52", 2),
        ("Hotel California Panama", None, "Via España", 2),
        ("Nomada Republic Hotel", None, "Marbella", 2),
        ("Serenade Lodging Rooms", None, "Calle 75 Este", 2),
        ("Boutique Apartments Atlapa", None, "Calle 76 Este", 2),
        ("Gran Evenia Panama", None, "Calle 52 Este", 2),

        # Zone 3: Obarrio / El Cangrejo / Condado del Rey (28 hotels)
        ("Sortis Hotel Spa & Casino", None, "Calle 56 y 57 Este, Obarrio", 3),
        ("Global Hotel Panama", None, "Calle 54 Este, Obarrio", 3),
        ("The Saba Hotel", None, "Calle Eusebio A. Morales, El Cangrejo", 3),
        ("Victoria Hotel and Suites", None, "Calle D y Via Argentina", 3),
        ("Toscana Inn Hotel", None, "Calle D y Via Argentina", 3),
        ("Ramada Panama Centro", None, "Via Argentina, El Cangrejo", 3),
        ("Hilton Garden Inn Downtown", None, "Calle 49B Oeste, El Cangrejo", 3),
        ("Exe Oriental Panama", None, "Calle 52 Este, Obarrio", 3),
        ("Hotel Coral Suites", None, "Calle 49 Este, Obarrio", 3),
        ("Hotel Suites Ambassador", None, "Via Italia, El Cangrejo", 3),
        ("Hotel & Casino Central Park", None, "Via Transistmica, Bethania", 3),
        ("Best Western El Dorado", None, "Avenida Miguel Brostella", 3),
        ("Hostal Los Angeles", None, "Calle Los Angeles", 3),
        ("Hotel La Cresta Inn", None, "Via España", 3),
        ("Nomada Container Hotel", None, "Calle 50", 3),
        ("OyeBonita Hostel", None, "Calle 64 Oeste", 3),
        ("Panama House B&B", None, "Calle 55 Este, Obarrio", 3),
        ("Residencial Ibiza", None, "Avenida De La Paz, El Ingenio", 3),
        ("Totumas Lodge", None, "Ave Martin Luther King, Hato Pintado", 3),
        ("Hotel Pensión Anduriña", None, "Avenida Republica del Perú", 3),
        ("Hotel Via España", None, "Via España y Calle 50", 3),
        ("Hotel Marbella", None, "Calle D, El Cangrejo", 3),
        ("Best Western Plus Zen El Cangrejo", None, "Calle 58 y Via Argentina", 3),
        ("Metro Hotel Panama", None, "Via España", 3),
        ("Hotel Grand International", None, "Via Argentina", 3),
        ("Baru Lodge Panama", None, "Bella Vista", 3),
        ("Mediterranean Dreams", None, "El Carmen", 3),

        # Zone 4: Costa del Este / Panama Pacifico (33 hotels)
        ("JW Marriott Panama", None, "Calle Punta Colon, Punta Pacifica", 4),
        ("The Westin Panama", None, "Avenida Centenario, Costa del Este", 4),
        ("Arcadia Condo Suites", None, "Calle Rio Mar, Costa del Este", 4),
        ("Residence Inn by Marriott Costa del Este", None, "Costa del Este", 4),
        ("Ramada Plaza Punta Pacifica", None, "Calle Elias del Valle", 4),
        ("PH The Towers", None, "Calle Winston Churchill", 4),
        ("PH Grand Tower", None, "Ave. Isaac Hanono", 4),
        ("PH Ocean Two", None, "Avenida Costa del Mar", 4),
        ("PH Vitri Tower", None, "Avenida Costa del Mar", 4),
        ("PH Pearl Tower", None, "Avenida Costa del Mar", 4),
        ("PH Aqualina", None, "Calle Punta Colon", 4),
        ("PH Oasis on the Bay", None, "Punta Pacifica", 4),
        ("PH Bellagio", None, "Punta Pacifica", 4),
        ("PH Matisse", None, "Costa del Este", 4),
        ("PH Marea II", None, "Costa del Este", 4),
        ("PH The Regent", None, "Avenida Centenario", 4),
        ("PH Parkside", None, "Costa del Este", 4),
        ("PH Titanium", None, "Costa del Este", 4),
        ("PH Sea Point", None, "Calle 50", 4),
        ("PH Quartier del Mar", None, "Ave. Cincuentenario", 4),
        ("PH Top Towers", None, "Costa del Este", 4),
        ("PH Acqua II", None, "Costa del Este", 4),
        ("PH Park Lane", None, "Costa del Este", 4),
        ("PH Bali", None, "Costa del Este", 4),
        ("PH Aurum", None, "Costa del Este", 4),
        ("PH Ten Tower", None, "Costa del Este", 4),
        ("PH Country Club", None, "Costa del Este", 4),
        ("PH Ocean One", None, "Costa del Este", 4),
        ("PH Dream Plaza", None, "Costa del Este", 4),
        ("PH Oceans II", None, "Juan Diaz", 4),
        ("PH Costa Real", None, "Costa del Este", 4),
        ("PH Parque del Mar", None, "Costa del Este", 4),

        # Zone 5: Tocumen / Airport Area (17 hotels)
        ("Crowne Plaza Panama Airport", None, "Ave. Domingo Diaz", 5),
        ("Riande Aeropuerto Hotel", None, "Via Tocumen, Corredor Sur", 5),
        ("Courtyard Marriott Metromall", None, "Ave. Domingo Diaz, Metromall", 5),
        ("The Santa Maria Hotel & Golf Resort", None, "Santa Maria Boulevard", 5),
        ("ORHO Hotel Boutique Airport", None, "Calle Aeropuerto", 5),
        ("Express Inn PTY Airport", None, "Calle Enrique Malek", 5),
        ("Hotel San Thomas Inn Tocumen", None, "Avenida Domingo Diaz", 5),
        ("Hostal Gemar", None, "Calle Principal", 5),
        ("Residencial El Amanecer", None, "Avenida Ricardo J. Alfaro", 5),
        ("PH Mystic Hills", None, "Ave. Domingo Diaz", 5),
        ("B&B Lodgings Cerro Azul", None, "Calle Tercera, Cerro Azul", 5),
        ("Pleasant Hostal", None, "El Valle de Cerro Azul", 5),
        ("Residencial Hotel Palazzo", None, "Calle Principal", 5),
        ("Zen Motel by RGS", None, "Ave. Domingo Diaz", 5),
        ("Hotel Villa Del Mar Juan Diaz", None, "Juan Diaz", 5),

        # Zone 6: Clayton / Albrook / Amador (18 hotels)
        ("Radisson Hotel Panama Canal", None, "Amador Causeway", 6),
        ("Holiday Inn Panama Canal", None, "Ave. Omar Torrijos, Clayton", 6),
        ("The Beach House Amador", None, "Amador Causeway", 6),
        ("Albrook Inn", None, "Ave. Diogenes de la Rosa, Albrook", 6),
        ("Summit Rainforest & Golf Resort", None, "Gaillard Road", 6),
        ("Wyndham Albrook Mall Hotel", None, "Ave. La Amistad", 6),
        ("Canal Inn B&B", None, "Ave. Clayton", 6),
        ("The Balboa Inn", None, "Calle San Blas", 6),
        ("Clayton Park Apartments", None, "Ave. Clayton", 6),
        ("La Semilla Ecolodge", None, "Cerro Azul", 6),
        ("PH Amador Hill", None, "Amador Causeway", 6),
        ("PH Naos Harbour Island", None, "Amador Causeway", 6),
        ("MARINN Tropical Vibes", None, "Amador", 6),
        ("Hostal Entre 2 Aguas", None, "Calle Corozal", 6),
        ("Greg's Place Albrook", None, "Albrook", 6),
        ("Cabañas Colindas", None, "Calle Hospital", 6),
        ("Las Vegas Hotel-Suites Balboa", None, "Balboa", 6),

        # Zone 7: Veracruz / Panama Pacifico (20 hotels)
        ("Westin Playa Bonita Panama", None, "Playa Bonita Village", 7),
        ("Dreams Playa Bonita", None, "Playa Bonita Village", 7),
        ("Mahogany Suites", None, "Calle Cocolí", 7),
        ("PH Mosaic", None, "Panama Pacifico Blvd", 7),
        ("PH Explora", None, "Howard", 7),
        ("PH Nativa", None, "Howard", 7),
        ("PH Centriqo", None, "Howard", 7),
        ("PH Midrise Woodlands", None, "Howard", 7),
        ("PH River Valley", None, "Howard", 7),
        ("Vista Venao Apartments", None, "Playa Veracruz", 7),
        ("Coastal Nature Haven", None, "Playa Veracruz", 7),
        ("PH Imaterra", None, "Howard", 7),
        ("PH Bosques del Pacifico", None, "Howard", 7),
        ("PH Parterre", None, "Howard", 7),
        ("PH Casa Bonita", None, "Playa Bonita Village", 7),
        ("PH Woodlands", None, "Howard", 7),
        ("Howard Residences", None, "Howard", 7),
        ("Villas de Howard", None, "Howard", 7),
        ("PH Explora Howard", None, "Howard", 7),
        ("Cëntriqo Howard", None, "Howard", 7),
    ]

    hotels_to_insert = [
        {"name": name, "name_es": name_es, "address": addr, "zone_id": zone_id, "is_active": True}
        for name, name_es, addr, zone_id in hotel_data
    ]

    op.bulk_insert(hotels, hotels_to_insert)


def downgrade() -> None:
    """Revert to original seed data."""
    # Remove Zone 7 transport rates
    op.execute("DELETE FROM zone_transport_rates WHERE from_zone_id = 7 OR to_zone_id = 7")

    # Remove Zone 7
    op.execute("DELETE FROM zones WHERE id = 7")

    # Hotels will need to be re-seeded from original migration
    op.execute("DELETE FROM hotels")