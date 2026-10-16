"""agent_tasks: the WAIT_ARTICLE_CLAIMS_RESOLVED wait kind (#555).

Article grounding now maps ABSENT (a claim the research does not cover) to
NEEDS_DEEP_RESEARCH, so new plans hold each article's APPLY_ARTICLE_CORRECTIONS
behind one wait task until the article has no PENDING / NEEDS_DEEP_RESEARCH
claims. Goals planned before it keep their fact-check -> corrections rows.

Widens ``ck_agent_tasks_kind`` — a drop-and-recreate on the stable constraint
name (issue #24 convention), as 0036 did for the research gate.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0037_article_claims_gate"
down_revision = "0036_research_ready_gate"
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
)
_NEW_VALUES = (*_OLD_VALUES, "WAIT_ARTICLE_CLAIMS_RESOLVED")

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
    op.execute("DELETE FROM agent_tasks WHERE kind = 'WAIT_ARTICLE_CLAIMS_RESOLVED'")
    op.drop_constraint(_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_CONSTRAINT, "agent_tasks", _in_clause(_OLD_VALUES))
