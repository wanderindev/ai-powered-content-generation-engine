"""Add grammar_check_fixes.source — LLM vs Grammarly provenance.

The Grammarly web-editor pass (driven by the PIC Companion extension through
``POST /admin/dashboard/articles/{id}/grammar-apply``) records the suggestions
it accepted in the same audit table as the Sonnet corrections pass (#409). The
``source`` column keeps the two distinguishable. Existing rows are all LLM.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0032_grammar_fix_source"
down_revision = "0031_article_audio_manifest_es"
branch_labels = None
depends_on = None

_SOURCES = ("LLM", "GRAMMARLY")


def upgrade() -> None:
    op.add_column(
        "grammar_check_fixes",
        sa.Column("source", sa.String(20), nullable=False, server_default="LLM"),
    )
    allowed = ", ".join(f"'{value}'" for value in _SOURCES)
    op.create_check_constraint(
        "ck_grammar_check_fixes_source", "grammar_check_fixes", f"source IN ({allowed})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_grammar_check_fixes_source", "grammar_check_fixes", type_="check")
    op.drop_column("grammar_check_fixes", "source")
