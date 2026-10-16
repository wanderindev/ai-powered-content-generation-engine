"""agent_tasks: a WAITING status for steps blocked on the operator (#553).

Some pipeline steps can only finish after the operator does work on the laptop
(pic-extension deep research, Grammarly). Until now such a step FAILED three
times in one drive and parked. ``WAITING`` is the proper state: the row records
what it is waiting for and is not handed out again until ``next_check_at``.

Adds the two nullable bookkeeping columns and widens ``ck_agent_tasks_status``
— a drop-and-recreate on the stable constraint name (issue #24 convention), as
0026/0027 did for fact-check subjects.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0035_agent_task_waiting"
down_revision = "0034_edu_job_types"
branch_labels = None
depends_on = None

_OLD_VALUES = ("PENDING", "READY", "LEASED", "DONE", "FAILED", "SKIPPED")
_NEW_VALUES = (*_OLD_VALUES, "WAITING")

_CONSTRAINT = "ck_agent_tasks_status"


def _in_clause(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"status IN ({allowed})"


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("waiting_for", sa.Text(), nullable=True))
    op.add_column(
        "agent_tasks", sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint(_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_CONSTRAINT, "agent_tasks", _in_clause(_NEW_VALUES))


def downgrade() -> None:
    # A WAITING row would violate the narrowed constraint; READY is what it
    # becomes anyway once its recheck time passes.
    op.execute("UPDATE agent_tasks SET status = 'READY' WHERE status = 'WAITING'")
    op.drop_constraint(_CONSTRAINT, "agent_tasks", type_="check")
    op.create_check_constraint(_CONSTRAINT, "agent_tasks", _in_clause(_OLD_VALUES))
    op.drop_column("agent_tasks", "next_check_at")
    op.drop_column("agent_tasks", "waiting_for")
