"""Weekly-series planner — turns one week's pipeline work into ``agent_tasks`` rows.

This module is the "plan" half of the goal-based weekly pipeline (epic #422):
it only *creates* task rows. Executing them is the driver's job (#418); nothing
here calls an LLM, an external service, or another endpoint.

Ensure-pass idempotency
-----------------------
Planning is an **insert-only ensure-pass**, not a one-shot build. Every call
computes the graph the *current database state* permits and inserts only the
rows that are missing, keyed by ``(goal_key, kind, subject_type, subject_id)``
— the model's unique constraint. It never mutates a row it did not just create:
no status flips, no payload rewrites. Promoting an existing ``PENDING`` row to
``READY`` once its dependencies finish belongs to the driver (#418).

Consequence, and the issue's acceptance criterion: two back-to-back calls with
unchanged DB state create zero rows, because the graph is already fully
expanded for that state. Idempotency is per task row, not per goal.

Fan-out: why ``PLAN_ARTICLES`` rows
-----------------------------------
The per-article tasks cannot be planned up front — the ``articles`` rows do not
exist until ``GENERATE_OUTLINE`` runs. The issue offered two mechanisms
(the outline task re-invokes the planner on completion, or the planner emits an
explicit expansion task); we emit an explicit ``PLAN_ARTICLES`` row.

Rationale: the #418 driver is a stateless claim-and-call loop, so an expansion
step modelled as a row is inspectable in the grid, retryable, leasable and
failable exactly like every other step, instead of being hidden inside a
report-handler side effect. **A claimed ``PLAN_ARTICLES`` task maps to calling
plan-week again** — the ensure-pass *is* the expansion. (That mapping is the
driver's code; this module only defines the contract.)

There are two fan-out points, hence two ``PLAN_ARTICLES`` rows per goal: one on
the research (expands the article chains and the sabías-que post chains once the
outline and the drafts exist) and one on the series parent article (expands the
article-promo post chain once ``GENERATE_ARTICLE_PROMO`` has produced the
draft). They coexist under the unique constraint because their subjects differ.

Dependency semantics
--------------------
A new row starts ``READY`` when it has no dependencies or when every dependency
is already **terminal-satisfied**, otherwise ``PENDING``. Terminal-satisfied
means ``DONE`` **or ``SKIPPED``** — a step the planner deliberately skipped must
never block its successors, and the driver (#418) has to honour the same rule
when it promotes ``PENDING`` rows.

A skipped step is a pass-through, not a shortcut (#702): a new row that depends
on a ``SKIPPED`` row also gets, transitively, that row's own dependencies in its
``depends_on``. Otherwise a row whose only dependency was planned ``SKIPPED``
would start ``READY`` while the skipped row's predecessors were still running —
a standalone article's ``APPROVE_ARTICLE`` (behind the "not a series" sections
row and the flag-skipped feature image) was claimable before ``WRITE_ARTICLE``.
Because the inherited ids are stored, the driver's promotion keeps the order
with no rule of its own.

The planner plans a step ``SKIPPED`` (with a ``payload["reason"]``) whenever a
``READY`` row would be un-runnable: an outline for research that already has
articles, sabías-que drafts that already exist, series sections for a standalone
article, the feature image while ``weekly_plan_feature_images_enabled`` is
off (the endpoint exists since #419; the flag is the output-quality gate), or
every Instagram step — ``GENERATE_SABIAS_QUE``,
``GENERATE_ARTICLE_PROMO`` and any per-post approve / render / schedule chain —
while ``weekly_plan_ig_posts_enabled`` is off (#552: the account is not
connected, so drafts would only pile up). With that flag off the generators
never run, so no drafts exist and the per-post chains are normally never planned
at all; a draft that already exists gets its chain planned SKIPPED. Both
``PLAN_ARTICLES`` rows still run, because SKIPPED satisfies them. The chain shape stays uniform either way, which keeps the graph
readable.

Two of three sabías-que drafts
------------------------------
``generate_sabias_que_posts`` creates **three** drafts but the week schedules
two. The planner picks the two lowest-id ``SABIAS_QUE`` drafts for the research
(lowest → Tuesday, next → Wednesday) and gives the surplus draft no task rows at
all: it stays a ``PENDING`` draft for manual use. It is deliberately *not*
marked ``SKIPPED`` — the planner never touches rows it did not create.

Series in flight (#559)
-----------------------
A call that would start a **new** goal is refused while
``weekly_plan_max_in_flight`` series are unfinished — a goal is unfinished while
any of its rows is not ``DONE`` / ``SKIPPED`` (so ``FAILED`` counts too). The
answer is a success marked ``at_capacity``, not an error: the driver's
empty-queue call ends its session normally instead of reporting a problem.
"New goal" means no row carries this call's goal key yet *and* no explicit
``research_id`` was passed. Expanding an existing goal (every ``PLAN_ARTICLES``
row's call, which re-sends that goal's ``week_start``) and an operator's
explicit research choice are never capped.

Since #573 that call also sends the goal key itself (``PLAN_ARTICLES`` payload
``goal_key``), which wins over the key recomputed from ``week_start``: a row
planned before #418 has no ``week_start`` (today's date would be used) and a
``weekly_plan_daily_cadence`` flip mid-goal changes the key's shape, and either
miss used to answer ``at_capacity`` — or plan an unrelated goal — while the
driver marked the expansion DONE. An explicit key must already have rows (404
otherwise), so it can only expand, never start a goal past the cap. Rows
planned before #573 have no payload ``goal_key``; since #587 the driver's
``build_calls`` sends the row's own ``goal_key`` column instead, so every
``PLAN_ARTICLES`` row names its goal. The sabías-qué target dates follow that
goal key's shape (``daily_series:`` or ``weekly_series:``), not the live
``weekly_plan_daily_cadence`` setting, so a goal expanded after a cadence flip
keeps its own date pattern (#587).
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.agent_task import AgentTask
from app.models.article import Article
from app.models.enums import (
    AgentTaskKind,
    AgentTaskStatus,
    AgentTaskSubject,
    FactCheckVerdict,
    InstagramPostType,
)
from app.models.instagram_post import InstagramPost
from app.models.research import Research

# Statuses that satisfy a dependency (see "Dependency semantics" above).
_SATISFIED = {AgentTaskStatus.DONE.value, AgentTaskStatus.SKIPPED.value}

# Verdicts still in flight (the attended deep-research loop). The wait kinds'
# conditions (agent_task_driver._claims_resolved) and the corrections preview's
# 400 key off the same set.
_UNRESOLVED_VERDICTS = {
    FactCheckVerdict.PENDING.value,
    FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
}


# The per-article chain, in execution order. Each task depends on the previous
# one; the first depends on the goal's GENERATE_OUTLINE task. The chain is
# planned in two passes split at GENERATE_SERIES_SECTIONS: series sections
# regenerate the WHOLE series and require every member translated, so that
# task additionally depends on ALL articles' TRANSLATE tasks (the 2026-09-02
# drive burned attempts running it while its siblings were still unwritten)
# and, since #558, on ALL articles' ES grammar step — the last step that
# changes the text, so the series meets there with final copy and publishes
# together (SET_PUBLISH_DATE picks the shared date when it runs). TRANSLATE
# stays in the fan-in because a legacy GRAMMAR_CHECK_ES may have been planned
# SKIPPED (the retired grammar-checks flag), which satisfies at once.
#
# WAIT_ARTICLE_CLAIMS_RESOLVED (#555) holds the corrections until the
# article's grounding claims are resolved (ABSENT ones go to deep research).
# APPLY_ARTICLE_CORRECTIONS is therefore never planned SKIPPED any more (the
# #481 plan-time "no correctable claims" skip): a SKIPPED corrections row would
# satisfy the EN grammar wait at once and route the chain around the gate, and
# the claims the planner can see are stale anyway — ARTICLE_FACT_CHECK
# re-grounds. A resolved-clean article is handled at run time by the preview's
# 200 with no edits.
#
# WAIT_GRAMMAR_CHECKED_EN / _ES (#556) wait for the operator's Grammarly pass in
# the pic-extension (the article's grammar_checked_*_at stamp). They replace the
# LLM GRAMMAR_CHECK_EN / _ES steps; a chain planned before #556 keeps its
# GRAMMAR_CHECK_* rows and gets no wait (see _LEGACY_GRAMMAR). The EN wait sits
# after the corrections so Grammarly never polishes text a correction would
# later rewrite, and before TRANSLATE so the Spanish is made from the polished
# English.
#
# ES_ACCURACY_CHECK (#557) sits between TRANSLATE and the ES grammar wait: it
# fixes Spanish that does not say what the fact-checked English says, before
# Grammarly polishes it. A chain that already has its ES grammar row when this
# step is reached (legacy GRAMMAR_CHECK_ES, or a WAIT_GRAMMAR_CHECKED_ES planned
# before #557 and already depending on TRANSLATE) gets no inserted step — the
# ensure-pass never rewrites that row's depends_on, so a check planned now would
# dangle off TRANSLATE in parallel with the grammar step instead of before it.
_ARTICLE_CHAIN: tuple[AgentTaskKind, ...] = (
    AgentTaskKind.WRITE_ARTICLE,
    AgentTaskKind.ARTICLE_FACT_CHECK,
    AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED,
    AgentTaskKind.APPLY_ARTICLE_CORRECTIONS,
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN,
    AgentTaskKind.GENERATE_TAGS,
    AgentTaskKind.TRANSLATE,
    AgentTaskKind.ES_ACCURACY_CHECK,
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES,
    AgentTaskKind.GENERATE_FEATURE_IMAGE,
    AgentTaskKind.GENERATE_SERIES_SECTIONS,
    AgentTaskKind.APPROVE_ARTICLE,
    AgentTaskKind.SET_PUBLISH_DATE,
)

_SERIES_SECTIONS_AT = _ARTICLE_CHAIN.index(AgentTaskKind.GENERATE_SERIES_SECTIONS)

# Grammar wait kind -> the LLM step it replaced. When an article's chain
# already has the legacy row (planned before #556), the planner chains off it
# and plans no wait — the next step's depends_on already points at the legacy
# row and the ensure-pass never rewrites it, so a wait would gate nothing.
_LEGACY_GRAMMAR: dict[AgentTaskKind, AgentTaskKind] = {
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN: AgentTaskKind.GRAMMAR_CHECK_EN,
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES: AgentTaskKind.GRAMMAR_CHECK_ES,
}

# Either one means the chain's ES grammar step was planned before #557 reached
# it (see ES_ACCURACY_CHECK above _ARTICLE_CHAIN).
_ES_GRAMMAR_KINDS: tuple[AgentTaskKind, ...] = (
    AgentTaskKind.GRAMMAR_CHECK_ES,
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES,
)

# The Instagram chain, in execution order. PATCH /instagram/posts/{id}/schedule
# requires the post to be APPROVED *and* already rendered, so this order is a
# hard precondition of the endpoints, not a preference.
# Skip reason for every Instagram step while weekly_plan_ig_posts_enabled is off.
_IG_DISABLED_REASON = "instagram posts disabled (weekly_plan_ig_posts_enabled)"

_IG_CHAIN: tuple[AgentTaskKind, ...] = (
    AgentTaskKind.APPROVE_IG_POST,
    AgentTaskKind.RENDER_IG_POST,
    AgentTaskKind.SCHEDULE_IG_POST,
)

# Python weekday() numbering (Monday = 0).
_TUESDAY, _WEDNESDAY = 1, 2


def _coming(week_start: date, weekday: int) -> date:
    """Return the first date strictly *after* ``week_start`` falling on ``weekday``.

    "Strictly after" is deliberate: when ``week_start`` already is that weekday
    the result is a week later, so planning on a Tuesday puts the first
    sabías-qué post on the *coming* Tuesday rather than on the planning day.
    """
    return week_start + timedelta(days=((weekday - week_start.weekday() - 1) % 7) + 1)


def goal_key_for(week_start: date) -> str:
    """Build the goal key for the anchor date.

    Weekly cadence keys by ISO week (``weekly_series:2026-W31``) so every drive
    within a week converges on the same goal. Daily cadence
    (``weekly_plan_daily_cadence``) keys by the date itself
    (``daily_series:2026-09-03``) so each day's drive plans a fresh series —
    the backlog-burn mode.
    """
    if get_settings().weekly_plan_daily_cadence:
        return f"daily_series:{week_start.isoformat()}"
    iso = week_start.isocalendar()
    return f"weekly_series:{iso.year}-W{iso.week:02d}"


def _sabias_target_dates(week_start: date, daily: bool) -> tuple[date, date]:
    """The two sabías-qué Instagram target dates for the goal's cadence.

    ``daily`` is derived from the resolved goal key's prefix (#587), not the
    live setting, so an existing goal keeps its own cadence after a flip.

    Weekly: the coming Tuesday/Wednesday. Daily: day+1 and day+2. Neither the
    article publish date (#558) nor the article-promo date (#576) is planned
    any more: ``SET_PUBLISH_DATE`` lets the publish endpoint pick the article's
    date when the series is finished, and the promo's ``SCHEDULE_IG_POST``
    lets the schedule endpoint derive its time from that date. Sabías-qué
    cards are written from the research and do not point at the article, so
    they keep their planned dates.
    """
    if daily:
        return week_start + timedelta(days=1), week_start + timedelta(days=2)
    return _coming(week_start, _TUESDAY), _coming(week_start, _WEDNESDAY)


def next_research_for_planning(db: Session) -> Research | None:
    """Return the next APPROVED research that has no articles yet.

    Mirrors ``GET /admin/dashboard/research/available-for-outlines`` (the same
    ordering by ``suggestion_id``); duplicated rather than shared so the planner
    does not couple itself to a presentation endpoint's response shape.
    """
    research_ids_with_articles = {
        row[0]
        for row in db.query(Article.research_id).filter(Article.research_id.isnot(None)).distinct()
    }
    candidates = (
        db.query(Research)
        .filter(Research.status == "APPROVED")
        .order_by(Research.suggestion_id.asc())
        .all()
    )
    for research in candidates:
        if research.id not in research_ids_with_articles:
            return research
    return None


class _Planner:
    """Accumulates the desired rows for one goal, inserting only what's missing."""

    def __init__(self, db: Session, goal_key: str, existing: list[AgentTask]):
        self.db = db
        self.goal_key = goal_key
        self.tasks: dict[tuple[str, str, int], AgentTask] = {
            (t.kind, t.subject_type, t.subject_id): t for t in existing
        }
        self.created = 0
        self._next_pos = max((t.position for t in existing), default=0) + 10

    def get(
        self, kind: AgentTaskKind, subject: AgentTaskSubject, subject_id: int
    ) -> AgentTask | None:
        return self.tasks.get((kind.value, subject.value, subject_id))

    def ensure(
        self,
        kind: AgentTaskKind,
        subject: AgentTaskSubject,
        subject_id: int,
        *,
        depends_on: list[AgentTask] | None = None,
        skip_reason: str | None = None,
        payload: dict | None = None,
    ) -> AgentTask:
        """Return the goal's task for this key, creating it if it is missing.

        An already-present row is returned untouched — status, payload and
        dependencies of existing rows are never rewritten (see module docstring).
        """
        key = (kind.value, subject.value, subject_id)
        found = self.tasks.get(key)
        if found is not None:
            return found

        deps = self._through_skipped([d for d in (depends_on or []) if d is not None])
        if skip_reason is not None:
            status = AgentTaskStatus.SKIPPED.value
            payload = {**(payload or {}), "reason": skip_reason}
        elif all(d.status in _SATISFIED for d in deps):
            status = AgentTaskStatus.READY.value
        else:
            status = AgentTaskStatus.PENDING.value

        task = AgentTask(
            goal_key=self.goal_key,
            kind=kind.value,
            subject_type=subject.value,
            subject_id=subject_id,
            status=status,
            depends_on=[d.id for d in deps] or None,
            position=self._next_pos,
            payload=payload,
        )
        self.db.add(task)
        # Flush so dependents can reference this row's real id in depends_on.
        self.db.flush()
        self.tasks[key] = task
        self.created += 1
        self._next_pos += 10
        return task

    def _through_skipped(self, deps: list[AgentTask]) -> list[AgentTask]:
        """``deps`` plus, transitively, the dependencies of every SKIPPED one (#702).

        SKIPPED satisfies a dependency exactly like DONE, so a row whose only
        dependency is a planned-SKIPPED step (series sections for a standalone
        article, the feature image behind its flag, an Instagram step) used to
        start READY — and be promoted on the goal's next report — while that
        step's own predecessors were still running: a single-article goal's
        APPROVE_ARTICLE was claimable before WRITE_ARTICLE. A skipped step is a
        pass-through, not a shortcut, so the new row also waits on whatever the
        skipped one waited on. Stored ids are goal-internal, so the driver's
        promotion honours the same order with no rule of its own.
        """
        by_id = {t.id: t for t in self.tasks.values()}
        # Breadth-first, so the caller's own dependencies keep their order and
        # inherited ones follow them.
        result: list[AgentTask] = []
        seen: set[int] = set()
        for dep in deps:
            if dep.id not in seen:
                seen.add(dep.id)
                result.append(dep)
        i = 0
        while i < len(result):
            dep = result[i]
            i += 1
            if dep.status != AgentTaskStatus.SKIPPED.value:
                continue
            for dep_id in dep.depends_on or []:
                if dep_id in by_id and dep_id not in seen:
                    seen.add(dep_id)
                    result.append(by_id[dep_id])
        return result


def _resolve_research(
    db: Session,
    goal_key: str,
    existing: list[AgentTask],
    research_id: int | None,
) -> Research:
    """Decide which research this goal is about (see the endpoint docstring)."""
    if existing:
        planned_ids = [
            t.subject_id for t in existing if t.kind == AgentTaskKind.GENERATE_OUTLINE.value
        ]
        if planned_ids:
            planned_id = planned_ids[0]
            if research_id is not None and research_id != planned_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"goal {goal_key} is already planned for research {planned_id}",
                )
            research = db.query(Research).filter(Research.id == planned_id).first()
            if research is None:
                raise HTTPException(status_code=404, detail="Research not found")
            return research

    if research_id is not None:
        research = db.query(Research).filter(Research.id == research_id).first()
        if research is None:
            raise HTTPException(status_code=404, detail="Research not found")
        if research.status != "APPROVED":
            raise HTTPException(status_code=400, detail="Research must be APPROVED")
        return research

    research = next_research_for_planning(db)
    if research is None:
        raise HTTPException(status_code=404, detail="No research available for planning")
    return research


def unfinished_goals(db: Session) -> list[str]:
    """Goal keys with at least one row not ``DONE`` / ``SKIPPED``, sorted.

    Counts honestly: a goal parked on a ``FAILED`` row, or one whose ``PENDING``
    rows sit behind it, stays in flight until the operator retries or skips the
    stuck row from the admin queue.
    """
    rows = db.query(AgentTask.goal_key).filter(AgentTask.status.notin_(_SATISFIED)).distinct().all()
    return sorted(row[0] for row in rows)


@dataclass
class PlanOutcome:
    """What one ``plan_week`` call did.

    ``at_capacity`` = a new goal was due but ``weekly_plan_max_in_flight`` series
    are unfinished, so nothing was planned: ``goal_key`` / ``research_id`` are
    ``None``, ``created`` is 0, ``tasks`` is empty and ``in_flight_goals`` names
    the goals that hold the slots.
    """

    goal_key: str | None
    research_id: int | None
    created: int
    tasks: list[AgentTask]
    at_capacity: bool = False
    in_flight_goals: list[str] = field(default_factory=list)


def _ordered_articles(articles: list[Article]) -> list[Article]:
    """Series parent first, then children by ``series_order`` (id as tiebreaker)."""
    parents = [a for a in articles if a.series_parent_id is None]
    parent = parents[0] if parents else min(articles, key=lambda a: a.id)
    children = sorted(
        (a for a in articles if a.id != parent.id),
        key=lambda a: (a.series_order if a.series_order is not None else 0, a.id),
    )
    return [parent, *children]


def plan_week(
    db: Session,
    *,
    research_id: int | None = None,
    week_start: date | None = None,
    goal_key: str | None = None,
) -> PlanOutcome:
    """Ensure the week's task graph exists and report what was planned.

    Safe to call on every driver tick: it inserts only the rows the current DB
    state newly permits. Concurrent double-invocation is backstopped by the
    ``uq_agent_tasks_goal_kind_subject`` unique constraint — the loser raises an
    IntegrityError (a 500) and a retry then finds the rows already present, so no
    application-level locking is needed.

    A call that would start a new goal returns an ``at_capacity`` outcome
    instead while too many series are in flight (see "Series in flight").

    An explicit ``goal_key`` (the ``PLAN_ARTICLES`` expansion path, #573) wins
    over the key ``week_start`` would compute and must name an existing goal;
    ``week_start`` still anchors the target dates, and the goal key's shape
    (``daily_series:`` vs ``weekly_series:``) picks their cadence (#587).
    """
    settings = get_settings()
    week_start = week_start or datetime.now(UTC).date()
    explicit_goal = goal_key is not None
    goal_key = goal_key or goal_key_for(week_start)
    # The goal's own shape decides its cadence (#587): identical to the setting
    # for a new goal, and stable for an existing one across a cadence flip.
    daily = goal_key.startswith("daily_series:")
    sabias_targets = _sabias_target_dates(week_start, daily)

    existing = db.query(AgentTask).filter(AgentTask.goal_key == goal_key).all()
    if explicit_goal and not existing:
        # Expansion only: an explicit key never starts a goal (that would be a
        # way around the in-flight cap and around goal_key_for's shapes).
        raise HTTPException(status_code=404, detail=f"Goal {goal_key} not found")
    if not existing and research_id is None and settings.weekly_plan_max_in_flight > 0:
        in_flight = unfinished_goals(db)
        if len(in_flight) >= settings.weekly_plan_max_in_flight:
            return PlanOutcome(
                goal_key=None,
                research_id=None,
                created=0,
                tasks=[],
                at_capacity=True,
                in_flight_goals=in_flight,
            )
    research = _resolve_research(db, goal_key, existing, research_id)
    planner = _Planner(db, goal_key, existing)

    research_subject = AgentTaskSubject.RESEARCH
    articles = db.query(Article).filter(Article.research_id == research.id).all()

    # --- Wave 0: everything that only needs the research row to exist --------
    # The operator fact-checks research and applies its corrections *before*
    # approving it, so the week only waits until that work is finished (#554):
    # one wait task, satisfied once the research has no PENDING /
    # NEEDS_DEEP_RESEARCH claims (or none at all). Goals planned before #554
    # keep their RESEARCH_FACT_CHECK / APPLY_RESEARCH_CORRECTIONS rows — the
    # ensure-pass never rewrites them — and keep chaining off the corrections
    # row, so a re-plan of an in-flight goal adds no gate.
    research_ready = planner.get(
        AgentTaskKind.APPLY_RESEARCH_CORRECTIONS, research_subject, research.id
    ) or planner.ensure(AgentTaskKind.WAIT_RESEARCH_READY, research_subject, research.id)

    outline = planner.ensure(
        AgentTaskKind.GENERATE_OUTLINE,
        research_subject,
        research.id,
        depends_on=[research_ready],
        # The endpoint replays existing articles idempotently (200, created=0),
        # but there is nothing to do — plan the recovery / explicit-research_id
        # path SKIPPED rather than spend a claim on a no-op.
        skip_reason="articles already exist" if articles else None,
    )
    # None while IG posts are enabled; otherwise the reason every IG step
    # (generators and per-post chains) is planned SKIPPED with.
    ig_skip_reason = None if settings.weekly_plan_ig_posts_enabled else _IG_DISABLED_REASON
    sabias_posts = (
        db.query(InstagramPost)
        .filter(
            InstagramPost.post_type == InstagramPostType.SABIAS_QUE.value,
            InstagramPost.research_id == research.id,
        )
        .order_by(InstagramPost.id.asc())
        .all()
    )
    sabias = planner.ensure(
        AgentTaskKind.GENERATE_SABIAS_QUE,
        research_subject,
        research.id,
        # Fact cards must be written from corrected research.
        depends_on=[research_ready],
        skip_reason=ig_skip_reason or ("posts already exist" if sabias_posts else None),
    )
    planner.ensure(
        AgentTaskKind.PLAN_ARTICLES,
        research_subject,
        research.id,
        depends_on=[outline, sabias],
        # week_start is the goal's anchor date: the driver re-invokes plan-week
        # with it so a mid-week expansion computes the same target dates as the
        # original call (#418). goal_key is sent too and wins over the key
        # recomputed from week_start, which a cadence flip would change (#573).
        payload={
            "expands": "article chains + sabias-que post chains",
            "week_start": week_start.isoformat(),
            "goal_key": goal_key,
        },
    )

    # --- Wave 1a: per-article chains, once the outline produced articles -----
    if articles:
        ordered = _ordered_articles(articles)
        parent = ordered[0]
        is_series = len(ordered) > 1
        parent_approve: AgentTask | None = None

        # Pass 1: every article's chain up to (not including) series sections,
        # so all TRANSLATE and ES grammar tasks exist before anything depends
        # on them.
        translates: list[AgentTask] = []
        es_finals: list[AgentTask] = []
        tails: list[AgentTask] = []
        for article in ordered:
            previous = outline
            for kind in _ARTICLE_CHAIN[:_SERIES_SECTIONS_AT]:
                skip_reason = None
                if (
                    kind is AgentTaskKind.GENERATE_FEATURE_IMAGE
                    and not settings.weekly_plan_feature_images_enabled
                ):
                    skip_reason = (
                        "feature-image generation disabled (weekly_plan_feature_images_enabled)"
                    )
                elif kind in _LEGACY_GRAMMAR and (
                    legacy := planner.get(
                        _LEGACY_GRAMMAR[kind], AgentTaskSubject.ARTICLE, article.id
                    )
                ):
                    # In flight before #556: keep chaining off the LLM step.
                    previous = legacy
                    if kind is AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES:
                        es_finals.append(previous)
                    continue
                elif (
                    kind is AgentTaskKind.ES_ACCURACY_CHECK
                    and not planner.get(kind, AgentTaskSubject.ARTICLE, article.id)
                    and any(
                        planner.get(es_grammar, AgentTaskSubject.ARTICLE, article.id)
                        for es_grammar in _ES_GRAMMAR_KINDS
                    )
                ):
                    # In flight before #557: the ES grammar row already chains
                    # off TRANSLATE.
                    continue
                elif kind is AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED and planner.get(
                    AgentTaskKind.APPLY_ARTICLE_CORRECTIONS, AgentTaskSubject.ARTICLE, article.id
                ):
                    # In flight before #555: this chain's corrections row already
                    # depends on the fact-check, and the ensure-pass never
                    # rewrites it, so a gate planned now would gate nothing.
                    continue
                previous = planner.ensure(
                    kind,
                    AgentTaskSubject.ARTICLE,
                    article.id,
                    depends_on=[previous],
                    skip_reason=skip_reason,
                )
                if kind is AgentTaskKind.TRANSLATE:
                    translates.append(previous)
                elif kind is AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES:
                    es_finals.append(previous)
            tails.append(previous)

        # Pass 2: series sections, approval, publish date. Series sections
        # regenerate the whole series at once, so each one waits for EVERY
        # article's translation and ES grammar step, not just its own chain.
        # An in-flight series keeps its existing rows' TRANSLATE-only fan-in
        # (insert-only); a member planned now gets the wider one.
        for article, tail in zip(ordered, tails):
            previous = tail
            for kind in _ARTICLE_CHAIN[_SERIES_SECTIONS_AT:]:
                skip_reason = None
                depends = [previous]
                if kind is AgentTaskKind.GENERATE_SERIES_SECTIONS:
                    if not is_series:
                        skip_reason = "not a series"
                    else:
                        for task in (*translates, *es_finals):
                            if all(task is not d for d in depends):
                                depends.append(task)
                # SET_PUBLISH_DATE carries no payload (#558): the publish
                # endpoint picks the date when the step runs, and every member
                # of the series reuses the first member's date.
                previous = planner.ensure(
                    kind,
                    AgentTaskSubject.ARTICLE,
                    article.id,
                    depends_on=depends,
                    skip_reason=skip_reason,
                )
                if kind is AgentTaskKind.APPROVE_ARTICLE and article.id == parent.id:
                    parent_approve = previous

        promo = planner.ensure(
            AgentTaskKind.GENERATE_ARTICLE_PROMO,
            AgentTaskSubject.ARTICLE,
            parent.id,
            # The generation service 400s on a non-APPROVED article.
            depends_on=[parent_approve] if parent_approve else [],
            skip_reason=ig_skip_reason,
        )
        planner.ensure(
            AgentTaskKind.PLAN_ARTICLES,
            AgentTaskSubject.ARTICLE,
            parent.id,
            depends_on=[promo],
            payload={
                "expands": "article-promo post chain",
                "week_start": week_start.isoformat(),
                "goal_key": goal_key,
            },
        )

        # --- Wave 2: the promo post's chain, once the draft exists ----------
        promo_post = (
            db.query(InstagramPost)
            .filter(
                InstagramPost.post_type == InstagramPostType.ARTICLE_PROMO.value,
                InstagramPost.article_id == parent.id,
            )
            .order_by(InstagramPost.id.asc())
            .first()
        )
        if promo_post is not None:
            # No target date (#576): the schedule endpoint derives the promo's
            # time from the article's real publish date, so the schedule step
            # also waits for the parent's SET_PUBLISH_DATE.
            _plan_ig_chain(
                planner,
                promo_post.id,
                promo,
                None,
                ig_skip_reason,
                schedule_after=planner.get(
                    AgentTaskKind.SET_PUBLISH_DATE, AgentTaskSubject.ARTICLE, parent.id
                ),
            )

    # --- Wave 1b: the two scheduled sabías-que drafts ------------------------
    for post, target in zip(sabias_posts[:2], sabias_targets):
        _plan_ig_chain(planner, post.id, sabias, target, ig_skip_reason)

    db.commit()

    tasks = (
        db.query(AgentTask)
        .filter(AgentTask.goal_key == goal_key)
        .order_by(AgentTask.position.asc(), AgentTask.id.asc())
        .all()
    )
    return PlanOutcome(
        goal_key=goal_key, research_id=research.id, created=planner.created, tasks=tasks
    )


def _plan_ig_chain(
    planner: _Planner,
    post_id: int,
    generator_task: AgentTask,
    target_date: date | None,
    skip_reason: str | None = None,
    schedule_after: AgentTask | None = None,
) -> None:
    """Plan APPROVE -> RENDER -> SCHEDULE for one Instagram post draft.

    ``skip_reason`` plans the whole chain SKIPPED (IG posts disabled). A
    ``target_date`` of None plans the schedule step without one (the endpoint
    derives the time — the article promo, #576); ``schedule_after`` adds a
    second dependency to the schedule step.
    """
    previous = generator_task
    for kind in _IG_CHAIN:
        depends = [previous]
        payload = None
        if kind is AgentTaskKind.SCHEDULE_IG_POST:
            if target_date is not None:
                payload = {"target_date": target_date.isoformat()}
            if schedule_after is not None:
                depends.append(schedule_after)
        previous = planner.ensure(
            kind,
            AgentTaskSubject.INSTAGRAM_POST,
            post_id,
            depends_on=depends,
            skip_reason=skip_reason,
            payload=payload,
        )
