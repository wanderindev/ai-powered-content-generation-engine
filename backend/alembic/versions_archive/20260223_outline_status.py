"""Add outline_status column to articles table.

Revision ID: 20260223_outline_status
Revises: 20260223_article_outline
Create Date: 2026-02-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260223_outline_status"
down_revision = "20260223_article_outline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("outline_status", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "outline_status")
