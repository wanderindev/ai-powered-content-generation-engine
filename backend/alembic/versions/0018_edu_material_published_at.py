"""edu material published_at (public delivery surface)

Adds ``edu_materials.published_at`` (nullable timestamptz, indexed) — publication
is separate from approval, mirroring ``articles.published_at`` (#326). A material
is public only when APPROVED *and* published_at is set and in the past.

Revision ID: 0018_edu_material_published_at
Revises: 0017_edu_slide_media
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_edu_material_published_at"
down_revision = "0017_edu_slide_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "edu_materials",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_edu_materials_published_at", "edu_materials", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_edu_materials_published_at", table_name="edu_materials")
    op.drop_column("edu_materials", "published_at")
