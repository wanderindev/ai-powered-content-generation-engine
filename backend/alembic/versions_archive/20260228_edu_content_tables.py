"""Create educational content tables.

Revision ID: 20260228_edu_content_tables
Revises: 20260225_media_candidates
Create Date: 2026-02-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260228_edu_content_tables"
down_revision = "20260225_media_candidates"

# Reference the existing enum type
content_status = postgresql.ENUM(
    "PENDING", "APPROVED", "REJECTED", name="content_status_type", create_type=False
)


def upgrade() -> None:
    # --- edu_suggestions ---
    op.create_table(
        "edu_suggestions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("excursion_key", sa.String(100), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("grade_bands", sa.ARRAY(sa.String), nullable=True),
        sa.Column("sub_topics", sa.ARRAY(sa.String), nullable=True),
        sa.Column(
            "status",
            content_status,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_edu_suggestions_excursion_key",
        "edu_suggestions",
        ["excursion_key"],
    )

    # --- edu_research ---
    op.create_table(
        "edu_research",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "suggestion_id",
            sa.Integer,
            sa.ForeignKey("edu_suggestions.id"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "status",
            content_status,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- edu_materials ---
    op.create_table(
        "edu_materials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "suggestion_id",
            sa.Integer,
            sa.ForeignKey("edu_suggestions.id"),
            nullable=False,
        ),
        sa.Column(
            "research_id",
            sa.Integer,
            sa.ForeignKey("edu_research.id"),
            nullable=False,
        ),
        sa.Column("material_type", sa.String(20), nullable=False),
        sa.Column("grade_band", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_es", sa.Text, nullable=True),
        sa.Column(
            "status",
            content_status,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "suggestion_id",
            "material_type",
            "grade_band",
            name="uq_edu_materials_suggestion_type_grade",
        ),
    )

    # --- edu_material_files ---
    op.create_table(
        "edu_material_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "material_id",
            sa.Integer,
            sa.ForeignKey("edu_materials.id"),
            nullable=False,
        ),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("gamma_url", sa.String(500), nullable=True),
        sa.Column("file_url", sa.String(500), nullable=False),
        sa.Column("file_format", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("edu_material_files")
    op.drop_table("edu_materials")
    op.drop_table("edu_research")
    op.drop_index("ix_edu_suggestions_excursion_key", table_name="edu_suggestions")
    op.drop_table("edu_suggestions")
