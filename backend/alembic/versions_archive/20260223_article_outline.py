"""Add outline column to articles table.

Revision ID: 20260223_article_outline
Revises: 20260201_child_split
Create Date: 2026-02-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260223_article_outline"
down_revision = "20260201_child_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("outline", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "outline")
