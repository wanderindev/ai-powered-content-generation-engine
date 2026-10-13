"""edu guide structured content blocks (#340)

Adds ``content_blocks`` / ``content_blocks_es`` JSONB columns to ``edu_materials``.
Generated study guides become a list of typed blocks (the source of the branded
PDF); ``content``/``content_es`` keep a markdown projection for back-compat.

Revision ID: 0016_edu_guide_blocks
Revises: 0015_edu_video_material
Create Date: 2026-06-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_edu_guide_blocks"
down_revision = "0015_edu_video_material"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "edu_materials",
        sa.Column("content_blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "edu_materials",
        sa.Column("content_blocks_es", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("edu_materials", "content_blocks_es")
    op.drop_column("edu_materials", "content_blocks")
