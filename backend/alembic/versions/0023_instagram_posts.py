"""instagram_posts: drafts, rendered artifacts and publication state.

Foundation of the Instagram social pipeline. One table for both post kinds
(``SABIAS_QUE`` fact cards generated from a research document, and
``ARTICLE_PROMO`` series announcements). ``post_type``/``template_variant`` are
closed vocabularies — String + named CHECK (issue #24 convention) mirroring
``InstagramPostType``/``InstagramTemplateVariant`` in ``app.models.enums``;
``status`` reuses the pre-existing ``content_status_type`` PG enum.

Publication is timestamp-driven (mirrors ``articles.published_at`` but active:
a scheduler publishes due rows): ``scheduled_at`` (indexed — the scheduler's
due-row scan), ``publishing_started_at`` (transient claim), ``published_at`` +
``ig_media_id`` (live), ``publish_error`` (parked failure, never auto-retried).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0023_instagram_posts"
down_revision = "0022_corrected_verdict"
branch_labels = None
depends_on = None

_POST_TYPE_VALUES = ("SABIAS_QUE", "ARTICLE_PROMO")
_TEMPLATE_VARIANT_VALUES = ("AZUL", "CLARO")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        "instagram_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_type", sa.String(length=20), nullable=False),
        sa.Column("template_variant", sa.String(length=10), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "APPROVED", "REJECTED", name="content_status_type", create_type=False
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("research_id", sa.Integer(), nullable=True),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("fact", sa.Text(), nullable=True),
        sa.Column("polaroid_caption", sa.String(length=120), nullable=True),
        sa.Column("kicker", sa.String(length=120), nullable=True),
        sa.Column("meta_text", sa.String(length=200), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("image_query", sa.String(length=255), nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("media_path", sa.String(length=500), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publishing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_error", sa.Text(), nullable=True),
        sa.Column("publish_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ig_media_id", sa.String(length=64), nullable=True),
        sa.Column("ig_permalink", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            _in_clause("post_type", _POST_TYPE_VALUES),
            name="ck_instagram_posts_post_type",
        ),
        sa.CheckConstraint(
            _in_clause("template_variant", _TEMPLATE_VARIANT_VALUES),
            name="ck_instagram_posts_template_variant",
        ),
        sa.ForeignKeyConstraint(["research_id"], ["research.id"]),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_instagram_posts_research_id", "instagram_posts", ["research_id"])
    op.create_index("ix_instagram_posts_article_id", "instagram_posts", ["article_id"])
    op.create_index("ix_instagram_posts_media_id", "instagram_posts", ["media_id"])
    op.create_index("ix_instagram_posts_scheduled_at", "instagram_posts", ["scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_instagram_posts_scheduled_at", table_name="instagram_posts")
    op.drop_index("ix_instagram_posts_media_id", table_name="instagram_posts")
    op.drop_index("ix_instagram_posts_article_id", table_name="instagram_posts")
    op.drop_index("ix_instagram_posts_research_id", table_name="instagram_posts")
    op.drop_table("instagram_posts")
