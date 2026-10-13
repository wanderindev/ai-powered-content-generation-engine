"""Add search_terms to media and create media_tags association table.

Revision ID: 20260308a
Revises: 20260307a
"""

from alembic import op
import sqlalchemy as sa

revision = "20260308a"
down_revision = "20260307c"
branch_labels = None
depends_on = None


def upgrade():
    # Add search_terms column to media
    op.add_column("media", sa.Column("search_terms", sa.Text(), nullable=True))

    # Create media_tags association table
    op.create_table(
        "media_tags",
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade():
    op.drop_table("media_tags")
    op.drop_column("media", "search_terms")