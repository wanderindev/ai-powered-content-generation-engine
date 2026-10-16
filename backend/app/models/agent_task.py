"""Agent tasks — one row per step of the goal-based weekly pipeline.

Foundation of the weekly-series epic (#422). A week's work is planned once as a
graph of rows (``goal_key`` groups them, e.g. ``weekly_series:2026-W31``) and an
external, stateless driver claims one ``READY`` task at a time, runs it, and
reports back. Modelling a step as a row rather than a line in a script is what
makes the pipeline resumable, inspectable mid-flight, and able to record a
partial failure somewhere durable.

``depends_on`` holds the ids of the tasks that must be ``DONE`` before this one
becomes ``READY``; the promotion logic itself is application code (#417/#418),
not a database rule — Postgres cannot foreign-key array elements, and nothing
here validates that a referenced id exists.

The subject reference is polymorphic (``subject_type`` + ``subject_id``,
mirroring ``fact_check_claims``) so one table can drive research, article and
Instagram-post steps; no FK is declared because the target table varies per row.
The unique constraint on ``(goal_key, kind, subject_type, subject_id)`` is what
makes re-planning a week idempotent instead of duplicating the graph.
"""

from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import (
    AgentTaskAction,
    AgentTaskKind,
    AgentTaskStatus,
    AgentTaskSubject,
    status_check,
)

__all__ = [
    "AgentTask",
    "AgentTaskAction",
    "AgentTaskActionLog",
    "AgentTaskKind",
    "AgentTaskStatus",
    "AgentTaskSubject",
]


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Groups one week's graph, e.g. "weekly_series:2026-W31". No standalone
    # index: the unique constraint below leads on goal_key, so its b-tree
    # already serves goal-scoped lookups.
    goal_key: Mapped[str] = mapped_column(String(100))

    # Closed vocabularies — String + named CHECK (issue #24 convention).
    kind: Mapped[str] = mapped_column(String(30))

    # Polymorphic subject. NOT NULL: every task acts on a concrete entity, and
    # per-article / per-post tasks are only planned once their rows exist (the
    # #417 fan-out point), which keeps the unique constraint above airtight.
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[int] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(20),
        default=AgentTaskStatus.PENDING.value,
        server_default=AgentTaskStatus.PENDING.value,
    )

    # ids of the agent_tasks that must be DONE first. NULL and [] both mean
    # "no dependencies". No FK — Postgres cannot reference array elements.
    depends_on: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))

    # Tiebreaker for ordering tasks within a goal.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Step-specific input the driver needs (target publish date, which draft to
    # use, ...) and whatever the driver reported back.
    payload: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)

    # Lease bookkeeping so a dead driver's task returns to the pool.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_by: Mapped[str | None] = mapped_column(String(100))

    error: Mapped[str | None] = mapped_column(Text)

    # WAITING bookkeeping (#553): what the step is waiting on (the driver's
    # ``waiting_for`` text) and when ``next`` may hand the row out again.
    waiting_for: Mapped[str | None] = mapped_column(Text)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        status_check("agent_tasks", "kind", AgentTaskKind),
        status_check("agent_tasks", "status", AgentTaskStatus),
        status_check("agent_tasks", "subject_type", AgentTaskSubject),
        UniqueConstraint(
            "goal_key",
            "kind",
            "subject_type",
            "subject_id",
            name="uq_agent_tasks_goal_kind_subject",
        ),
    )


class AgentTaskActionLog(Base):
    """One applied retry / skip on an agent task, with who and why (#641).

    Append-only. ``apply_task_action`` writes a row for every action that
    changed the task (a converged replay writes none), from both the admin
    queue (``POST .../agent-tasks/{id}/action``, ``actor`` = the admin email or
    ``agent-driver``) and the MCP ``retry_agent_task`` / ``skip_agent_task``
    tools (``actor`` = ``mcp:<token name>``). ``reason`` is optional on the
    HTTP route and required by the MCP tools. ``previous_status`` is the
    task's status just before the action.
    """

    __tablename__ = "agent_task_actions"
    __table_args__ = (
        status_check("agent_task_actions", "action", AgentTaskAction),
        status_check("agent_task_actions", "previous_status", AgentTaskStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    action: Mapped[str] = mapped_column(String(10))
    previous_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
