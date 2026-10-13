"""edu_materials integrity: natural-key uniqueness, one-research-FK CHECK, drop dead files table.

Revision ID: 0012_edu_materials_unique_key
Revises: 0011_booking_slot_unique_index
Create Date: 2026-06-01

Part of epic #279 (edu-materials generation & storage robustness):

* **#97** — add a UNIQUE constraint on ``(research_id, material_type, grade_band)``,
  the natural key of a generated study guide / slide deck. It backstops the
  read-then-write upsert in ``edu_material_generation`` against concurrent
  duplicate inserts and underpins the per-band commit (#90). Uploaded rows have
  ``research_id`` NULL (they key off ``blog_research_id``); under Postgres'
  default NULLS DISTINCT they never collide on this key. Prod is already
  duplicate-free, so the constraint builds cleanly.
* **#95** — add a CHECK enforcing exactly one of ``research_id`` /
  ``blog_research_id`` is set (the discriminated-union invariant). Prod data
  satisfies it today (6 generated + 3 uploaded rows, none with both/neither).
* **#96** — drop the dead ``edu_material_files`` table (0 rows in prod;
  scaffolding for the not-yet-shipped Gamma export phase). Downgrade recreates it
  from its post-0010 (timestamptz) shape.

If the unique constraint fails to build on prod, a real duplicate already exists —
investigate rather than force it.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_edu_materials_unique_key"
down_revision = "0011_booking_slot_unique_index"
branch_labels = None
depends_on = None

_UNIQUE_NAME = "uq_edu_materials_research_type_band"
_CHECK_NAME = "ck_edu_materials_one_research_fk"


def upgrade() -> None:
    op.create_unique_constraint(
        _UNIQUE_NAME,
        "edu_materials",
        ["research_id", "material_type", "grade_band"],
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "edu_materials",
        "num_nonnulls(research_id, blog_research_id) = 1",
    )
    op.drop_table("edu_material_files")


def downgrade() -> None:
    op.create_table(
        "edu_material_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("gamma_url", sa.String(length=500), nullable=True),
        sa.Column("file_url", sa.String(length=500), nullable=False),
        sa.Column("file_format", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["material_id"], ["edu_materials.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_edu_material_files_material_id",
        "edu_material_files",
        ["material_id"],
    )
    op.drop_constraint(_CHECK_NAME, "edu_materials", type_="check")
    op.drop_constraint(_UNIQUE_NAME, "edu_materials", type_="unique")
