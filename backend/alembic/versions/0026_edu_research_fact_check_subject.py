"""Add EDU_RESEARCH to the fact_check_claims subject vocabulary (#460).

Extends the blog fact-check pipeline (epic #373) to the educational-materials
pipeline: ``edu_research`` docs take the same external tier as blog research.
The stable constraint name (issue #24 convention) makes this a clean
drop-and-recreate — the column is ``String`` + named CHECK, not a PG enum.

Mirrors 0022_corrected_verdict.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0026_edu_research_fact_check_subject"
down_revision = "0025_agent_tasks"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "SUGGESTION",
    "RESEARCH",
    "ARTICLE",
)
_NEW_VALUES = (*_OLD_VALUES, "EDU_RESEARCH")

_CONSTRAINT = "ck_fact_check_claims_subject_type"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"subject_type IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "fact_check_claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fact_check_claims", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # EDU_RESEARCH rows would violate the narrowed constraint. They are claims
    # about edu content with no pre-#460 equivalent, so delete rather than
    # remap onto a blog subject (which would corrupt the blog KB).
    op.execute("DELETE FROM fact_check_claims WHERE subject_type = 'EDU_RESEARCH'")
    op.drop_constraint(_CONSTRAINT, "fact_check_claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fact_check_claims", _in_clause(_OLD_VALUES))
