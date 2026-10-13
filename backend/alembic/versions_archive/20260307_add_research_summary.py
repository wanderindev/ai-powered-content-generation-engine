"""Add summary column to research table.

Revision ID: 20260307b
Revises: 20260307a
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260307b"
down_revision = "20260307a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("research", sa.Column("summary", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("research", "summary")