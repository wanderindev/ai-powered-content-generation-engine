"""Drop old media_candidates/media_suggestions tables, create new media_candidates.

Revision ID: 20260225_media_candidates
Revises: 20260223_outline_status
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "20260225_media_candidates"
down_revision = "20260223_outline_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old tables (order matters due to FK: candidates -> suggestions)
    op.drop_table("media_candidates")
    op.drop_table("media_suggestions")

    # Create new media_candidates table
    op.create_table(
        "media_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Source identification
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("external_url", sa.String(1024), nullable=False),
        sa.Column("thumbnail_url", sa.String(1024), nullable=True),
        sa.Column("page_url", sa.String(1024), nullable=True),
        # Metadata
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("author", sa.String(500), nullable=True),
        sa.Column("tags", ARRAY(sa.String(255)), nullable=True),
        # Licensing
        sa.Column("license", sa.String(100), nullable=True),
        sa.Column("license_url", sa.String(512), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        # Technical
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(50), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        # Relevance scoring
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("relevance_notes", sa.Text(), nullable=True),
        # Review status
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        # Provenance
        sa.Column("source_categories", ARRAY(sa.String(255)), nullable=True),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=True),
        # Link to approved media
        sa.Column("media_id", sa.Integer(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Constraints
        sa.UniqueConstraint("source", "external_id", name="uq_candidate_source_external_id"),
    )

    # Indexes
    op.create_index("ix_candidate_status", "media_candidates", ["status"])
    op.create_index("ix_candidate_source", "media_candidates", ["source"])
    op.create_index("ix_candidate_relevance", "media_candidates", ["relevance_score"])


def downgrade() -> None:
    op.drop_table("media_candidates")
    # Note: old media_candidates and media_suggestions tables are not restored.
    # They were empty and from a previous project.