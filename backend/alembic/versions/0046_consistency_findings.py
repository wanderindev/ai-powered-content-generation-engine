"""consistency_findings: cross-article contradictions, shadow mode (#631, epic #618).

Purely additive: one new table, two FK indexes, three CHECKs, no data writes.
Nothing writes it while ``CROSS_ARTICLE_CONSISTENCY_ENABLED`` is off, and no
existing query reads it except the admin articles grid's separately guarded
count (which degrades to 0 before this migration is applied).

* ``article_id`` / ``other_article_id`` -> ``articles`` ``ON DELETE CASCADE``.
* ``claim_id`` -> ``fact_check_claims`` ``ON DELETE SET NULL``: a fact-check
  re-run re-inserts the claims with new ids, and CASCADE would wipe resolved
  findings with them. ``claim_text`` is the durable copy.
* ``other_source_type`` / ``verdict`` / ``resolution`` are String + named CHECK
  (#24) over ``ConsistencySource`` / ``ConsistencyVerdict`` /
  ``ConsistencyResolution``.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0046_consistency_findings"
down_revision = "0045_suggestion_dedup"
branch_labels = None
depends_on = None

_TABLE = "consistency_findings"
_VERDICTS = ("CONSISTENT", "CONTRADICTS", "UNRELATED")
_SOURCES = ("ARTICLE_CLAIM", "VERIFIED_CLAIM")
_RESOLUTIONS = ("DISMISSED", "RESOLVED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("fact_check_claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("other_source_type", sa.String(20), nullable=False),
        sa.Column("other_source_id", sa.Integer(), nullable=False),
        sa.Column(
            "other_article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("other_text", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_in("verdict", _VERDICTS), name="ck_consistency_findings_verdict"),
        sa.CheckConstraint(
            _in("other_source_type", _SOURCES), name="ck_consistency_findings_other_source_type"
        ),
        sa.CheckConstraint(
            _in("resolution", _RESOLUTIONS), name="ck_consistency_findings_resolution"
        ),
    )
    op.create_index("ix_consistency_findings_article_id", _TABLE, ["article_id"])
    op.create_index("ix_consistency_findings_other_article_id", _TABLE, ["other_article_id"])


def downgrade() -> None:
    op.drop_table(_TABLE)
