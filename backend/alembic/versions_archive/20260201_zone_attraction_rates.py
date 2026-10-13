"""Add zone to attraction transport rates.

Revision ID: 20260201_attraction_rates
Revises: 20260201_hotel_data
Create Date: 2026-02-01
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "20260201_attraction_rates"
down_revision = "20260201_hotel_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create zone_attraction_rates table and populate with rates."""

    # Create the table
    op.create_table(
        "zone_attraction_rates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("zone_id", sa.Integer, sa.ForeignKey("zones.id"), nullable=False),
        sa.Column("attraction_id", sa.Integer, sa.ForeignKey("attractions.id"), nullable=False),
        sa.Column("rate_small", sa.Numeric(10, 2), nullable=False),  # 1-3 passengers
        sa.Column("rate_medium", sa.Numeric(10, 2), nullable=False),  # 4-6 passengers
        sa.Column("rate_large", sa.Numeric(10, 2), nullable=False),  # 7+ passengers
        sa.UniqueConstraint("zone_id", "attraction_id", name="uq_zone_attraction_rate"),
    )

    # Define the rates table for bulk insert
    rates_table = sa.table(
        "zone_attraction_rates",
        sa.column("zone_id", sa.Integer),
        sa.column("attraction_id", sa.Integer),
        sa.column("rate_small", sa.Numeric),
        sa.column("rate_medium", sa.Numeric),
        sa.column("rate_large", sa.Numeric),
    )

    # Attraction IDs:
    # 1: Miraflores Visitors Center
    # 2: Panama Viejo
    # 3: Biomuseo
    # 4: Mi Pueblito
    # 5: Canal Museum (Casco Antiguo)
    # 6: Interoceanic Canal Museum (Casco Antiguo)

    rates_data = []

    # Zone 1 (Casco Antiguo) - Casco attractions are free (walking distance)
    zone_1_rates = [
        (1, 5, 0, 0, 0),       # Canal Museum - free
        (1, 6, 0, 0, 0),       # Interoceanic Canal Museum - free
        (1, 1, 15, 25, 35),    # Miraflores
        (1, 3, 10, 20, 30),    # Biomuseo
        (1, 2, 10, 20, 30),    # Panama Viejo
        (1, 4, 10, 20, 30),    # Mi Pueblito
    ]

    # Zone 2 (Calidonia / Bella Vista / Marbella)
    zone_2_rates = [
        (2, 5, 10, 20, 30),    # Canal Museum (Casco)
        (2, 6, 10, 20, 30),    # Interoceanic Canal Museum (Casco)
        (2, 1, 15, 25, 35),    # Miraflores
        (2, 3, 15, 25, 35),    # Biomuseo
        (2, 2, 10, 20, 30),    # Panama Viejo
        (2, 4, 15, 25, 35),    # Mi Pueblito
    ]

    # Zone 3 (Obarrio / El Cangrejo / Condado del Rey)
    zone_3_rates = [
        (3, 5, 15, 25, 35),    # Canal Museum (Casco)
        (3, 6, 15, 25, 35),    # Interoceanic Canal Museum (Casco)
        (3, 1, 20, 30, 40),    # Miraflores
        (3, 3, 20, 30, 40),    # Biomuseo
        (3, 2, 10, 20, 30),    # Panama Viejo
        (3, 4, 20, 30, 40),    # Mi Pueblito
    ]

    # Zone 4 (Costa del Este) - similar to Zone 3 based on location
    zone_4_rates = [
        (4, 5, 15, 25, 35),    # Canal Museum (Casco)
        (4, 6, 15, 25, 35),    # Interoceanic Canal Museum (Casco)
        (4, 1, 20, 30, 40),    # Miraflores
        (4, 3, 20, 30, 40),    # Biomuseo
        (4, 2, 10, 20, 30),    # Panama Viejo
        (4, 4, 20, 30, 40),    # Mi Pueblito
    ]

    # Zone 5 (Tocumen / Airport Area)
    zone_5_rates = [
        (5, 5, 20, 30, 40),    # Canal Museum (Casco)
        (5, 6, 20, 30, 40),    # Interoceanic Canal Museum (Casco)
        (5, 1, 30, 40, 50),    # Miraflores
        (5, 3, 30, 40, 50),    # Biomuseo
        (5, 2, 20, 30, 40),    # Panama Viejo
        (5, 4, 30, 40, 50),    # Mi Pueblito
    ]

    # Zone 6 (Clayton / Albrook / Amador)
    zone_6_rates = [
        (6, 5, 15, 25, 35),    # Canal Museum (Casco)
        (6, 6, 15, 25, 35),    # Interoceanic Canal Museum (Casco)
        (6, 1, 10, 20, 30),    # Miraflores
        (6, 3, 15, 25, 35),    # Biomuseo
        (6, 2, 15, 25, 35),    # Panama Viejo
        (6, 4, 15, 25, 35),    # Mi Pueblito
    ]

    # Zone 7 (Veracruz / Panama Pacifico)
    zone_7_rates = [
        (7, 5, 25, 35, 45),    # Canal Museum (Casco)
        (7, 6, 25, 35, 45),    # Interoceanic Canal Museum (Casco)
        (7, 1, 30, 40, 50),    # Miraflores
        (7, 3, 25, 35, 45),    # Biomuseo
        (7, 2, 30, 40, 50),    # Panama Viejo
        (7, 4, 25, 35, 45),    # Mi Pueblito
    ]

    # Combine all rates
    all_rates = (
        zone_1_rates + zone_2_rates + zone_3_rates + zone_4_rates +
        zone_5_rates + zone_6_rates + zone_7_rates
    )

    # Convert to dicts for bulk insert
    for zone_id, attraction_id, small, medium, large in all_rates:
        rates_data.append({
            "zone_id": zone_id,
            "attraction_id": attraction_id,
            "rate_small": Decimal(str(small)),
            "rate_medium": Decimal(str(medium)),
            "rate_large": Decimal(str(large)),
        })

    op.bulk_insert(rates_table, rates_data)


def downgrade() -> None:
    """Drop zone_attraction_rates table."""
    op.drop_table("zone_attraction_rates")