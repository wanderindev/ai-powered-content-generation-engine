"""verified_claims: knowledge base of resolved fact-check verdicts.

Part of epic #373 (issue #378). Claims verified externally (web search /
deep research) are upserted here, keyed by normalized claim text, so later
fact-check runs inherit the verdict (``checked_via=KB_MATCH``) instead of
re-verifying. Closed vocabularies are String + named CHECK (issue #24),
mirroring ``FactClaimType`` / ``FactCheckVerdict`` / ``FactCheckMethod`` in
``app.models.enums``.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0020_verified_claims"
down_revision = "0019_fact_check_claims"
branch_labels = None
depends_on = None

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
        "verified_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_key", sa.Text(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=20), nullable=False),
        sa.Column("verdict", sa.String(length=30), nullable=False),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column("citation", sa.Text(), nullable=True),
        sa.Column("source_method", sa.String(length=20), nullable=True),
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
            _in_clause("claim_type", _CLAIM_TYPE_VALUES),
            name="ck_verified_claims_claim_type",
        ),
        sa.CheckConstraint(
            _in_clause("verdict", _VERDICT_VALUES),
            name="ck_verified_claims_verdict",
        ),
        sa.CheckConstraint(
            _in_clause("source_method", _METHOD_VALUES),
            name="ck_verified_claims_source_method",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_key"),
    )


def downgrade() -> None:
    op.drop_table("verified_claims")
