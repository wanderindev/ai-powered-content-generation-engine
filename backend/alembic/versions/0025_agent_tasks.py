"""agent_tasks: one row per step of the goal-based weekly pipeline.

Foundation of the weekly-series epic (#422). ``goal_key`` groups a week's graph,
``depends_on`` (an int array of ``agent_tasks.id``) encodes the ordering, and the
lease columns let a dead driver's task return to the pool. The unique constraint
on ``(goal_key, kind, subject_type, subject_id)`` makes re-planning a week
idempotent rather than duplicating the graph.

``kind`` / ``status`` / ``subject_type`` are closed vocabularies — String + named
CHECK (issue #24 convention) mirroring ``AgentTaskKind`` / ``AgentTaskStatus`` /
``AgentTaskSubject`` in ``app.models.enums``. ``subject_type`` + ``subject_id``
are polymorphic (the ``fact_check_claims`` pattern), so no FK is declared; and
Postgres cannot foreign-key the ids inside ``depends_on`` either.

No standalone index on ``goal_key``: the unique constraint's b-tree leads on that
column and already serves goal-scoped scans.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0025_agent_tasks"
down_revision = "0024_grammar_check"
branch_labels = None
depends_on = None

_KIND_VALUES = (
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

_STATUS_VALUES = ("PENDING", "READY", "LEASED", "DONE", "FAILED", "SKIPPED")

_SUBJECT_TYPE_VALUES = ("RESEARCH", "ARTICLE", "INSTAGRAM_POST")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("goal_key", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("depends_on", sa.ARRAY(sa.Integer()), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_by", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in_clause("kind", _KIND_VALUES), name="ck_agent_tasks_kind"),
        sa.CheckConstraint(_in_clause("status", _STATUS_VALUES), name="ck_agent_tasks_status"),
        sa.CheckConstraint(
            _in_clause("subject_type", _SUBJECT_TYPE_VALUES),
            name="ck_agent_tasks_subject_type",
        ),
        sa.UniqueConstraint(
            "goal_key",
            "kind",
            "subject_type",
            "subject_id",
            name="uq_agent_tasks_goal_kind_subject",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("agent_tasks")
