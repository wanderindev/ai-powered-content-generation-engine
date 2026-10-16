"""Weekly-series driver support — the claim / lease / report half of epic #422.

Where ``weekly_plan.py`` only *creates* task rows, this module is what moves them:
it leases a ``READY`` row to a driver, sweeps leases a dead driver still holds,
and records the outcome — including the dependency promotion that is, in effect,
the entire scheduler.

The kind -> calls mapping
------------------------
``KIND_CALLS`` maps every ``AgentTaskKind`` to a renderer producing the ordered
HTTP calls that execute it; ``build_calls`` is the dispatch (subject id
substituted into the path, body/query built from a copy of ``task.payload``,
which falls back to the row's own ``goal_key`` column when the payload carries
none, #587). Shipping the
call target *in the ``next`` response* is the design decision the issue makes
explicitly: it keeps the driver dumb — a stateless claim-call-report loop with no
pipeline knowledge — and keeps the pipeline knowledge here, in the repo that can
test it. The exhaustiveness test over ``AgentTaskKind`` means a new kind cannot
ship without someone deciding what it calls.

Most kinds are a single call. A few need two, and where the second call needs
data from the first response the path or body carries a ``{placeholder}`` and
``notes`` says exactly what to substitute. That is deliberately a contract in
prose: the driver is a thin shell loop, not a workflow engine, and the two
multi-step shapes here (poll a background job; preview-then-apply corrections)
do not justify a DSL.

``GENERATE_FEATURE_IMAGE`` calls the Gemini generation endpoint (#419). The
planner still plans it ``SKIPPED`` while ``weekly_plan_feature_images_enabled``
is off — that flag is now an output-quality gate, not a missing-endpoint one.

Lease semantics
---------------
A claim is ``SELECT ... FOR UPDATE SKIP LOCKED`` (mirroring
``instagram_scheduler._claim_next_due``) so two drivers can never take the same
row. ``attempts`` increments on claim only. A lease that expires returns the row
to ``READY`` — unless ``attempts`` already hit the ceiling, in which case it goes
``FAILED``, because otherwise a task that reliably kills its driver would loop
between claimed and expired forever. The sweep runs at the top of ``next`` rather
than on a background tick: same effect, no new moving parts.

Promotion
---------
Reporting ``DONE`` **or ``SKIPPED``** promotes every ``PENDING`` row in the same
goal whose dependencies are now all terminal-satisfied. Terminal-satisfied means
``DONE`` or ``SKIPPED`` — the planner creates ``SKIPPED`` rows on purpose (the
feature-image gate, series sections for a standalone article), so treating only
``DONE`` as satisfying would silently stall a whole week. A single pass suffices:
a freshly promoted row is ``READY``, and ``READY`` never satisfies a dependency.

``FAILED`` re-queues the row while ``attempts`` is under the ceiling and
otherwise leaves it ``FAILED`` with its dependents still ``PENDING`` — never a
cascade fail, because a human may fix the step by hand and re-run it.

Waiting (#553)
--------------
Some steps can only finish after the operator works on the laptop (deep
research in the pic-extension, Grammarly). Those are **wait kinds**: their
entry in ``WAIT_CONDITIONS`` is a read-only condition over the subject, and
their single call is the agent-tasks ``wait-check`` endpoint that evaluates it.
When the condition does not hold the driver reports ``WAITING`` with a short
``waiting_for`` text: the row goes ``WAITING``, the claim's attempt is refunded
(waiting is not failing), and ``next`` will not hand it out again until
``next_check_at`` (``agent_task_wait_recheck_hours`` later — the next daily
drive). Dependents stay ``PENDING``; ``WAITING`` satisfies nothing. When the
condition holds the driver reports ``DONE`` and promotion proceeds as usual.
A driver that predates ``WAITING`` reports ``FAILED`` instead, which still
works — it is the old burn-three-attempts-and-park path.

The first wait kind is ``WAIT_RESEARCH_READY`` (#554): the outline waits until
the research has no ``PENDING`` / ``NEEDS_DEEP_RESEARCH`` claims, because the
operator fact-checks research and applies its corrections before approving it.
The second is ``WAIT_ARTICLE_CLAIMS_RESOLVED`` (#555): an article's corrections
wait until its grounding claims are resolved — ``ABSENT`` grounding verdicts
are queued as ``NEEDS_DEEP_RESEARCH`` for the operator's deep-research drain.
The third and fourth are ``WAIT_GRAMMAR_CHECKED_EN`` / ``_ES`` (#556): the
operator's Grammarly pass in the pic-extension, satisfied by the article's
``grammar_checked_en_at`` / ``grammar_checked_es_at`` stamp.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.agent_task import AgentTask, AgentTaskActionLog
from app.models.article import Article
from app.models.enums import AgentTaskAction, AgentTaskKind, AgentTaskStatus, FactCheckSubject
from app.models.fact_check import FactCheckClaim
from app.schemas.agent_tasks import GOAL_KEY_PATTERN, CallSpec
from app.services.weekly_plan import _SATISFIED, _UNRESOLVED_VERDICTS

logger = logging.getLogger(__name__)

# The planner owns this rule (see its "Dependency semantics" docstring); the
# driver must honour exactly the same one when it promotes PENDING rows.
SATISFIED = _SATISFIED


def _job_poll_call() -> CallSpec:
    """The agent-tasks job proxy — how a 202-returning step is followed up."""
    return CallSpec(
        method="GET",
        path="/api/v1/admin/dashboard/agent-tasks/jobs/{job_id}",
        notes=(
            "substitute the job id from the previous response; poll until status is "
            "SUCCEEDED (report DONE) or FAILED (report FAILED)"
        ),
    )


def _corrections_calls(subject_type: str, subject_id: int) -> list[CallSpec]:
    """Preview-then-apply, the shape both correction kinds share."""
    base = f"/api/v1/admin/dashboard/fact-check/{subject_type}/{subject_id}/corrections"
    return [
        CallSpec(
            method="POST",
            path=f"{base}/preview",
            notes=(
                "200 with correctable_claims 0 and an empty edits array means the "
                "fact-check resolved clean — still call apply with the empty edits "
                "(it is a no-op success) and report DONE. 400 'Fact-check verdicts "
                "still pending' means the attended deep-research loop has not "
                "finished — report FAILED so the task re-queues or parks for a "
                "human (#481)."
            ),
        ),
        CallSpec(
            method="POST",
            path=f"{base}/apply",
            notes='body = {"edits": <the edits array from the preview response>}',
        ),
    ]


def _wait_check(kind: AgentTaskKind) -> Callable[[int, dict], list[CallSpec]]:
    """Renderer for a wait kind: one read-only condition check (#553)."""

    def render(sid: int, payload: dict) -> list[CallSpec]:
        return [
            CallSpec(
                method="GET",
                path=f"{AGENT_TASKS_PREFIX}/wait-check/{kind.value}/{sid}",
                notes=(
                    "read-only. satisfied=true -> report DONE. satisfied=false -> "
                    'report WAITING with {"waiting_for": <waiting_for from the '
                    "response>}; that is not a failure and burns no attempt."
                ),
            )
        ]

    return render


def _at(date_str: str | None, time_suffix: str) -> str | None:
    """Turn a payload date string into an instant, or None when absent."""
    return f"{date_str}{time_suffix}" if date_str else None


def _grammar_check(language: str) -> Callable[[int, dict], list[CallSpec]]:
    def render(sid: int, payload: dict) -> list[CallSpec]:
        return [
            CallSpec(
                method="POST",
                # admin.py router (prefix /admin), not the dashboard one.
                path=f"/api/v1/admin/articles/{sid}/grammar-check",
                query={"language": language},
            )
        ]

    return render


def _simple(method: str, path_tpl: str, **extra) -> Callable[[int, dict], list[CallSpec]]:
    """Renderer for a one-call kind whose body/query does not depend on payload."""

    def render(sid: int, payload: dict) -> list[CallSpec]:
        body = extra.get("body")
        if callable(body):
            body = body(sid)
        return [
            CallSpec(
                method=method,
                path=path_tpl.format(sid=sid),
                body=body,
                notes=extra.get("notes"),
            )
        ]

    return render


def _research_fact_check(sid: int, payload: dict) -> list[CallSpec]:
    return [
        CallSpec(
            method="POST",
            path=f"/api/v1/admin/dashboard/research/{sid}/fact-check",
            notes=(
                "no mode param: the default deep_research tier is the epic's "
                "deliberate human-gate design. Returns 202 + a job id."
            ),
        ),
        _job_poll_call(),
    ]


def _article_fact_check(sid: int, payload: dict) -> list[CallSpec]:
    return [
        CallSpec(
            method="POST",
            path=f"/api/v1/admin/dashboard/articles/{sid}/fact-check",
            notes=(
                "202 + a job id. write already auto-kicks a grounding check; the "
                "endpoint joins an in-flight run (202, same job id), so just poll it."
            ),
        ),
        _job_poll_call(),
    ]


def _plan_articles(sid: int, payload: dict) -> list[CallSpec]:
    # Re-invoking plan-week IS the expansion (see weekly_plan's docstring).
    # week_start carries the goal's original anchor date (target dates); rows
    # planned before #418 lack it, so it is omitted rather than guessed.
    # goal_key names the goal outright, so a cadence flip or a missing
    # week_start cannot recompute a different key. Rows planned since #573
    # carry it in the payload; older rows get the row's own goal_key column
    # injected by build_calls (#587), so the legacy case only omits week_start.
    body = {k: payload[k] for k in ("week_start", "goal_key") if payload.get(k)}
    return [
        CallSpec(
            method="POST",
            path="/api/v1/admin/dashboard/agent-tasks/plan-week",
            body=body or None,
            notes="the ensure-pass is the fan-out; expect created > 0 the first time",
        )
    ]


def _write_article(sid: int, payload: dict) -> list[CallSpec]:
    return [
        CallSpec(
            method="PATCH",
            path=f"/api/v1/admin/dashboard/articles/{sid}/outline-status",
            body={"status": "APPROVED"},
            notes=(
                "required: outlines are created PENDING and write 400s on a "
                "non-APPROVED outline; the unattended pipeline auto-approves."
            ),
        ),
        CallSpec(method="POST", path=f"/api/v1/admin/dashboard/articles/{sid}/write"),
    ]


def _set_publish_date(sid: int, payload: dict) -> list[CallSpec]:
    # No body: the endpoint picks the date when the step runs (#558) — the
    # next free 12:00 UTC day, one series per day, shared by every member of
    # the series. A legacy row's payload["target_publish_date"] (fixed at
    # planning time, days stale by now) is deliberately ignored.
    return [
        CallSpec(
            method="PATCH",
            path=f"/api/v1/admin/dashboard/articles/{sid}/publish",
            notes=(
                "no body: the server picks the slot and replays reuse it. 400s "
                "until the article has a feature image — with "
                "weekly_plan_feature_images_enabled off the image step is SKIPPED and a "
                "human supplies it; report FAILED."
            ),
        )
    ]


def _schedule_ig_post(sid: int, payload: dict) -> list[CallSpec]:
    # Sabías-qué rows carry a planned target_date (17:00 UTC = noon Panama).
    # Article-promo rows planned since #576 carry none: the endpoint derives
    # the time from the article's actual published_at (publish day + 1), so
    # the promo can never precede the article. A legacy promo row still sends
    # its planned date.
    scheduled_at = _at(payload.get("target_date"), "T17:00:00Z")
    return [
        CallSpec(
            method="PATCH",
            path=f"/api/v1/admin/dashboard/instagram/posts/{sid}/schedule",
            body={"scheduled_at": scheduled_at} if scheduled_at else None,
            notes=(
                "requires APPROVED + rendered; the planner's chain order guarantees it. "
                "No body = server derives the promo time from the article's publish "
                "date; 409 until the article has one (report FAILED)."
            ),
        )
    ]


# Wait kinds (#553): kind -> condition(db, subject_id). The condition returns
# None when it holds, otherwise a short operator-facing description of what the
# step is waiting for. Read-only by contract — the driver may call it every
# drive. A wait kind's KIND_CALLS entry is ``_wait_check(kind)``.
def _claims_resolved(subject: FactCheckSubject, label: str) -> Callable[[Session, int], str | None]:
    """Condition: the subject has no ``PENDING`` / ``NEEDS_DEEP_RESEARCH`` claims.

    A subject with **no** claims at all also holds — for research that is the
    operator's call (#554): approved research the operator chose not to
    fact-check is ready. Reusable for any fact-check subject.
    """

    def condition(db: Session, subject_id: int) -> str | None:
        rows = (
            db.query(FactCheckClaim.verdict, func.count(FactCheckClaim.id))
            .filter(
                FactCheckClaim.subject_type == subject.value,
                FactCheckClaim.subject_id == subject_id,
                FactCheckClaim.verdict.in_(_UNRESOLVED_VERDICTS),
            )
            .group_by(FactCheckClaim.verdict)
            .all()
        )
        if not rows:
            return None
        counts = ", ".join(f"{n} {verdict}" for verdict, n in sorted(rows))
        return (
            f"fact-check of {label} {subject_id} unresolved ({counts}): "
            "deep research / corrections by the operator"
        )

    return condition


def _grammar_checked(language: str) -> Callable[[Session, int], str | None]:
    """Condition: the article carries its ``grammar_checked_<lang>_at`` stamp.

    The stamp is written by grammar-apply when the operator runs the Grammarly
    pass in the pic-extension (an empty ``accepted`` list stamps too), so a
    clean article costs the operator one run, not a rewrite.
    """
    column = Article.grammar_checked_en_at if language == "en" else Article.grammar_checked_es_at

    def condition(db: Session, subject_id: int) -> str | None:
        row = db.query(Article.id, column).filter(Article.id == subject_id).first()
        if row is None:
            return f"article {subject_id} not found"
        if row[1] is not None:
            return None
        return f"Grammarly {language.upper()} pass on article {subject_id} by the operator"

    return condition


WAIT_CONDITIONS: dict[str, Callable[[Session, int], str | None]] = {
    AgentTaskKind.WAIT_RESEARCH_READY.value: _claims_resolved(
        FactCheckSubject.RESEARCH, "research"
    ),
    # No claims at all also holds here; the corrections preview then 400s "run
    # a fact-check first", so an unchecked article still cannot silently no-op.
    AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED.value: _claims_resolved(
        FactCheckSubject.ARTICLE, "article"
    ),
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value: _grammar_checked("en"),
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value: _grammar_checked("es"),
}


def evaluate_wait(db: Session, kind: str, subject_id: int) -> str | None:
    """Run a wait kind's condition; None when satisfied. 404 for a non-wait kind."""
    condition = WAIT_CONDITIONS.get(kind)
    if condition is None:
        raise HTTPException(status_code=404, detail=f"{kind} is not a wait kind")
    return condition(db, subject_id)


# Every kind a driver can be handed, mapped to its call renderer. Exhaustive over
# AgentTaskKind by test — a new kind must decide what it calls before it ships.
KIND_CALLS: dict[str, Callable[[int, dict], list[CallSpec]]] = {
    # Legacy (#554): new plans gate on WAIT_RESEARCH_READY instead; these two
    # stay for goals planned before it, whose rows the planner never rewrites.
    AgentTaskKind.RESEARCH_FACT_CHECK.value: _research_fact_check,
    AgentTaskKind.APPLY_RESEARCH_CORRECTIONS.value: (
        lambda sid, payload: _corrections_calls("RESEARCH", sid)
    ),
    AgentTaskKind.GENERATE_OUTLINE.value: _simple(
        "POST",
        "/api/v1/admin/dashboard/articles/generate-outlines",
        body=lambda sid: {"research_id": sid},
    ),
    AgentTaskKind.PLAN_ARTICLES.value: _plan_articles,
    AgentTaskKind.WRITE_ARTICLE.value: _write_article,
    AgentTaskKind.ARTICLE_FACT_CHECK.value: _article_fact_check,
    AgentTaskKind.APPLY_ARTICLE_CORRECTIONS.value: (
        lambda sid, payload: _corrections_calls("ARTICLE", sid)
    ),
    # Legacy (#556): new plans wait for the operator's Grammarly pass instead
    # (WAIT_GRAMMAR_CHECKED_EN / _ES); these two stay for goals planned before
    # it. The LLM endpoint itself stays for manual runs from the admin grid.
    AgentTaskKind.GRAMMAR_CHECK_EN.value: _grammar_check("en"),
    AgentTaskKind.GENERATE_TAGS.value: _simple(
        "POST", "/api/v1/admin/dashboard/articles/{sid}/generate-tags"
    ),
    AgentTaskKind.TRANSLATE.value: _simple(
        "POST",
        "/api/v1/admin/dashboard/articles/{sid}/translate",
        notes="translates title + excerpt + content in one call",
    ),
    AgentTaskKind.GRAMMAR_CHECK_ES.value: _grammar_check("es"),
    # #557: the Spanish says what the English says (DeepL mistakes). Zero fixes
    # is a 200; a replay re-runs and converges, so there is no "already done".
    AgentTaskKind.ES_ACCURACY_CHECK.value: _simple(
        "POST",
        "/api/v1/admin/articles/{sid}/es-accuracy-check",
        notes="200 with applied/skipped counts — report DONE, zero fixes included",
    ),
    # Planned SKIPPED while weekly_plan_feature_images_enabled is off, but the
    # endpoint exists (#419), so a row planned with the flag on is runnable.
    AgentTaskKind.GENERATE_FEATURE_IMAGE.value: _simple(
        "POST",
        "/api/v1/admin/dashboard/articles/{sid}/generate-image",
        notes="503 while GEMINI_API_KEY is unset — report FAILED",
    ),
    AgentTaskKind.GENERATE_SERIES_SECTIONS.value: _simple(
        "POST",
        "/api/v1/admin/dashboard/articles/{sid}/series-sections",
        notes=(
            "resolves the series parent itself, so any member works. Responds 200 with "
            "a success flag — report FAILED if success is false."
        ),
    ),
    AgentTaskKind.APPROVE_ARTICLE.value: _simple(
        "PATCH",
        "/api/v1/admin/dashboard/articles/{sid}/status",
        body={"status": "APPROVED"},
    ),
    AgentTaskKind.SET_PUBLISH_DATE.value: _set_publish_date,
    AgentTaskKind.GENERATE_SABIAS_QUE.value: _simple(
        "POST",
        "/api/v1/admin/dashboard/instagram/generate/sabias-que",
        body=lambda sid: {"research_id": sid},
    ),
    AgentTaskKind.GENERATE_ARTICLE_PROMO.value: _simple(
        "POST",
        "/api/v1/admin/dashboard/instagram/generate/article-promo",
        body=lambda sid: {"article_id": sid},
    ),
    AgentTaskKind.APPROVE_IG_POST.value: _simple(
        "PATCH",
        "/api/v1/admin/dashboard/instagram/posts/{sid}/status",
        body={"status": "APPROVED"},
    ),
    AgentTaskKind.RENDER_IG_POST.value: _simple(
        "POST", "/api/v1/admin/dashboard/instagram/posts/{sid}/render"
    ),
    AgentTaskKind.SCHEDULE_IG_POST.value: _schedule_ig_post,
    AgentTaskKind.WAIT_RESEARCH_READY.value: _wait_check(AgentTaskKind.WAIT_RESEARCH_READY),
    AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED.value: _wait_check(
        AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED
    ),
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value: _wait_check(AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN),
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value: _wait_check(AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES),
}


# The exact (method, path) surface KIND_CALLS emits onto admin-token routers.
# Kept next to KIND_CALLS and bound to it by test: every rendered call must be
# either on the agent-tasks router (which accepts the driver token wholesale)
# or matched here. Anchored full-match regexes including the method, because a
# prefix would over-grant (``/articles/{id}`` also serves GET/PUT/DELETE) and a
# route *template* would too (``PATCH /{entity}/{entity_id}/status`` is generic
# over suggestions/research/articles — only articles belongs to the driver). (#444)
DRIVER_ALLOWED_CALLS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # dashboard.py (prefix /api/v1/admin/dashboard)
    ("POST", re.compile(r"/api/v1/admin/dashboard/research/[0-9]+/fact-check")),
    (
        "POST",
        re.compile(
            r"/api/v1/admin/dashboard/fact-check/(RESEARCH|ARTICLE)/[0-9]+/corrections/preview"
        ),
    ),
    (
        "POST",
        re.compile(
            r"/api/v1/admin/dashboard/fact-check/(RESEARCH|ARTICLE)/[0-9]+/corrections/apply"
        ),
    ),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/generate-outlines")),
    ("PATCH", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/outline-status")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/write")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/fact-check")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/generate-tags")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/translate")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/generate-image")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/series-sections")),
    ("PATCH", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/status")),
    ("PATCH", re.compile(r"/api/v1/admin/dashboard/articles/[0-9]+/publish")),
    # instagram.py (prefix /api/v1/admin/dashboard/instagram)
    ("POST", re.compile(r"/api/v1/admin/dashboard/instagram/generate/sabias-que")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/instagram/generate/article-promo")),
    ("PATCH", re.compile(r"/api/v1/admin/dashboard/instagram/posts/[0-9]+/status")),
    ("POST", re.compile(r"/api/v1/admin/dashboard/instagram/posts/[0-9]+/render")),
    ("PATCH", re.compile(r"/api/v1/admin/dashboard/instagram/posts/[0-9]+/schedule")),
    # admin.py (prefix /api/v1/admin). The ``?language=`` query is not part of
    # request.url.path, so GRAMMAR_CHECK_EN and _ES share this one entry.
    ("POST", re.compile(r"/api/v1/admin/articles/[0-9]+/grammar-check")),
    ("POST", re.compile(r"/api/v1/admin/articles/[0-9]+/es-accuracy-check")),
)

# Calls onto this router need no allowlist entry: the agent-tasks router already
# accepts the driver token wholesale (``validate_admin_or_driver_token``, #418).
AGENT_TASKS_PREFIX = "/api/v1/admin/dashboard/agent-tasks"


def is_driver_call_allowed(method: str, path: str) -> bool:
    """True when (method, path) is on the exact surface KIND_CALLS emits (#444)."""
    return any(m == method and rx.fullmatch(path) for m, rx in DRIVER_ALLOWED_CALLS)


def build_calls(task: AgentTask) -> list[CallSpec]:
    """Render the concrete HTTP calls that execute ``task``.

    Paths are absolute (``/api/v1/...``) so the driver can call them verbatim.
    An unmapped kind yields an empty list rather than an error: ``next`` must
    never 500 on a row someone added out of band.

    The renderer gets a *copy* of the payload — never the row's JSONB itself,
    which ``next`` also serializes back to the driver. When the payload has no
    ``goal_key``, the copy gets the row's own ``goal_key`` column (#587), but
    only if it has a plan-week shape (``GOAL_KEY_PATTERN``); an odd value would
    turn a working expansion into a 422, so it is left out as before. The
    payload value wins when present.
    """
    render = KIND_CALLS.get(task.kind)
    if render is None:
        logger.error("agent-task %s has no call mapping for kind %s", task.id, task.kind)
        return []
    payload = dict(task.payload or {})
    # Applied to every kind: only _plan_articles reads goal_key; every other
    # renderer ignores the payload or reads only target_date (#587).
    if not payload.get("goal_key") and re.fullmatch(GOAL_KEY_PATTERN, task.goal_key or ""):
        payload["goal_key"] = task.goal_key
    return render(task.subject_id, payload)


def sweep_expired_leases(db: Session, settings: Settings) -> int:
    """Return every expired lease to the pool; returns how many rows were swept.

    A row whose ``attempts`` already reached the ceiling goes ``FAILED`` instead
    of ``READY`` — otherwise a task that kills its driver every time would claim,
    expire and re-claim forever.
    """
    now = datetime.now(UTC)
    expired = (
        db.query(AgentTask)
        .filter(
            AgentTask.status == AgentTaskStatus.LEASED.value,
            AgentTask.lease_expires_at.isnot(None),
            AgentTask.lease_expires_at < now,
        )
        .all()
    )
    for task in expired:
        if (task.attempts or 0) >= settings.agent_task_max_attempts:
            task.status = AgentTaskStatus.FAILED.value
            task.error = "lease expired; attempts exhausted"
        else:
            task.status = AgentTaskStatus.READY.value
        task.leased_by = None
        task.lease_expires_at = None
        logger.warning(
            "agent-task lease expired: id=%s kind=%s attempts=%s -> %s",
            task.id,
            task.kind,
            task.attempts,
            task.status,
        )
    if expired:
        db.commit()
    return len(expired)


def _select_next_for_update(db: Session) -> AgentTask | None:
    """Lock the next claimable row (SKIP LOCKED). Caller owns the commit.

    Claimable = ``READY``, or ``WAITING`` whose recheck time has passed (#553).
    Reading the due WAITING rows here, rather than flipping them to READY in a
    sweep, keeps the admin page honest: a row shows WAITING until a driver
    actually re-checks it.
    """
    now = datetime.now(UTC)
    return (
        db.query(AgentTask)
        .filter(
            or_(
                AgentTask.status == AgentTaskStatus.READY.value,
                and_(
                    AgentTask.status == AgentTaskStatus.WAITING.value,
                    or_(AgentTask.next_check_at.is_(None), AgentTask.next_check_at <= now),
                ),
            )
        )
        .order_by(AgentTask.position.asc(), AgentTask.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
        .first()
    )


def claim_next_task(db: Session, settings: Settings, leased_by: str) -> AgentTask | None:
    """Lease the highest-priority READY task, or return None when there is none."""
    task = _select_next_for_update(db)
    if task is None:
        db.rollback()  # release the transaction opened by the locking select
        return None

    task.status = AgentTaskStatus.LEASED.value
    task.leased_by = leased_by
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=settings.agent_task_lease_minutes)
    task.attempts = (task.attempts or 0) + 1
    task.next_check_at = None
    db.commit()  # claim visible to (and skipped by) concurrent drivers
    db.refresh(task)
    logger.info(
        "agent-task claimed: id=%s kind=%s subject=%s/%s attempt=%s by=%s",
        task.id,
        task.kind,
        task.subject_type,
        task.subject_id,
        task.attempts,
        leased_by,
    )
    return task


def _promote_ready(db: Session, goal_key: str) -> list[int]:
    """Flip every PENDING row of the goal whose dependencies are all satisfied.

    Goal-scoped because dependency ids are goal-internal by construction. One
    pass is enough — a promoted row is READY, and READY satisfies nothing.
    """
    tasks = db.query(AgentTask).filter(AgentTask.goal_key == goal_key).all()
    by_id = {t.id: t.status for t in tasks}
    promoted: list[int] = []
    for task in tasks:
        if task.status != AgentTaskStatus.PENDING.value:
            continue
        deps = task.depends_on or []
        if all(by_id.get(dep_id) in SATISFIED for dep_id in deps):
            task.status = AgentTaskStatus.READY.value
            promoted.append(task.id)
    return promoted


def report_task(
    db: Session,
    settings: Settings,
    task_id: int,
    status: str,
    result: dict | None,
    error: str | None,
    waiting_for: str | None = None,
) -> tuple[AgentTask, list[int]]:
    """Record a driver's outcome for one task; returns ``(task, promoted_ids)``.

    The leaseholder is deliberately *not* checked: this is a single-driver
    deployment and ``leased_by`` is observability bookkeeping, not an ownership
    lock. A late report (after a sweep returned the row to READY) is still valid
    completed work, so READY is an accepted starting state; a terminal or PENDING
    task is a protocol error and 409s.
    """
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Agent task not found")
    if task.status not in (AgentTaskStatus.LEASED.value, AgentTaskStatus.READY.value):
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} is {task.status}; only LEASED or READY tasks can be reported",
        )

    if status == AgentTaskStatus.WAITING.value and task.kind not in WAIT_CONDITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"{task.kind} is not a wait kind; only wait kinds can report WAITING",
        )

    # Overwrite, never merge — the report is the whole truth about this attempt.
    task.result = result
    task.error = error
    task.leased_by = None
    task.lease_expires_at = None
    task.waiting_for = None
    task.next_check_at = None

    promoted: list[int] = []
    if status == AgentTaskStatus.WAITING.value:
        # Waiting on the operator is not a failure: refund the claim's attempt
        # so a week of waiting never parks the row, and keep it out of ``next``
        # until the next drive. Dependents stay PENDING — WAITING satisfies
        # nothing.
        task.status = status
        task.attempts = max((task.attempts or 0) - 1, 0)
        task.waiting_for = waiting_for or "operator"
        task.next_check_at = datetime.now(UTC) + timedelta(
            hours=settings.agent_task_wait_recheck_hours
        )
    elif status in (AgentTaskStatus.DONE.value, AgentTaskStatus.SKIPPED.value):
        task.status = status
        db.flush()
        promoted = _promote_ready(db, task.goal_key)
    else:  # FAILED
        if (task.attempts or 0) < settings.agent_task_max_attempts:
            # Under the ceiling: back into the pool for another driver tick.
            # Dependents stay PENDING either way — never cascade-fail.
            task.status = AgentTaskStatus.READY.value
        else:
            task.status = AgentTaskStatus.FAILED.value

    db.commit()
    db.refresh(task)
    logger.info(
        "agent-task reported: id=%s reported=%s -> status=%s promoted=%s",
        task.id,
        status,
        task.status,
        promoted,
    )
    return task, promoted


# States a human may skip from. LEASED is excluded on purpose: a driver is
# actively working the row, so the operator waits for its report or the sweep.
_SKIPPABLE = (
    AgentTaskStatus.PENDING.value,
    AgentTaskStatus.READY.value,
    AgentTaskStatus.FAILED.value,
    AgentTaskStatus.WAITING.value,
)

# States a human may retry from. On a WAITING row "retry" means "check again
# now" — the operator finished the work and does not want to wait for the
# recheck time (#553).
_RETRYABLE = (AgentTaskStatus.FAILED.value, AgentTaskStatus.WAITING.value)


# Longest reason stored on an action row; the HTTP schema and the MCP tools cap
# their input at this length.
MAX_ACTION_REASON_CHARS = 1000


@dataclass
class TaskActionResult:
    """What :func:`apply_task_action` did (or found already done)."""

    task: AgentTask
    promoted: list[int]
    # True when the task was already in the action's target state and nothing
    # changed (the #495 replay rule); no action row is written then.
    replayed: bool
    # The newest action row for the task (the one just written, or on a
    # replay the earlier one that put it in this state), or None.
    last_action: AgentTaskActionLog | None


def _latest_action(db: Session, task_id: int) -> AgentTaskActionLog | None:
    return (
        db.query(AgentTaskActionLog)
        .filter(AgentTaskActionLog.task_id == task_id)
        .order_by(AgentTaskActionLog.id.desc())
        .first()
    )


def apply_task_action(
    db: Session,
    task_id: int,
    action: str,
    *,
    reason: str | None = None,
    actor: str | None = None,
) -> TaskActionResult:
    """Apply a human intervention to one task.

    The admin queue page (#420) is how a stalled week gets unstuck without SSH:
    ``retry`` puts a parked ``FAILED`` task back in the pool, ``skip`` marks a
    task ``SKIPPED`` and promotes whatever that unblocks. ``report_task`` cannot
    serve either — it rejects any starting state other than LEASED/READY. The
    MCP ``retry_agent_task`` / ``skip_agent_task`` tools (#641) call this same
    function.

    Every applied action appends an ``agent_task_actions`` row with ``reason``
    and ``actor``. Replays converge (#495): ``skip`` on a ``SKIPPED`` task, and
    ``retry`` on a ``READY`` task (the state a retry produces), return the
    task unchanged with ``replayed=True`` and write nothing. Anything else
    outside the allowed starting states is a 409; a retry replayed after the
    driver has already claimed the row therefore gets a 409 naming
    ``LEASED`` / ``DONE``, which itself shows the first retry took effect.
    """
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Agent task not found")
    if action not in AgentTaskAction.values():
        raise HTTPException(status_code=400, detail="action must be 'retry' or 'skip'")
    reason = (reason or "").strip()[:MAX_ACTION_REASON_CHARS] or None

    latest = _latest_action(db, task.id)
    if action == AgentTaskAction.RETRY.value:
        if task.status not in _RETRYABLE:
            # READY is the state a retry produces. Anything else keeps its 409,
            # whatever the history: a weeks-old retry must not mask a DONE or
            # LEASED row, and the 409 names the status the retry led to.
            if task.status == AgentTaskStatus.READY.value:
                return TaskActionResult(task, [], True, latest)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Task {task_id} is {task.status}; only FAILED or WAITING tasks can be retried"
                ),
            )
    elif task.status == AgentTaskStatus.SKIPPED.value:
        return TaskActionResult(task, [], True, latest)
    elif task.status not in _SKIPPABLE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task {task_id} is {task.status}; "
                "only PENDING, READY, FAILED or WAITING tasks can be skipped"
            ),
        )

    entry = AgentTaskActionLog(
        task_id=task.id,
        action=action,
        previous_status=task.status,
        reason=reason,
        actor=actor,
    )
    db.add(entry)
    promoted: list[int] = []
    if action == AgentTaskAction.RETRY.value:
        # Reset the attempt counter: at the ceiling the sweep fails an expired
        # lease outright and a FAILED report parks the row, so a retry without
        # the reset would buy one attempt and no lease-expiry grace. ``error``
        # stays as visible history; the next report overwrites it.
        task.status = AgentTaskStatus.READY.value
        task.attempts = 0
        task.leased_by = None
        task.lease_expires_at = None
        task.next_check_at = None
        task.waiting_for = None
    else:  # skip
        task.status = AgentTaskStatus.SKIPPED.value
        task.leased_by = None
        task.lease_expires_at = None
        task.next_check_at = None
        db.flush()
        promoted = _promote_ready(db, task.goal_key)

    db.commit()
    db.refresh(task)
    db.refresh(entry)
    logger.info(
        "agent-task action: id=%s action=%s -> status=%s promoted=%s actor=%s",
        task.id,
        action,
        task.status,
        promoted,
        actor,
    )
    return TaskActionResult(task, promoted, False, entry)
