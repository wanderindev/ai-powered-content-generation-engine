"""Pydantic schemas for the weekly-series agent-task API (epic #422)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The two goal-key shapes goal_key_for (app/services/weekly_plan.py) emits.
GOAL_KEY_PATTERN = r"^(weekly_series:\d{4}-W\d{2}|daily_series:\d{4}-\d{2}-\d{2})$"


class PlanWeekRequest(BaseModel):
    """Optional overrides for a plan-week call; an empty body is the norm."""

    # Force a specific research instead of picking the next APPROVED one. Also
    # the recovery path for a research that already has articles.
    research_id: int | None = None
    # Anchors both the goal_key (its ISO week) and the target dates. Defaults to
    # today (UTC).
    week_start: date | None = None
    # Expansion only (#573): the goal a PLAN_ARTICLES row belongs to, sent
    # verbatim so a cadence flip or a missing week_start cannot recompute a
    # different key. Wins over the key week_start would give; the goal must
    # already exist (404 otherwise), so it can never start a new goal or
    # bypass the in-flight cap. Shape-checked against the two cadences.
    goal_key: str | None = Field(default=None, pattern=GOAL_KEY_PATTERN)


class AgentTaskItem(BaseModel):
    id: int
    goal_key: str
    kind: str
    subject_type: str
    subject_id: int
    status: str
    depends_on: list[int] | None = None
    position: int
    payload: dict | None = None
    # Lease / outcome bookkeeping (#418). Harmlessly present on plan-week
    # responses too, where they are always the defaults.
    attempts: int = 0
    leased_by: str | None = None
    lease_expires_at: datetime | None = None
    error: str | None = None
    # WAITING bookkeeping (#553).
    waiting_for: str | None = None
    next_check_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PlanWeekResponse(BaseModel):
    # Both None exactly when at_capacity is true (nothing was planned).
    goal_key: str | None
    research_id: int | None
    # Number of rows this call inserted (0 once the graph is fully expanded for
    # the current DB state).
    created: int
    # The full goal graph, ordered by (position, id) — not just the new rows.
    tasks: list[AgentTaskItem]
    # #559: true = a new goal was due but weekly_plan_max_in_flight series are
    # unfinished, so nothing was planned (goal_key / research_id null, created
    # 0, tasks empty). A normal answer, not an error — the driver ends its
    # session. in_flight_goals names the goals holding the slots.
    at_capacity: bool = False
    max_in_flight: int
    in_flight_goals: list[str] = []
    detail: str | None = None


class CallSpec(BaseModel):
    """One HTTP call the driver should make to execute a task (#418).

    ``path`` is absolute (``/api/v1/...``) so the driver can use it verbatim.
    Multi-step kinds return several specs in order; where a later step needs data
    from an earlier response the path or body carries a ``{placeholder}`` and
    ``notes`` says exactly what to substitute.
    """

    method: str
    path: str
    query: dict | None = None
    body: dict | None = None
    notes: str | None = None


class NextTaskResponse(BaseModel):
    """The leased task plus everything needed to act on it without a round trip."""

    task: AgentTaskItem
    calls: list[CallSpec]


class ReportRequest(BaseModel):
    """A driver's outcome for one leased task."""

    status: Literal["DONE", "FAILED", "SKIPPED", "WAITING"]
    result: dict | None = None
    error: str | None = None
    # WAITING only (#553): what the step waits on, shown to the operator.
    waiting_for: str | None = Field(None, max_length=500)


class WaitCheckResponse(BaseModel):
    """A wait kind's condition, evaluated read-only (#553)."""

    kind: str
    subject_id: int
    satisfied: bool
    # None when satisfied; otherwise what the step is waiting on — the driver
    # passes it through verbatim in its WAITING report.
    waiting_for: str | None = None


class TaskActionRequest(BaseModel):
    """A human's intervention on a stuck task (#420): retry or skip.

    On a WAITING task, ``retry`` means "check again now" (#553).
    """

    action: Literal["retry", "skip"]
    # Why (#641): stored on the agent_task_actions row with the caller's
    # identity. Optional here; the MCP tools require it.
    reason: str | None = Field(default=None, max_length=1000)


class ReportResponse(BaseModel):
    task: AgentTaskItem
    # Ids promoted PENDING -> READY by this report; makes the scheduler's one
    # real decision observable in driver logs.
    promoted: list[int]


class TaskActionResponse(ReportResponse):
    """Result of a retry / skip (#420, #641)."""

    # True when the task was already in the action's target state (a replay):
    # nothing changed and no action row was written.
    replayed: bool = False
