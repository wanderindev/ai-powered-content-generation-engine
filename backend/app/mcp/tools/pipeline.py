"""MCP read tools over the weekly-series pipeline (#640): ``pipeline_status``
and ``explain_stuck_goal``.

Both read ``agent_tasks`` only (plus the capacity settings): no wait condition
is re-evaluated live, no other table is read, nothing is written. A goal is
**in flight** exactly when ``weekly_plan.unfinished_goals`` says so (any row
not ``DONE`` / ``SKIPPED``; ``FAILED`` holds the slot), the same rule the
planner's capacity check uses.

``explain_stuck_goal`` only *suggests* ``retry`` / ``skip``; the actions are
the admin queue's ``POST /api/v1/admin/dashboard/agent-tasks/{id}/action``
(the MCP ``retry_agent_task`` / ``skip_agent_task`` tools, #641, call the same
function). The suggestion rules mirror
``agent_task_driver.apply_task_action``: ``retry`` is allowed on FAILED and
WAITING rows (on WAITING it means "check now"), ``skip`` on PENDING / READY /
FAILED / WAITING, never on LEASED.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.core import database
from app.core.config import get_settings
from app.mcp.tools.bounds import bound_result, clip
from app.mcp.tools.writes import MCP_REFUSED_TASK_ACTIONS
from app.models.agent_task import AgentTask
from app.models.enums import AgentTaskKind, AgentTaskStatus
from app.services.weekly_plan import unfinished_goals

ERROR_CHARS = 500
MAX_GOALS = 10
# pipeline_status lists at most this many rows per status per goal (by_status
# has the full counts; explain_stuck_goal lists every blocker of one goal).
ROWS_PER_STATUS = 5
STATUS_ERROR_CHARS = 300
ACTION_ENDPOINT = "POST /api/v1/admin/dashboard/agent-tasks/{task_id}/action"

_S = AgentTaskStatus
_SATISFIED = {_S.DONE.value, _S.SKIPPED.value}
_GOAL_KEY = re.compile(r"^[a-z_]+:[0-9A-Za-z-]+$")

# Steps whose loss does not hurt the article itself: skipping them is the
# normal way past a failure (the planner skips them too when their flag is off).
_OPTIONAL_KINDS = frozenset(
    {
        AgentTaskKind.GENERATE_FEATURE_IMAGE.value,
        AgentTaskKind.GENERATE_SABIAS_QUE.value,
        AgentTaskKind.GENERATE_ARTICLE_PROMO.value,
        AgentTaskKind.APPROVE_IG_POST.value,
        AgentTaskKind.RENDER_IG_POST.value,
        AgentTaskKind.SCHEDULE_IG_POST.value,
    }
)
# Error text that usually means "try again" rather than "fix something first".
_TRANSIENT = re.compile(
    r"timeout|timed out|\b50[234]\b|\b429\b|rate.?limit|overloaded|temporar|"
    r"connection (reset|refused|aborted)|queuepool|try again|unavailable",
    re.IGNORECASE,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _check_goal_key(goal_key: str) -> None:
    if not _GOAL_KEY.match(goal_key):
        raise ToolError(
            "invalid_goal_key: expected a key like 'daily_series:2026-10-11' or "
            "'weekly_series:2026-W41'."
        )


def _load_goals(db, goal_keys: list[str]) -> dict[str, list[AgentTask]]:
    rows = (
        db.query(AgentTask)
        .filter(AgentTask.goal_key.in_(goal_keys))
        .order_by(AgentTask.goal_key, AgentTask.position, AgentTask.id)
        .all()
    )
    goals: dict[str, list[AgentTask]] = {key: [] for key in goal_keys}
    for row in rows:
        goals[row.goal_key].append(row)
    return goals


def _capacity(in_flight: list[str]) -> dict[str, Any]:
    settings = get_settings()
    cap = settings.weekly_plan_max_in_flight
    return {
        "max_in_flight": cap,
        "cap_enabled": cap > 0,
        "in_flight": len(in_flight),
        "in_flight_goals": in_flight,
        "at_capacity": cap > 0 and len(in_flight) >= cap,
        "daily_cadence": settings.weekly_plan_daily_cadence,
        "max_attempts": settings.agent_task_max_attempts,
    }


def _task_ref(task: AgentTask) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "kind": task.kind,
        "subject_type": task.subject_type,
        "subject_id": task.subject_id,
    }


def _blocked_dependents(task_id: int, tasks: list[AgentTask]) -> list[int]:
    """Ids of not-yet-satisfied rows that (transitively) depend on ``task_id``."""
    children: dict[int, list[AgentTask]] = {}
    for task in tasks:
        for dep in task.depends_on or ():
            children.setdefault(dep, []).append(task)
    seen: set[int] = set()
    stack = [task_id]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child.id not in seen and child.status not in _SATISFIED:
                seen.add(child.id)
                stack.append(child.id)
    return sorted(seen)


# --- pipeline_status ------------------------------------------------------------

PIPELINE_STATUS_DESCRIPTION = """\
Snapshot of Panama In Context's content pipeline (the agent_tasks graph a deterministic \
driver works through, one goal per series: "daily_series:YYYY-MM-DD" or \
"weekly_series:YYYY-Www").

Use it to see what is in flight, what is waiting on the operator, what failed, and \
whether the planner has capacity for a new series. With goal_key, it reports that one \
goal (finished or not); without, every in-flight goal (any row not DONE/SKIPPED; a \
FAILED row keeps its goal in flight and holds a capacity slot). Do not use it to decide \
what to do about a stuck goal (use explain_stuck_goal) or to read an article (get_article).

Arguments: goal_key (optional).

Returns {"capacity": {"max_in_flight", "cap_enabled", "in_flight", "in_flight_goals", \
"at_capacity", "daily_cadence", "max_attempts"}, "goals": [{"goal_key", "finished", \
"task_count", "by_status": {status: n}, "waiting": [{"task_id", "kind", "subject_type", \
"subject_id", "waiting_for", "next_check_at"}], "failed": [{... , "attempts", "error"}], \
"leased": [{..., "leased_by", "lease_expires_at"}], "ready": [{...}], "last_activity_at"}], \
"goals_omitted", "truncated"}. At most 10 goals, oldest key first, and at most 5 rows per \
list per goal ("by_status" has the full counts; explain_stuck_goal lists them all).

Example: pipeline_status() -> {"capacity": {"max_in_flight": 2, "in_flight": 2, \
"at_capacity": true, ...}, "goals": [{"goal_key": "daily_series:2026-10-09", \
"by_status": {"DONE": 14, "WAITING": 1, "PENDING": 9}, "waiting": [{"task_id": 812, \
"kind": "WAIT_GRAMMAR_CHECKED_EN", "waiting_for": "Grammarly EN pass on article 251", ...}], \
"failed": [], ...}], "goals_omitted": 0, "truncated": false}"""


def _goal_status(goal_key: str, tasks: list[AgentTask]) -> dict[str, Any]:
    by_status = Counter(task.status for task in tasks)
    last = max((task.updated_at for task in tasks if task.updated_at), default=None)

    def rows(status: AgentTaskStatus) -> list[AgentTask]:
        return [task for task in tasks if task.status == status.value][:ROWS_PER_STATUS]

    return {
        "goal_key": goal_key,
        "finished": all(task.status in _SATISFIED for task in tasks),
        "task_count": len(tasks),
        "by_status": dict(sorted(by_status.items())),
        "waiting": [
            {
                **_task_ref(task),
                "waiting_for": clip(task.waiting_for, STATUS_ERROR_CHARS),
                "next_check_at": _iso(task.next_check_at),
            }
            for task in rows(_S.WAITING)
        ],
        "failed": [
            {
                **_task_ref(task),
                "attempts": task.attempts,
                "error": clip(task.error, STATUS_ERROR_CHARS),
            }
            for task in rows(_S.FAILED)
        ],
        "leased": [
            {
                **_task_ref(task),
                "leased_by": task.leased_by,
                "lease_expires_at": _iso(task.lease_expires_at),
            }
            for task in rows(_S.LEASED)
        ],
        "ready": [_task_ref(task) for task in rows(_S.READY)],
        "last_activity_at": _iso(last),
    }


def pipeline_status(
    goal_key: Annotated[str | None, Field(max_length=100)] = None,
) -> dict[str, Any]:
    if goal_key is not None:
        _check_goal_key(goal_key)
    db = database.SessionLocal()
    try:
        in_flight = unfinished_goals(db)
        keys = [goal_key] if goal_key is not None else in_flight
        goals = _load_goals(db, keys[:MAX_GOALS])
    finally:
        db.close()
    if goal_key is not None and not goals[goal_key]:
        raise ToolError("not_found: no agent_tasks rows carry that goal_key.")
    return bound_result(
        {
            "capacity": _capacity(in_flight),
            "goals": [_goal_status(key, tasks) for key, tasks in goals.items()],
            "goals_omitted": max(0, len(keys) - MAX_GOALS),
        }
    )


# --- explain_stuck_goal --------------------------------------------------------

EXPLAIN_STUCK_GOAL_DESCRIPTION = """\
Diagnose why one Panama In Context pipeline goal is not finishing, and suggest the \
operator action for each blocker. Built from the goal's agent_tasks rows only; it \
changes nothing and only SUGGESTS retry / skip (the operator, or a pipeline-write tool, \
acts).

Use it when pipeline_status shows a goal with failed rows, long waits, or no progress. \
Do not use it for an overview of all goals (use pipeline_status).

Arguments: goal_key (e.g. "daily_series:2026-10-09").

Returns {"goal_key", "verdict": "finished" | "blocked_on_failure" | \
"waiting_on_operator" | "in_progress" | "stalled", "summary", "holds_capacity_slot", \
"by_status", "blockers": [{"task_id", "kind", "subject_type", "subject_id", "status", \
"reason", "attempts", "max_attempts", "error" | "waiting_for", "next_check_at"?, \
"blocked_dependents": [task ids], "suggested_action": "retry" | "skip" | "wait" | \
"investigate", "allowed_actions": [str], "rationale"}], "action_endpoint", "truncated"}. \
allowed_actions mirrors the admin queue; the MCP retry/skip tools refuse to skip a \
WAIT_* gate or a fact-check / corrections / grammar step (ARTICLE_FACT_CHECK, \
APPLY_ARTICLE_CORRECTIONS and their legacy research / LLM-grammar kinds), or to \
retry/skip APPROVE_ARTICLE, SET_PUBLISH_DATE, APPROVE_IG_POST or \
SCHEDULE_IG_POST, and the rationale then says the operator must act. \
Blockers are FAILED rows (they hold the capacity slot until retried or skipped), \
WAITING rows (operator work on the laptop; retry = "check now" once done), LEASED rows \
(expired leases are swept by the driver's next claim) and PENDING rows whose \
dependencies are all satisfied but which were never promoted.

Example: explain_stuck_goal(goal_key="daily_series:2026-10-02") -> {"verdict": \
"blocked_on_failure", "summary": "1 failed task blocks 7 others ...", "blockers": \
[{"task_id": 640, "kind": "GENERATE_FEATURE_IMAGE", "status": "FAILED", "attempts": 3, \
"error": "Gemini 429 ...", "blocked_dependents": [641, 642], "suggested_action": "retry", \
"allowed_actions": ["retry", "skip"], "rationale": "The error looks transient ..."}], ...}"""


def _failed_advice(task: AgentTask) -> tuple[str, str]:
    error = task.error or ""
    if _TRANSIENT.search(error):
        return "retry", (
            "The error looks transient (timeout, rate limit, 5xx or pool exhaustion); "
            "retry resets attempts and returns the task to the driver."
        )
    if task.kind in _OPTIONAL_KINDS:
        return "skip", (
            "Optional step (feature image / Instagram): skipping it marks it SKIPPED, "
            "which satisfies its dependents and frees the goal."
        )
    return "investigate", (
        "Not obviously transient: read the error and fix the cause, then retry; "
        "skip only if the step's work was done by hand, since SKIPPED satisfies "
        "dependents exactly like DONE."
    )


def _with_mcp_note(kind: str, rationale: str) -> str:
    """Append which actions the MCP retry/skip tools refuse on ``kind`` (#686).

    ``allowed_actions`` keeps mirroring ``apply_task_action`` (the admin
    queue), so the note is what tells an agent to hand the step to the operator.
    """
    refused = MCP_REFUSED_TASK_ACTIONS.get(kind)
    if not refused:
        return rationale
    verbs = " / ".join(sorted(action.value for action in refused))
    return (
        f"{rationale} Only the operator can {verbs} a {kind} task, from the admin Agent "
        f"Tasks queue: the MCP {verbs} tool refuses it."
    )


def _verdict(tasks: list[AgentTask]) -> str:
    statuses = {task.status for task in tasks}
    if statuses <= _SATISFIED:
        return "finished"
    if _S.FAILED.value in statuses:
        return "blocked_on_failure"
    if statuses & {_S.READY.value, _S.LEASED.value}:
        return "in_progress"
    if _S.WAITING.value in statuses:
        return "waiting_on_operator"
    return "stalled"


def _summary(verdict: str, tasks: list[AgentTask], blockers: list[dict[str, Any]]) -> str:
    counts = Counter(task.status for task in tasks)
    done = counts[_S.DONE.value] + counts[_S.SKIPPED.value]
    head = f"{done}/{len(tasks)} tasks done or skipped."
    if verdict == "finished":
        return head + " The goal is finished and holds no capacity slot."
    failed = [b for b in blockers if b["status"] == _S.FAILED.value]
    waiting = [b for b in blockers if b["status"] == _S.WAITING.value]
    parts = [head]
    if failed:
        blocked = {d for b in failed for d in b["blocked_dependents"]}
        parts.append(
            f"{len(failed)} failed task(s) block {len(blocked)} other(s) and keep the goal "
            "holding a capacity slot until retried or skipped."
        )
    if waiting:
        parts.append(f"{len(waiting)} task(s) wait on operator work.")
    if verdict == "in_progress":
        parts.append("The driver has READY or LEASED work; the goal is moving.")
    if verdict == "stalled":
        parts.append(
            "Nothing is READY, LEASED, WAITING or FAILED, yet rows are PENDING: "
            "a dependency was never promoted (see blockers)."
        )
    return " ".join(parts)


def explain_stuck_goal(
    goal_key: Annotated[str, Field(min_length=1, max_length=100)],
) -> dict[str, Any]:
    _check_goal_key(goal_key)
    max_attempts = get_settings().agent_task_max_attempts
    db = database.SessionLocal()
    try:
        tasks = _load_goals(db, [goal_key])[goal_key]
    finally:
        db.close()
    if not tasks:
        raise ToolError("not_found: no agent_tasks rows carry that goal_key.")

    now = datetime.now(UTC)
    by_id = {task.id: task for task in tasks}
    blockers: list[dict[str, Any]] = []
    for task in tasks:
        base = {
            **_task_ref(task),
            "status": task.status,
            "attempts": task.attempts,
            "max_attempts": max_attempts,
        }
        if task.status == _S.FAILED.value:
            action, rationale = _failed_advice(task)
            blockers.append(
                {
                    **base,
                    "reason": "failed",
                    "error": clip(task.error, ERROR_CHARS),
                    "blocked_dependents": _blocked_dependents(task.id, tasks),
                    "suggested_action": action,
                    "allowed_actions": ["retry", "skip"],
                    "rationale": _with_mcp_note(task.kind, rationale),
                }
            )
        elif task.status == _S.WAITING.value:
            due = task.next_check_at is None or task.next_check_at <= now
            blockers.append(
                {
                    **base,
                    "reason": "waiting_on_operator",
                    "waiting_for": clip(task.waiting_for, ERROR_CHARS),
                    "next_check_at": _iso(task.next_check_at),
                    "blocked_dependents": _blocked_dependents(task.id, tasks),
                    "suggested_action": "wait",
                    "allowed_actions": ["retry", "skip"],
                    "rationale": _with_mcp_note(
                        task.kind,
                        "Blocked on operator work, not on a fault. "
                        + (
                            "The recheck is due; the next driver run re-checks it. "
                            if due
                            else "The driver re-checks it at next_check_at. "
                        )
                        + "Once the operator has done the work, retry means 'check now'.",
                    ),
                }
            )
        elif task.status == _S.LEASED.value:
            expired = task.lease_expires_at is not None and task.lease_expires_at <= now
            blockers.append(
                {
                    **base,
                    "reason": "lease_expired" if expired else "leased",
                    "leased_by": task.leased_by,
                    "lease_expires_at": _iso(task.lease_expires_at),
                    "blocked_dependents": _blocked_dependents(task.id, tasks),
                    "suggested_action": "wait",
                    "allowed_actions": [],
                    "rationale": (
                        "The lease expired: the driver's next claim sweeps it back to READY "
                        "(or FAILED at the attempt ceiling). Is the driver running?"
                        if expired
                        else "A driver is working on it; wait for its report."
                    ),
                }
            )
        elif task.status == _S.PENDING.value:
            deps = task.depends_on or []
            missing = [dep for dep in deps if dep not in by_id]
            if missing or all(by_id[dep].status in _SATISFIED for dep in deps):
                blockers.append(
                    {
                        **base,
                        "reason": "missing_dependency" if missing else "not_promoted",
                        "depends_on": deps,
                        "missing_dependencies": missing,
                        "blocked_dependents": _blocked_dependents(task.id, tasks),
                        "suggested_action": "investigate",
                        "allowed_actions": ["skip"],
                        "rationale": _with_mcp_note(
                            task.kind,
                            "PENDING although its dependencies are satisfied (or point at "
                            "rows outside this goal); promotion only happens when a task in "
                            "the goal is reported or skipped. Skip only if the step is not "
                            "needed.",
                        ),
                    }
                )

    verdict = _verdict(tasks)
    return bound_result(
        {
            "goal_key": goal_key,
            "verdict": verdict,
            "summary": _summary(verdict, tasks, blockers),
            "holds_capacity_slot": verdict != "finished",
            "by_status": dict(sorted(Counter(task.status for task in tasks).items())),
            "blockers": blockers,
            "action_endpoint": ACTION_ENDPOINT,
        }
    )


__all__ = [
    "EXPLAIN_STUCK_GOAL_DESCRIPTION",
    "PIPELINE_STATUS_DESCRIPTION",
    "explain_stuck_goal",
    "pipeline_status",
]
