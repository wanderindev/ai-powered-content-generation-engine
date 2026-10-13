"""edu_slides: structured per-slide rows for generated narrated decks (#320).

The slide generator now emits structured slides (one row per slide) instead of
markdown on ``EduMaterial.content``, giving the render (#321) and audio (#322)
stages a per-slide home (``image_url`` / ``audio_url`` / ``duration_ms``).

``template`` is a closed vocabulary — String + named CHECK (issue #24), mirroring
``SlideTemplate`` in ``app.models.enums``; the stable ``ck_edu_slides_template``
name makes adding a template a clean drop-and-recreate later. Rows are deleted
with their parent ``edu_materials`` row (FK ``ON DELETE CASCADE``).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0014_edu_slides"
down_revision = "0013_crawl_jobs"
branch_labels = None
depends_on = None

_TEMPLATE_VALUES = (
    "title",
    "section",
    "bullets",
    "two-col-image-left",
    "two-col-image-right",
    "full-image",
    "timeline",
    "map",
    "closing",
)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        "edu_slides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("image_query", sa.String(length=500), nullable=True),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("audio_url", sa.String(length=500), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["material_id"], ["edu_materials.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            _in_clause("template", _TEMPLATE_VALUES), name="ck_edu_slides_template"
        ),
        sa.UniqueConstraint("material_id", "position", name="uq_edu_slides_material_position"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edu_slides_material_id", "edu_slides", ["material_id"])


def downgrade() -> None:
    op.drop_index("ix_edu_slides_material_id", table_name="edu_slides")
    op.drop_table("edu_slides")
