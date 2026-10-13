"""edu slide media picks + source briefing (slides redesign)

Adds the human-picked photo columns to ``edu_slides`` (``media_id`` /
``media_path`` — same pattern as guide image blocks: generation only ever emits
an ``image_query`` placeholder, an editor picks the real photo in the admin) and
``edu_materials.source_briefing`` (the condensed research briefing slides were
generated from, persisted so the later narration pass works from the SAME facts
the human approved).

Revision ID: 0017_edu_slide_media
Revises: 0016_edu_guide_blocks
Create Date: 2026-07-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_edu_slide_media"
down_revision = "0016_edu_guide_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edu_slides", sa.Column("media_id", sa.Integer(), nullable=True))
    op.add_column("edu_slides", sa.Column("media_path", sa.String(500), nullable=True))
    op.create_foreign_key(
        "fk_edu_slides_media_id_media",
        "edu_slides",
        "media",
        ["media_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_edu_slides_media_id", "edu_slides", ["media_id"])
    op.add_column("edu_materials", sa.Column("source_briefing", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("edu_materials", "source_briefing")
    op.drop_index("ix_edu_slides_media_id", table_name="edu_slides")
    op.drop_constraint("fk_edu_slides_media_id_media", "edu_slides", type_="foreignkey")
    op.drop_column("edu_slides", "media_path")
    op.drop_column("edu_slides", "media_id")
