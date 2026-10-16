"""agent_tasks: the WAIT_RESEARCH_READY wait kind (#554).

The operator fact-checks research and applies its corrections before approving
it, so new plans replace the RESEARCH_FACT_CHECK / APPLY_RESEARCH_CORRECTIONS
steps with one wait task that holds the outline until the research has no
PENDING / NEEDS_DEEP_RESEARCH claims. The two old kinds stay in the vocabulary
for goals planned before this change.

Widens ``ck_agent_tasks_kind`` — a drop-and-recreate on the stable constraint
name (issue #24 convention), as 0035 did for ``ck_agent_tasks_status``.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0036_research_ready_gate"
down_revision = "0035_agent_task_waiting"
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
)
_NEW_VALUES = (*_OLD_VALUES, "WAIT_RESEARCH_READY")

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
    op.execute("DELETE FROM agent_tasks WHERE kind = 'WAIT_RESEARCH_READY'")
    op.drop_constraint(_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_CONSTRAINT, "agent_tasks", _in_clause(_OLD_VALUES))
