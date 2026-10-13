"""fact_check_claims: atomic claims + verdicts for the fact-check pipeline.

Foundation of epic #373 (issue #374). One table serves all three content
stages via a polymorphic (subject_type, subject_id) reference — suggestion,
research document, or article. All four vocabularies are closed — String +
named CHECK (issue #24 convention), mirroring ``FactCheckSubject``,
``FactClaimType``, ``FactCheckVerdict`` and ``FactCheckMethod`` in
``app.models.enums``.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0019_fact_check_claims"
down_revision = "0018_edu_material_published_at"
branch_labels = None
depends_on = None

_SUBJECT_VALUES = ("SUGGESTION", "RESEARCH", "ARTICLE")
_CLAIM_TYPE_VALUES = ("EVENT", "PERSON", "DATE", "PLACE", "NUMBER", "OTHER")
_VERDICT_VALUES = (
    "PENDING",
    "VERIFIED",
    "INACCURATE",
    "UNVERIFIABLE",
    "DISPUTED",
    "NEEDS_DEEP_RESEARCH",
)
_METHOD_VALUES = ("GROUNDING", "WEB_SEARCH", "DEEP_RESEARCH", "KB_MATCH")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        "fact_check_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=20), nullable=False),
        sa.Column(
            "verdict", sa.String(length=30), server_default="PENDING", nullable=False
        ),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column("citation", sa.Text(), nullable=True),
        sa.Column("checked_via", sa.String(length=20), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            _in_clause("subject_type", _SUBJECT_VALUES),
            name="ck_fact_check_claims_subject_type",
        ),
        sa.CheckConstraint(
            _in_clause("claim_type", _CLAIM_TYPE_VALUES),
            name="ck_fact_check_claims_claim_type",
        ),
        sa.CheckConstraint(
            _in_clause("verdict", _VERDICT_VALUES),
            name="ck_fact_check_claims_verdict",
        ),
        sa.CheckConstraint(
            _in_clause("checked_via", _METHOD_VALUES),
            name="ck_fact_check_claims_checked_via",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fact_check_claims_subject",
        "fact_check_claims",
        ["subject_type", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_fact_check_claims_subject", table_name="fact_check_claims")
    op.drop_table("fact_check_claims")
