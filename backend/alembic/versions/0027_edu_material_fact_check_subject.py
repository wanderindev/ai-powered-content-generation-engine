"""Add EDU_MATERIAL to the fact_check_claims subject vocabulary (#461).

Study guides are grounded against their ``edu_research`` the way blog articles
are grounded against their ``research``. One value covers every material_type —
the subject is the ``EduMaterial`` row.

Mirrors 0026_edu_research_fact_check_subject.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0027_edu_material_fact_check_subject"
down_revision = "0026_edu_research_fact_check_subject"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "SUGGESTION",
    "RESEARCH",
    "ARTICLE",
    "EDU_RESEARCH",
)
_NEW_VALUES = (*_OLD_VALUES, "EDU_MATERIAL")

_CONSTRAINT = "ck_fact_check_claims_subject_type"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"subject_type IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "fact_check_claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fact_check_claims", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # EDU_MATERIAL rows would violate the narrowed constraint. They are claims
    # about generated guide content with no pre-#461 equivalent, so delete
    # rather than remap onto another subject (which would corrupt that subject's
    # claim set and the verified KB fed from it).
    op.execute("DELETE FROM fact_check_claims WHERE subject_type = 'EDU_MATERIAL'")
    op.drop_constraint(_CONSTRAINT, "fact_check_claims", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fact_check_claims", _in_clause(_OLD_VALUES))
