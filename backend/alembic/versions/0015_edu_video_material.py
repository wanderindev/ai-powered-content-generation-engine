"""Add the ``video`` material type + ``edu_materials.duration_ms`` (#323).

The narrated slide MP4 (#323) is stored as an ``EduMaterial`` row with the new
``material_type='video'``. Per the issue #24 status-column rules:
  * extend the ``ck_edu_materials_material_type`` CHECK to admit ``'video'`` — a
    clean drop-and-recreate of the stable-named constraint (the 0006 snapshot was
    updated in lockstep so the model↔enum↔migration guard test stays green);
  * add a nullable ``duration_ms`` column to hold the video's playback length
    (NULL for the non-video material types).

Revision ID: 0015_edu_video_material
Revises: 0014_edu_slides
Create Date: 2026-06-21
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0015_edu_video_material"
down_revision = "0014_edu_slides"
branch_labels = None
depends_on = None

_CK = "ck_edu_materials_material_type"
_WITH_VIDEO = ["guide", "slides", "infographic", "podcast", "video"]
_WITHOUT_VIDEO = ["guide", "slides", "infographic", "podcast"]


def _set_material_type_check(values: list[str]) -> None:
    allowed = ", ".join(f"'{v}'" for v in values)
    op.execute(f"ALTER TABLE public.edu_materials DROP CONSTRAINT IF EXISTS {_CK}")
    op.execute(
        f"ALTER TABLE public.edu_materials "
        f"ADD CONSTRAINT {_CK} CHECK (material_type IN ({allowed}))"
    )


def upgrade() -> None:
    op.add_column("edu_materials", sa.Column("duration_ms", sa.Integer(), nullable=True))
    _set_material_type_check(_WITH_VIDEO)


def downgrade() -> None:
    _set_material_type_check(_WITHOUT_VIDEO)
    op.drop_column("edu_materials", "duration_ms")