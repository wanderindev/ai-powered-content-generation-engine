"""Add thumbnail_path column to media table.

Revision ID: 20260307a
Revises: 20260228_edu_content_tables
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260307a"
down_revision = "20260228_populate_edu_sug"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media", sa.Column("thumbnail_path", sa.String(512), nullable=True))


def downgrade():
    op.drop_column("media", "thumbnail_path")