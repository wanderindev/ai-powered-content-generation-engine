"""agent_tasks: the WAIT_GRAMMAR_CHECKED_EN / _ES wait kinds (#556).

Grammar is done by the operator with the pic-extension (Grammarly), recorded as
``articles.grammar_checked_en_at`` / ``grammar_checked_es_at``. New plans replace
the LLM GRAMMAR_CHECK_EN / GRAMMAR_CHECK_ES steps with two wait tasks satisfied
by those stamps. Goals planned before it keep their GRAMMAR_CHECK_* rows.

Widens ``ck_agent_tasks_kind`` — a drop-and-recreate on the stable constraint
name (issue #24 convention), as 0036 and 0037 did.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0038_grammar_waits"
down_revision = "0037_article_claims_gate"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "RESEARCH_FACT_CHECK",
    "APPLY_RESEARCH_CORRECTIONS",
    "GENERATE_OUTLINE",
    "PLAN_ARTICLES",
    "WRITE_ARTICLE",
    "ARTICLE_FACT_CHECK",
    "APPLY_ARTICLE_CORRECTIONS",
    "GRAMMAR_CHECK_EN",
    "GENERATE_TAGS",
    "TRANSLATE",
    "GRAMMAR_CHECK_ES",
    "GENERATE_FEATURE_IMAGE",
    "GENERATE_SERIES_SECTIONS",
    "APPROVE_ARTICLE",
    "SET_PUBLISH_DATE",
    "GENERATE_SABIAS_QUE",
    "GENERATE_ARTICLE_PROMO",
    "APPROVE_IG_POST",
    "RENDER_IG_POST",
    "SCHEDULE_IG_POST",
    "WAIT_RESEARCH_READY",
    "WAIT_ARTICLE_CLAIMS_RESOLVED",
)
_NEW_VALUES = (*_OLD_VALUES, "WAIT_GRAMMAR_CHECKED_EN", "WAIT_GRAMMAR_CHECKED_ES")

_CONSTRAINT = "ck_agent_tasks_kind"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"kind IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_CONSTRAINT, "agent_tasks", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # A gate row would violate the narrowed constraint. Dropping it loses only
    # a wait marker; its dependents keep their (now dangling) depends_on ids.
    op.execute(
        "DELETE FROM agent_tasks WHERE kind IN ('WAIT_GRAMMAR_CHECKED_EN', 'WAIT_GRAMMAR_CHECKED_ES')"
    )
    op.drop_constraint(_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_CONSTRAINT, "agent_tasks", _in_clause(_OLD_VALUES))
