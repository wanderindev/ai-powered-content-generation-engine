"""Add grammar_review_diffs — paragraphs flagged by the meaning check (#518).

``grammar-apply`` now has Sonnet review every changed paragraph before the
corrected body is written. Changes it could not vouch for are still written
but recorded here (before/after + reason) for a human to look at.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0033_grammar_review_diffs"
down_revision = "0032_grammar_fix_source"
branch_labels = None
depends_on = None

_LANGUAGES = ("en", "es")


def upgrade() -> None:
    op.create_table(
        "grammar_review_diffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("before_text", sa.Text(), nullable=False),
        sa.Column("after_text", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_grammar_review_diffs_article_id", "grammar_review_diffs", ["article_id"]
    )
    allowed = ", ".join(f"'{value}'" for value in _LANGUAGES)
    op.create_check_constraint(
        "ck_grammar_review_diffs_language", "grammar_review_diffs", f"language IN ({allowed})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_grammar_review_diffs_language", "grammar_review_diffs", type_="check")
    op.drop_index("ix_grammar_review_diffs_article_id", table_name="grammar_review_diffs")
    op.drop_table("grammar_review_diffs")
