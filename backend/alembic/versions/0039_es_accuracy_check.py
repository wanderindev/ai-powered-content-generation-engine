"""ES_ACCURACY_CHECK agent-task kind + ES_ACCURACY grammar-fix source (#557).

After TRANSLATE, a Sonnet pass compares the Spanish against the (already
fact-checked) English and fixes meaning differences DeepL introduced. New plans
put an ``ES_ACCURACY_CHECK`` task between TRANSLATE and
WAIT_GRAMMAR_CHECKED_ES; its FIND/REPLACE pairs are audited in
``grammar_check_fixes`` with ``source = 'ES_ACCURACY'``.

Widens two CHECK constraints, each a drop-and-recreate on its stable name
(issue #24 convention, as 0036-0038 did). No table, column or row changes.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0039_es_accuracy_check"
down_revision = "0038_grammar_waits"
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
    "WAIT_GRAMMAR_CHECKED_EN",
    "WAIT_GRAMMAR_CHECKED_ES",
)
_NEW_VALUES = (*_OLD_VALUES, "ES_ACCURACY_CHECK")

_OLD_SOURCES = ("LLM", "GRAMMARLY")
_NEW_SOURCES = (*_OLD_SOURCES, "ES_ACCURACY")

_KIND_CONSTRAINT = "ck_agent_tasks_kind"
_SOURCE_CONSTRAINT = "ck_grammar_check_fixes_source"


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint(_KIND_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_KIND_CONSTRAINT, "agent_tasks", _in_clause("kind", _NEW_VALUES))
    op.drop_constraint(_SOURCE_CONSTRAINT, "grammar_check_fixes", type_="check")
    op.create_check_constraint(
        _SOURCE_CONSTRAINT, "grammar_check_fixes", _in_clause("source", _NEW_SOURCES)
    )


def downgrade() -> None:
    # Rows carrying the new values would violate the narrowed constraints.
    # Dropping an ES_ACCURACY_CHECK task loses one pipeline step marker (its
    # dependents keep a dangling depends_on id); dropping the audit rows loses
    # the record of what the check changed — the Spanish edits themselves stay.
    op.execute("DELETE FROM agent_tasks WHERE kind = 'ES_ACCURACY_CHECK'")
    op.execute("DELETE FROM grammar_check_fixes WHERE source = 'ES_ACCURACY'")
    op.drop_constraint(_SOURCE_CONSTRAINT, "grammar_check_fixes", type_="check")
    op.create_check_constraint(
        _SOURCE_CONSTRAINT, "grammar_check_fixes", _in_clause("source", _OLD_SOURCES)
    )
    op.drop_constraint(_KIND_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_KIND_CONSTRAINT, "agent_tasks", _in_clause("kind", _OLD_VALUES))
