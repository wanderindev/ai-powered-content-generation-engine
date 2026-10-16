"""Weekly-series agent-task admin + driver API (epic #422).

Planning (#417) plus the claim / lease / report surface an external driver needs
(#418). The whole router accepts either an admin magic-link token or the
long-lived driver service token — see ``validate_admin_or_driver_token``; the
driver token is scoped to this router plus the exact call surface ``KIND_CALLS``
emits (see ``validate_admin_or_allowlisted_driver_token``, #444), and nothing else.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_or_driver_token
from app.core.config import get_settings
from app.core.database import get_db
from app.models.agent_task import AgentTask
from app.models.crawl_job import CrawlJob
from app.schemas.agent_tasks import (
    AgentTaskItem,
    NextTaskResponse,
    PlanWeekRequest,
    PlanWeekResponse,
    ReportRequest,
    ReportResponse,
    TaskActionRequest,
    TaskActionResponse,
    WaitCheckResponse,
)
from app.schemas.media_candidate import CrawlJobResponse
from app.services.agent_task_driver import (
    apply_task_action,
    build_calls,
    claim_next_task,
    evaluate_wait,
    report_task,
    sweep_expired_leases,
)
from app.services.weekly_plan import plan_week

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/dashboard/agent-tasks",
    tags=["agent-tasks"],
    dependencies=[Depends(validate_admin_or_driver_token)],
)


@router.post("/plan-week", response_model=PlanWeekResponse)
def plan_week_endpoint(
    body: PlanWeekRequest | None = None,
    db: Session = Depends(get_db),
):
    """Ensure this week's pipeline task graph exists and return the whole goal.

    Insert-only and safe to call repeatedly (including on every driver tick):
    each call adds the rows the current database state newly permits and never
    touches rows that already exist, so a second call with unchanged state
    creates nothing. See ``app/services/weekly_plan.py`` for the graph shape,
    the ``PLAN_ARTICLES`` fan-out contract and the date rules.

    Body is optional. ``research_id`` forces a specific research (and is the
    re-plan path for one that already has articles); ``week_start`` anchors the
    goal key and the Instagram target dates. ``goal_key`` (sent by every
    ``PLAN_ARTICLES`` row: from its payload since #573, otherwise from the
    row's own ``goal_key`` column, #587) names the goal to expand and wins
    over the key ``week_start`` would compute; it must already exist (404
    otherwise), so it never starts a goal.

    Starting a **new** goal (no row has this goal key yet, no ``research_id``) is
    refused while ``weekly_plan_max_in_flight`` goals are unfinished: the answer
    is still a 200, with ``at_capacity=true``, ``goal_key`` / ``research_id``
    null, ``created`` 0, no tasks and ``in_flight_goals`` listing the goals
    holding the slots (#559). Expanding an existing goal is never capped.
    """
    body = body or PlanWeekRequest()
    max_in_flight = get_settings().weekly_plan_max_in_flight
    outcome = plan_week(
        db,
        research_id=body.research_id,
        week_start=body.week_start,
        goal_key=body.goal_key,
    )
    if outcome.at_capacity:
        detail = (
            f"At capacity: {len(outcome.in_flight_goals)} series in flight "
            f"(max {max_in_flight}): {', '.join(outcome.in_flight_goals)}"
        )
        logger.info("plan-week: %s", detail)
        return PlanWeekResponse(
            goal_key=None,
            research_id=None,
            created=0,
            tasks=[],
            at_capacity=True,
            max_in_flight=max_in_flight,
            in_flight_goals=outcome.in_flight_goals,
            detail=detail,
        )
    logger.info(
        "plan-week: goal=%s research=%s created=%s total=%s",
        outcome.goal_key,
        outcome.research_id,
        outcome.created,
        len(outcome.tasks),
    )
    return PlanWeekResponse(
        goal_key=outcome.goal_key,
        research_id=outcome.research_id,
        created=outcome.created,
        tasks=outcome.tasks,
        max_in_flight=max_in_flight,
    )


@router.get("/next", response_model=NextTaskResponse, responses={204: {"description": "No work"}})
def next_task(
    db: Session = Depends(get_db),
    principal=Depends(validate_admin_or_driver_token),
):
    """Lease the highest-priority READY task and return it with its call plan.

    Expired leases are swept first, so a task whose driver died is claimable
    again without any background machinery. The claim itself is ``SELECT ... FOR
    UPDATE SKIP LOCKED``, so two drivers can never take the same row.

    ``204`` means nothing is READY — which is also the driver's signal to call
    ``plan-week`` and see whether the graph can expand.
    """
    settings = get_settings()
    swept = sweep_expired_leases(db, settings)
    if swept:
        logger.info("agent-tasks next: swept %s expired lease(s)", swept)

    task = claim_next_task(db, settings, leased_by=principal.admin_email)
    if task is None:
        return Response(status_code=204)

    return NextTaskResponse(task=AgentTaskItem.model_validate(task), calls=build_calls(task))


@router.patch("/{task_id}", response_model=ReportResponse)
def report_task_endpoint(
    task_id: int,
    body: ReportRequest,
    db: Session = Depends(get_db),
):
    """Record a task's outcome and promote whatever it unblocked.

    ``DONE`` and ``SKIPPED`` both promote: the planner deliberately creates
    ``SKIPPED`` rows, so a skipped step must never block its successors.
    ``FAILED`` re-queues while ``attempts`` is under the ceiling and otherwise
    parks the task, leaving its dependents ``PENDING`` for a human. ``WAITING``
    (wait kinds only, #553) refunds the attempt and holds the task out of
    ``next`` until ``next_check_at``.
    """
    task, promoted = report_task(
        db,
        get_settings(),
        task_id,
        body.status,
        body.result,
        body.error,
        body.waiting_for,
    )
    return ReportResponse(task=AgentTaskItem.model_validate(task), promoted=promoted)


@router.post("/{task_id}/action", response_model=TaskActionResponse)
def task_action_endpoint(
    task_id: int,
    body: TaskActionRequest,
    db: Session = Depends(get_db),
    principal=Depends(validate_admin_or_driver_token),
):
    """Human intervention on a stuck task from the admin queue page (#420).

    ``retry`` returns a parked ``FAILED`` task to the pool with a fresh attempt
    budget; ``skip`` marks a task ``SKIPPED`` and promotes whatever that
    unblocks, which is how a week gets past a step that will never succeed.
    Distinct from ``PATCH /{task_id}``: that endpoint is the driver's report
    protocol and only accepts LEASED or READY rows.

    ``reason`` (optional) is stored with the caller's email on an
    ``agent_task_actions`` row (#641). A replay converges: ``skip`` on a
    SKIPPED task or ``retry`` on a READY / already-retried one returns 200
    with ``replayed: true`` and changes nothing. Same service function as the
    MCP ``retry_agent_task`` / ``skip_agent_task`` tools.
    """
    outcome = apply_task_action(
        db,
        task_id,
        body.action,
        reason=body.reason,
        actor=getattr(principal, "admin_email", None),
    )
    return TaskActionResponse(
        task=AgentTaskItem.model_validate(outcome.task),
        promoted=outcome.promoted,
        replayed=outcome.replayed,
    )


@router.get("", response_model=list[AgentTaskItem])
def list_agent_tasks(
    goal_key: str | None = Query(
        None, description="Restrict to one goal, e.g. weekly_series:2026-W31"
    ),
    db: Session = Depends(get_db),
):
    """List task rows for the admin grid and for debugging a stalled week.

    Unpaginated on purpose — a goal is a few dozen rows; the #420 grid can add
    paging if the table ever grows past browsing size.
    """
    query = db.query(AgentTask)
    if goal_key:
        query = query.filter(AgentTask.goal_key == goal_key)
    return query.order_by(
        AgentTask.goal_key.desc(),
        AgentTask.position.asc(),
        AgentTask.id.asc(),
    ).all()


@router.get("/wait-check/{kind}/{subject_id}", response_model=WaitCheckResponse)
def wait_check(
    kind: str,
    subject_id: int,
    db: Session = Depends(get_db),
):
    """Evaluate a wait kind's condition for one subject — read-only (#553).

    The single call of every wait kind. ``satisfied=false`` is the driver's cue
    to report ``WAITING`` with the returned ``waiting_for``; 404 for a kind that
    is not a wait kind.
    """
    waiting_for = evaluate_wait(db, kind, subject_id)
    return WaitCheckResponse(
        kind=kind,
        subject_id=subject_id,
        satisfied=waiting_for is None,
        waiting_for=waiting_for,
    )


@router.get("/jobs/{job_id}", response_model=CrawlJobResponse)
def get_agent_task_job(
    job_id: int,
    db: Session = Depends(get_db),
):
    """Poll a background job kicked off by a task (mirrors GET /media-library/jobs/{id}).

    The fact-check kinds answer 202 + a job id, and the canonical poll path lives
    on the admin-only media-library router. Proxying it here keeps the driver
    token scoped to this router instead of granting the driver all of
    media-library for the sake of one read.
    """
    job = db.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
