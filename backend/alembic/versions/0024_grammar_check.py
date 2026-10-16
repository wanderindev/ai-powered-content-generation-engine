"""grammar_check: per-language proofread stamps on articles + a fix audit table.

Backs the grammar-check stage (issue #409). Two nullable timestamptz columns on
``articles`` record the last successful proofread per language — the stamps the
weekly-article orchestrator and the editorial calendar key off — and
``grammar_check_fixes`` holds one append-only row per FIND/REPLACE pair the
model returned, applied or skipped, so an editor can review what changed.

``language`` is a closed vocabulary — String + named CHECK (issue #24
convention) mirroring ``Language`` in ``app.models.enums``.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0024_grammar_check"
down_revision = "0023_instagram_posts"
branch_labels = None
depends_on = None

_LANGUAGE_VALUES = ("en", "es")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.add_column(
        "articles", sa.Column("grammar_checked_en_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "articles", sa.Column("grammar_checked_es_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "grammar_check_fixes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("find_text", sa.Text(), nullable=False),
        sa.Column("replace_text", sa.Text(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            _in_clause("language", _LANGUAGE_VALUES),
            name="ck_grammar_check_fixes_language",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grammar_check_fixes_article_id", "grammar_check_fixes", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_grammar_check_fixes_article_id", table_name="grammar_check_fixes")
    op.drop_table("grammar_check_fixes")
    op.drop_column("articles", "grammar_checked_es_at")
    op.drop_column("articles", "grammar_checked_en_at")
