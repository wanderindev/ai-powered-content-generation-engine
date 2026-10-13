"""Split child category into 5-10 and 10-17 age groups.

Revision ID: 20260201_child_split
Revises: 20260201_ticket_prices
Create Date: 2026-02-01
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal

revision = "20260201_child_split"
down_revision = "20260201_ticket_prices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Split child ticket category into 5-10 and 10-17 age groups."""

    # 1. Rename existing child columns to child_5_10
    op.alter_column("attractions", "ticket_child_tourist",
                    new_column_name="ticket_child_5_10_tourist")
    op.alter_column("attractions", "ticket_child_resident",
                    new_column_name="ticket_child_5_10_resident")

    # 2. Add new columns for 10-17 age group
    op.add_column(
        "attractions",
        sa.Column("ticket_child_10_17_tourist", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "attractions",
        sa.Column("ticket_child_10_17_resident", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )

    conn = op.get_bind()

    # 3. For most attractions, set 10-17 = same as 5-10
    # First, copy 5-10 values to 10-17 for all attractions
    conn.execute(sa.text("""
        UPDATE attractions SET
            ticket_child_10_17_tourist = ticket_child_5_10_tourist,
            ticket_child_10_17_resident = ticket_child_5_10_resident
    """))

    # 4. Update Casco Churches Package (ID 7) specifically:
    # 5-10: $4 (churches only, no bell tower)
    # 10-17: $12 (full package with bell tower)
    conn.execute(sa.text("""
        UPDATE attractions SET
            ticket_child_5_10_tourist = 4.00,
            ticket_child_5_10_resident = 4.00,
            ticket_child_10_17_tourist = 12.00,
            ticket_child_10_17_resident = 12.00,
            notes = 'Includes: San Francisco Bell Tower ($8, requires age 10+), Metropolitan Cathedral ($2), San Jose Golden Altar ($2). Children 5-10 pay $4 (churches only). Children 10-17 pay $12 (full package). Under 5 free.'
        WHERE id = 7
    """))

    # 5. Update participant breakdown schema in bookings table
    # The participants JSON column will now expect child_5_10 and child_10_17 instead of child_5_17


def downgrade() -> None:
    """Merge child categories back to single 5-17 group."""

    # Drop the 10-17 columns
    op.drop_column("attractions", "ticket_child_10_17_tourist")
    op.drop_column("attractions", "ticket_child_10_17_resident")

    # Rename 5-10 back to child
    op.alter_column("attractions", "ticket_child_5_10_tourist",
                    new_column_name="ticket_child_tourist")
    op.alter_column("attractions", "ticket_child_5_10_resident",
                    new_column_name="ticket_child_resident")