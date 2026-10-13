"""Add CORRECTED to the fact_check_claims verdict vocabulary (#387).

CORRECTED marks an INACCURATE claim whose correction was applied to the
content itself — a terminal green state. The stable constraint name (issue
#24 convention) makes this a clean drop-and-recreate.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0022_corrected_verdict"
down_revision = "0021_fact_check_job_type"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "PENDING",
    "VERIFIED",
    "INACCURATE",
    "UNVERIFIABLE",
    "DISPUTED",
    "NEEDS_DEEP_RESEARCH",
)
_NEW_VALUES = (*_OLD_VALUES, "CORRECTED")

_CONSTRAINT = "ck_fact_check_claims_verdict"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"verdict IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "fact_check_claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fact_check_claims", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # Rows already CORRECTED would violate the old constraint; revert them to
    # INACCURATE (the state they were corrected from) before narrowing.
    op.execute("UPDATE fact_check_claims SET verdict = 'INACCURATE' WHERE verdict = 'CORRECTED'")
    op.drop_constraint(_CONSTRAINT, "fact_check_claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fact_check_claims", _in_clause(_OLD_VALUES))
