"""Refactor edu_materials: remove suggestion_id, add blog_research_id and file columns.

Revision ID: 20260307c
Revises: 20260307b
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260307c"
down_revision = "20260307b"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the unique constraint that uses suggestion_id
    op.drop_constraint("uq_edu_materials_suggestion_type_grade", "edu_materials", type_="unique")

    # Drop suggestion_id column
    op.drop_column("edu_materials", "suggestion_id")

    # Make research_id nullable (was NOT NULL)
    op.alter_column("edu_materials", "research_id", existing_type=sa.Integer(), nullable=True)

    # Make content nullable (binary assets have no markdown)
    op.alter_column("edu_materials", "content", existing_type=sa.Text(), nullable=True)

    # Make grade_band nullable (blog materials don't have grade bands)
    op.alter_column("edu_materials", "grade_band", existing_type=sa.String(20), nullable=True)

    # Add blog_research_id FK
    op.add_column("edu_materials", sa.Column("blog_research_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_edu_materials_blog_research",
        "edu_materials",
        "research",
        ["blog_research_id"],
        ["id"],
    )

    # Add file storage columns
    op.add_column("edu_materials", sa.Column("file_url", sa.String(500), nullable=True))
    op.add_column("edu_materials", sa.Column("thumbnail_url", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("edu_materials", "thumbnail_url")
    op.drop_column("edu_materials", "file_url")
    op.drop_constraint("fk_edu_materials_blog_research", "edu_materials", type_="foreignkey")
    op.drop_column("edu_materials", "blog_research_id")
    op.alter_column("edu_materials", "grade_band", existing_type=sa.String(20), nullable=False)
    op.alter_column("edu_materials", "content", existing_type=sa.Text(), nullable=False)
    op.alter_column("edu_materials", "research_id", existing_type=sa.Integer(), nullable=False)
    op.add_column("edu_materials", sa.Column("suggestion_id", sa.Integer(), nullable=True))
    # Note: suggestion_id data is lost, unique constraint cannot be fully restored