"""Add fixed_price_config JSONB column to tours table.

Supports tours with fixed per-person pricing (e.g. open group tours)
instead of the hourly pricing engine.

Revision ID: 20260325a
Revises: (head)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260325a"
down_revision = "20260308b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tours", sa.Column("fixed_price_config", JSONB, nullable=True))

    # Set config for Siete Iglesias tour
    op.execute("""
        UPDATE tours
        SET fixed_price_config = '{
            "adult": 20.00,
            "child_5_10": 12.50,
            "child_under_5": 0,
            "senior_discount_pct": 20
        }'::jsonb
        WHERE slug = 'siete-iglesias'
    """)


def downgrade() -> None:
    op.drop_column("tours", "fixed_price_config")