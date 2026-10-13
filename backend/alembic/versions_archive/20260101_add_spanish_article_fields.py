"""Add Spanish translation fields to articles table.

Revision ID: 20260101_spanish_fields
Revises:
Create Date: 2026-01-01
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260101_spanish_fields"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add Spanish translation columns to articles table."""
    op.add_column("articles", sa.Column("title_es", sa.String(255), nullable=True))
    op.add_column("articles", sa.Column("content_es", sa.Text(), nullable=True))
    op.add_column("articles", sa.Column("excerpt_es", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove Spanish translation columns from articles table."""
    op.drop_column("articles", "excerpt_es")
    op.drop_column("articles", "content_es")
    op.drop_column("articles", "title_es")
