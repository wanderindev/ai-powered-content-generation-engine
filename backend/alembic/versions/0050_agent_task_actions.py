"""agent_task_actions: who retried / skipped a task, and why (#641, epic #620).

Purely additive: one new table, two CHECKs, one index, no data writes. Rows
are written by ``agent_task_driver.apply_task_action`` for every retry / skip
that changed a task, from the admin queue and from the MCP
``retry_agent_task`` / ``skip_agent_task`` tools. A converged replay writes
no row.

* ``action`` is ``String(10)`` + named CHECK ``ck_agent_task_actions_action``
  over ``AgentTaskAction`` (``retry`` / ``skip``).
* ``previous_status`` is ``String(20)`` + named CHECK
  ``ck_agent_task_actions_previous_status`` over ``AgentTaskStatus``.
* ``reason`` is free text (required by the MCP tools, optional over HTTP);
  ``actor`` is the admin email, ``agent-driver`` or ``mcp:<token name>``.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0050_agent_task_actions"
down_revision = "0049_mcp_tool_calls"
branch_labels = None
depends_on = None

_TABLE = "agent_task_actions"
_ACTION_CONSTRAINT = "ck_agent_task_actions_action"
_STATUS_CONSTRAINT = "ck_agent_task_actions_previous_status"
_ACTIONS = ("retry", "skip")
_STATUSES = ("PENDING", "READY", "LEASED", "WAITING", "DONE", "FAILED", "SKIPPED")


def _in(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("agent_tasks.id"), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(254), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_in("action", _ACTIONS), name=_ACTION_CONSTRAINT),
        sa.CheckConstraint(_in("previous_status", _STATUSES), name=_STATUS_CONSTRAINT),
    )
    op.create_index("ix_agent_task_actions_task_id", _TABLE, ["task_id"])


def downgrade() -> None:
    op.drop_table(_TABLE)
