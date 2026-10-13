"""Update ticket prices from research and add Casco attractions.

Revision ID: 20260201_ticket_prices
Revises: 20260201_attraction_rates
Create Date: 2026-02-01
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "20260201_ticket_prices"
down_revision = "20260201_attraction_rates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Update ticket prices and add new attractions."""

    # 1. Add min_age column to attractions for age restrictions
    op.add_column(
        "attractions",
        sa.Column("min_age", sa.Integer, nullable=True, server_default="0"),
    )

    # 2. Update existing attraction prices
    # Using raw SQL for updates since we need to update by name/id

    conn = op.get_bind()

    # Miraflores Visitors Center (ID 1) - includes IMAX
    # Tourist: Adult $17.22, Child (6-12) $7.22, Under 6 free
    # Resident: Adult $3.00, Senior $1.50, Minor (0-18) free
    conn.execute(
        sa.text("""
            UPDATE attractions SET
                ticket_adult_tourist = 17.22,
                ticket_senior_tourist = 17.22,
                ticket_child_tourist = 7.22,
                ticket_infant_tourist = 0,
                ticket_adult_resident = 3.00,
                ticket_senior_resident = 1.50,
                ticket_child_resident = 0,
                ticket_infant_resident = 0,
                notes = 'Includes IMAX movie. Tourist child rate applies to ages 6-12. Resident minors (0-18) free.'
            WHERE id = 1
        """)
    )

    # Panama Viejo (ID 2)
    # Tourist: Adult $17.00, Child (6-17) $5.00
    # Resident: Adult $11.00, Senior $5.50, Child $2.00, Preschool $1.00
    conn.execute(
        sa.text("""
            UPDATE attractions SET
                ticket_adult_tourist = 17.00,
                ticket_senior_tourist = 17.00,
                ticket_child_tourist = 5.00,
                ticket_infant_tourist = 0,
                ticket_adult_resident = 11.00,
                ticket_senior_resident = 5.50,
                ticket_child_resident = 2.00,
                ticket_infant_resident = 1.00,
                notes = 'UNESCO World Heritage site. Free for residents on last Sunday of each month.'
            WHERE id = 2
        """)
    )

    # Biomuseo (ID 3)
    # Tourist: Adult $20.00, Minor (5-17) $12.00, Senior $16.00
    # Resident: Adult $10.00, Minor (5-17) $6.00, Senior $5.00
    conn.execute(
        sa.text("""
            UPDATE attractions SET
                ticket_adult_tourist = 20.00,
                ticket_senior_tourist = 16.00,
                ticket_child_tourist = 12.00,
                ticket_infant_tourist = 0,
                ticket_adult_resident = 10.00,
                ticket_senior_resident = 5.00,
                ticket_child_resident = 6.00,
                ticket_infant_resident = 0,
                notes = 'Frank Gehry designed museum. Child rate applies to ages 5-17.'
            WHERE id = 3
        """)
    )

    # Mi Pueblito (ID 4)
    # Tourist: Adult $3.00 (same for all tourist ages)
    # Resident: Adult $1.00, Senior free, Student free, Child (0-5) free
    conn.execute(
        sa.text("""
            UPDATE attractions SET
                ticket_adult_tourist = 3.00,
                ticket_senior_tourist = 3.00,
                ticket_child_tourist = 3.00,
                ticket_infant_tourist = 0,
                ticket_adult_resident = 1.00,
                ticket_senior_resident = 0,
                ticket_child_resident = 1.00,
                ticket_infant_resident = 0,
                notes = 'Municipal ethnographic complex. Resident seniors, students, and children under 5 free.'
            WHERE id = 4
        """)
    )

    # Canal Museum / Interoceanic Canal Museum (IDs 5, 6)
    # These are the same museum - update both with same prices
    # Tourist: Adult $15.00, Child (6-12) $7.50, Senior $15.00 (no discount)
    # Resident: Adult $5.00, Child $2.50, Senior $2.50
    conn.execute(
        sa.text("""
            UPDATE attractions SET
                ticket_adult_tourist = 15.00,
                ticket_senior_tourist = 15.00,
                ticket_child_tourist = 7.50,
                ticket_infant_tourist = 0,
                ticket_adult_resident = 5.00,
                ticket_senior_resident = 2.50,
                ticket_child_resident = 2.50,
                ticket_infant_resident = 0,
                notes = 'Museo del Canal Interoceánico. Free on Jan 9 (Martyrs Day) and Sep 9 (Anniversary) for nationals.'
            WHERE id IN (5, 6)
        """)
    )

    # 3. Add new attractions for Casco tours

    # Casco Churches Package (for Plazas Tour)
    # $12 total: Bell Tower $8 + Cathedral $2 + San Jose $2
    # min_age 10 for bell tower - children 5-9 pay reduced rate
    conn.execute(
        sa.text("""
            INSERT INTO attractions (
                id, name, name_es,
                ticket_adult_tourist, ticket_senior_tourist, ticket_child_tourist, ticket_infant_tourist,
                ticket_adult_resident, ticket_senior_resident, ticket_child_resident, ticket_infant_resident,
                min_age, notes, is_active
            ) VALUES (
                7, 'Casco Churches Package', 'Paquete Iglesias del Casco',
                12.00, 12.00, 12.00, 0,
                12.00, 12.00, 12.00, 0,
                10, 'Includes: San Francisco Bell Tower ($8), Metropolitan Cathedral ($2), San Jose Golden Altar ($2). Children under 10 cannot climb bell tower and pay reduced rate of $4.',
                true
            )
        """)
    )

    # Museo de la Merced (for Museums Tour)
    # $5 for ages 5+, flat rate
    conn.execute(
        sa.text("""
            INSERT INTO attractions (
                id, name, name_es,
                ticket_adult_tourist, ticket_senior_tourist, ticket_child_tourist, ticket_infant_tourist,
                ticket_adult_resident, ticket_senior_resident, ticket_child_resident, ticket_infant_resident,
                min_age, notes, is_active
            ) VALUES (
                8, 'Museo de la Merced', 'Museo de la Iglesia de la Merced',
                5.00, 5.00, 5.00, 0,
                5.00, 5.00, 5.00, 0,
                5, 'Church museum in Casco Antiguo. Flat rate $5 for ages 5+.',
                true
            )
        """)
    )

    # 4. Link new attractions to tours
    tour_attractions = sa.table(
        "tour_attractions",
        sa.column("tour_id", sa.Integer),
        sa.column("attraction_id", sa.Integer),
        sa.column("is_optional", sa.Boolean),
        sa.column("visit_order", sa.Integer),
    )

    # Add Casco Churches Package to Casco Plazas Tour (tour_id 2)
    op.bulk_insert(
        tour_attractions,
        [{"tour_id": 2, "attraction_id": 7, "is_optional": False, "visit_order": 1}],
    )

    # Add Museo de la Merced to Casco Museums Tour (tour_id 3)
    # Check existing attractions in tour 3 and add after them
    op.bulk_insert(
        tour_attractions,
        [{"tour_id": 3, "attraction_id": 8, "is_optional": False, "visit_order": 3}],
    )

    # 5. Add zone attraction rates for new attractions (Casco = Zone 1)
    # Using same rates as other Casco attractions (Canal Museum)
    zone_rates = sa.table(
        "zone_attraction_rates",
        sa.column("zone_id", sa.Integer),
        sa.column("attraction_id", sa.Integer),
        sa.column("rate_small", sa.Numeric),
        sa.column("rate_medium", sa.Numeric),
        sa.column("rate_large", sa.Numeric),
    )

    # Rates for attraction 7 (Casco Churches) and 8 (Museo de la Merced)
    # Copy from existing Casco attraction rates (attraction 5)
    new_rates = []
    for zone_id in range(1, 8):
        for attr_id in [7, 8]:
            if zone_id == 1:
                # Same zone - free
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("0"), "rate_medium": Decimal("0"), "rate_large": Decimal("0")
                })
            elif zone_id == 2:
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("10"), "rate_medium": Decimal("20"), "rate_large": Decimal("30")
                })
            elif zone_id == 3:
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("15"), "rate_medium": Decimal("25"), "rate_large": Decimal("35")
                })
            elif zone_id == 4:
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("15"), "rate_medium": Decimal("25"), "rate_large": Decimal("35")
                })
            elif zone_id == 5:
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("20"), "rate_medium": Decimal("30"), "rate_large": Decimal("40")
                })
            elif zone_id == 6:
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("15"), "rate_medium": Decimal("25"), "rate_large": Decimal("35")
                })
            elif zone_id == 7:
                new_rates.append({
                    "zone_id": zone_id, "attraction_id": attr_id,
                    "rate_small": Decimal("25"), "rate_medium": Decimal("35"), "rate_large": Decimal("45")
                })

    op.bulk_insert(zone_rates, new_rates)


def downgrade() -> None:
    """Remove new attractions and revert prices."""
    conn = op.get_bind()

    # Remove zone attraction rates for new attractions
    conn.execute(sa.text("DELETE FROM zone_attraction_rates WHERE attraction_id IN (7, 8)"))

    # Remove tour_attractions links
    conn.execute(sa.text("DELETE FROM tour_attractions WHERE attraction_id IN (7, 8)"))

    # Remove new attractions
    conn.execute(sa.text("DELETE FROM attractions WHERE id IN (7, 8)"))

    # Drop min_age column
    op.drop_column("attractions", "min_age")