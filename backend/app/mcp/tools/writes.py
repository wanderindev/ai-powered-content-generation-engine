"""MCP write tools, ``pipeline`` scope (#641).

* ``get_deep_research_batch`` / ``submit_fact_check_verdicts``: the
  deep-research fact-check loop, over the same service functions as
  ``POST /admin/dashboard/fact-check/deep-research/generate-prompt`` and
  ``.../deep-research/verdicts`` (``fact_check.deep_research_batch`` and
  ``fact_check.submit_deep_research_verdicts``).
* ``submit_research``: research intake, over
  ``research_intake.submit_research`` (also behind
  ``POST /admin/dashboard/research/upload``), which replaces the
  ``/research-process`` skill's raw SQL.
* ``retry_agent_task`` / ``skip_agent_task``: over
  ``agent_task_driver.apply_task_action``, the function behind
  ``POST /admin/dashboard/agent-tasks/{id}/action``; the reason and the
  token name land on an ``agent_task_actions`` row. Both refuse the kinds in
  ``MCP_REFUSED_TASK_ACTIONS`` (#686, #690): the operator's wait gates and the
  fact-check / corrections / grammar steps that feed them cannot be skipped,
  and the publish-path steps cannot be retried or skipped from MCP; those stay
  with the admin Agent Tasks queue.

Every submit converges on replay (#495): the second identical call returns
the existing result with ``replayed: true`` instead of an error.

Each tool opens its own ``SessionLocal()`` and closes it before returning;
the services end their transaction before any external call (Haiku in
research validation), and the claim reindex after a verdict upload (Voyage)
runs after the session is closed, off the call's path in a daemon thread
(:func:`_reindex_off_request`, #689) -- the MCP twin of the HTTP route's
``schedule_claims_reindex`` background task. Errors are ``ToolError``s raised outside
any ``except`` block, so no service exception is chained to them, and their
text never quotes the caller's document.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.core import database
from app.mcp.tools.bounds import MAX_RESULT_CHARS, bound_result, result_size
from app.models.agent_task import AgentTask, AgentTaskActionLog
from app.models.enums import AgentTaskAction, AgentTaskKind, AgentTaskStatus
from app.services import fact_check, research_intake
from app.services.agent_task_driver import MAX_ACTION_REASON_CHARS, apply_task_action
from app.services.knowledge.claims import reindex_claims

DeepResearchScope = Literal["SUGGESTION", "RESEARCH", "EDU_RESEARCH", "ARTICLE"]
BATCH_ID_PATTERN = r"^\d{8}_\d{6}$"
# A deep-research verdict document is a few KB of verdict lines plus notes.
MAX_VERDICT_DOCUMENT_CHARS = 500_000
# Research targets 5-8k words (~50k chars); the HTTP upload caps files at 5 MB.
MAX_RESEARCH_CHARS = 1_000_000


def _caller() -> str | None:
    """``mcp:<token name>`` of the calling token, for the action audit row.

    Reads the access token's ``client_id`` without binding the token to a
    local (it carries the raw bearer; see docs/claude/mcp.md).
    """
    name = getattr(get_access_token(), "client_id", None)
    return f"mcp:{name}" if name else None


# --- get_deep_research_batch -------------------------------------------------------

GET_DEEP_RESEARCH_BATCH_DESCRIPTION = """\
Get the next batch of Panama In Context fact-check claims that wait for deep research \
(verdict NEEDS_DEEP_RESEARCH), compiled into the verification prompt to run in a \
deep-research session. Same service as the admin API's deep-research generate-prompt.

Use it to start one cycle of the deep-research fact-check loop: run prompt_text in a \
deep-research tool, then pass the report and this batch's claim_ids to \
submit_fact_check_verdicts. Call it again while pending_total > claim_count, but only \
after submitting this batch: it always takes the oldest pending claims, so until then it \
returns the same ones under a new batch_id. It changes nothing (no batch is persisted; \
batch_id is a timestamp label), so it is safe to repeat; \
it needs the pipeline scope only because it is the loop's first step. Do not use it to \
look up whether one statement was already checked (use check_claim).

Arguments: subject_type (optional: SUGGESTION, RESEARCH, EDU_RESEARCH or ARTICLE), \
subject_id (optional, needs subject_type). Without them, every pending claim, oldest \
subject first.

Returns {"nothing_pending": bool, "pending_total": int, "batch_id": str | null, \
"claim_ids": [int], "claim_count": int, "prompt_text": str | null, \
"limited_by_size": bool, "truncated": false}. At most 25 claims; fewer when the prompt \
would exceed the 20k-character result cap (limited_by_size), so prompt_text is not cut; \
only a single claim too long for the cap on its own is clipped (truncated: true). \
Nothing pending is not an error: nothing_pending is true, pending_total 0, prompt_text null.

Example: get_deep_research_batch(subject_type="ARTICLE", subject_id=251) -> \
{"nothing_pending": false, "pending_total": 31, "batch_id": "20261011_120000", \
"claim_ids": [9012, 9013, ...], "claim_count": 25, "prompt_text": "REF: \
fact_check_20261011_120000 ...", "limited_by_size": false, "truncated": false}"""


def get_deep_research_batch(
    subject_type: DeepResearchScope | None = None,
    subject_id: Annotated[int | None, Field(ge=1)] = None,
) -> dict[str, Any]:
    limit = fact_check.DEEP_RESEARCH_BATCH_LIMIT
    limited = False
    scope_error: str | None = None
    db = database.SessionLocal()
    try:
        while True:
            try:
                batch = fact_check.deep_research_batch(db, subject_type, subject_id, limit=limit)
            except fact_check.DeepResearchScopeError as err:
                scope_error = str(err)
                batch = None
                break
            if batch is None or limit == 1:
                break
            size = result_size(batch)
            if size <= MAX_RESULT_CHARS - 500:
                break
            # Shrink the batch rather than cut the prompt: a clipped prompt
            # would lose claims the verdict upload then reports as missing.
            limited = True
            limit = max(1, min(limit - 1, limit * (MAX_RESULT_CHARS - 500) // size))
    finally:
        db.close()
    if scope_error is not None:
        raise ToolError(f"invalid_argument: {scope_error}")
    if batch is None:
        return {
            "nothing_pending": True,
            "pending_total": 0,
            "batch_id": None,
            "claim_ids": [],
            "claim_count": 0,
            "prompt_text": None,
            "limited_by_size": False,
            "truncated": False,
        }
    return bound_result(
        {
            "nothing_pending": False,
            "pending_total": batch["pending_total"],
            "batch_id": batch["batch_id"],
            "claim_ids": batch["claim_ids"],
            "claim_count": len(batch["claim_ids"]),
            "prompt_text": batch["prompt_text"],
            "limited_by_size": limited,
        }
    )


# --- submit_fact_check_verdicts ---------------------------------------------------


def _reindex_off_request(article_ids, *, verified: bool) -> threading.Thread:
    """Run :func:`reindex_claims` in a daemon thread and return at once (#689).

    The reindex embeds through Voyage (60 s timeout, up to 4 attempts), so in
    line it held the tool call open for ~113 s in prod (2026-10-12), past the
    client's tool timeout. The HTTP twin queues the same work as a
    ``BackgroundTask``; an MCP tool has no response hook, so it gets a thread.
    ``reindex_claims`` never raises and opens its own sessions; a reindex lost
    to a worker restart is caught up by the next one or by
    ``scripts.reindex_knowledge``.
    """
    thread = threading.Thread(
        target=reindex_claims,
        args=(list(article_ids),),
        kwargs={"verified": verified},
        name="mcp-claims-reindex",
        daemon=True,
    )
    thread.start()
    return thread


SUBMIT_FACT_CHECK_VERDICTS_DESCRIPTION = """\
Write a deep-research verdict report back to Panama In Context's fact-check claims. Same \
service as the admin API's deep-research verdicts upload, with all its conservatism: \
only lines "VERDICT: <claim id> | VERIFIED|INACCURATE|DISPUTED|UNVERIFIABLE | <note> | \
<citations>" are read; VERIFIED / INACCURATE / DISPUTED need a citation; a claim must \
still be NEEDS_DEEP_RESEARCH (an already-resolved one is skipped, never overwritten); \
verdicts outside claim_ids are skipped and unanswered ids reported as missing (they stay \
queued for the next batch). Definitive verdicts also feed the verified-claims knowledge \
base, and resolved article claims are re-indexed in the background right after the call \
returns.

Use it right after a deep-research run of a get_deep_research_batch prompt, with that \
batch's claim_ids. Do not use it to record a verdict you reasoned out yourself without \
sources, or for claims that were not in the batch.

Arguments: claim_ids (the batch's ids, 1-25), document (the full report text, \
verbatim), batch_id (optional, the batch's label, echoed back).

Returns {"batch_id", "written": int, "skipped": [{"claim_id", "reason"}], \
"missing_claim_ids": [int], "kb_upserts": int, "replayed": bool, "truncated"}. \
Replaying the same document converges: nothing is written, every claim is skipped as \
"claim is no longer pending (verdict=...)", and replayed is true. Errors: "rejected: ..." \
when every verdict is an uncited UNVERIFIABLE (the signature of a run whose web tools \
failed; nothing is written and the claims stay queued) or when the document has no \
VERDICT line at all.

Example: submit_fact_check_verdicts(claim_ids=[9012, 9013], batch_id="20261011_120000", \
document="## Verdicts\\nVERDICT: 9012 | VERIFIED | - | Castillero Calvo, 2004\\n...") -> \
{"batch_id": "20261011_120000", "written": 1, "skipped": [], "missing_claim_ids": [9013], \
"kb_upserts": 1, "replayed": false, "truncated": false}"""


def submit_fact_check_verdicts(
    claim_ids: Annotated[
        list[int], Field(min_length=1, max_length=fact_check.DEEP_RESEARCH_BATCH_LIMIT)
    ],
    document: Annotated[str, Field(min_length=1, max_length=MAX_VERDICT_DOCUMENT_CHARS)],
    batch_id: Annotated[str | None, Field(pattern=BATCH_ID_PATTERN)] = None,
) -> dict[str, Any]:
    rejected: str | None = None
    result: dict[str, Any] | None = None
    db = database.SessionLocal()
    try:
        try:
            result = fact_check.submit_deep_research_verdicts(db, document, list(claim_ids))
        except fact_check.VerdictDocumentRejected as err:
            db.rollback()
            rejected = str(err)
    finally:
        db.close()
    if rejected is not None:
        raise ToolError(f"rejected: {rejected}")
    assert result is not None
    # After the session is closed, and off the call's path (#689).
    if result["article_ids"] or result["kb_upserts"]:
        _reindex_off_request(result["article_ids"], verified=result["kb_upserts"] > 0)
    return bound_result(
        {
            "batch_id": batch_id,
            "written": result["written"],
            "skipped": [{"claim_id": cid, "reason": reason} for cid, reason in result["skipped"]],
            "missing_claim_ids": result["missing_claim_ids"],
            "kb_upserts": result["kb_upserts"],
            "replayed": result["replayed"],
        }
    )


# --- submit_research -----------------------------------------------------------------

SUBMIT_RESEARCH_DESCRIPTION = """\
Store a finished deep-research document as the research for one Panama In Context \
article suggestion. Same service as the admin research upload, and the replacement for \
the /research-process skill's SQL. It first runs the quality checks: at least 4,000 \
words; a Sources / References / Bibliography / Works Cited / Citations heading (when \
missing, one is compiled from the inline citations and appended); at least 2 of the \
suggestion's sub-topics covered (judged by a model). Then it inserts a PENDING research \
row, which the operator reviews and approves in the admin UI.

Use it once per research document produced from a research prompt for that suggestion. \
Do not use it to edit approved research (it refuses; only the operator can, in the \
admin UI), to store notes or partial drafts \
(they fail the checks), or for edu research.

Arguments: suggestion_id, markdown (the full document, verbatim), replace (default \
false). When the suggestion already has research with a different body the call fails \
with "conflict: ..." unless replace is true; replace never overwrites APPROVED research \
(that stays an operator action in the admin UI) and resets replaced research to PENDING.

Returns {"accepted": bool, "action": "inserted" | "updated" | "replayed" | "rejected", \
"replayed": bool, "suggestion_id", "suggestion_title", "research_id": int | null, \
"status": str | null, "word_count": int, "references_generated": bool, "checks": \
{"word_count": bool, "references": bool, "subtopic_coverage": bool | {"passed", "found", \
"total"}} | null, "truncated"}. A document that fails the checks is accepted=false, \
action "rejected", with checks saying which failed; nothing is stored. Sending the body \
already stored converges: action "replayed", the existing research_id, no checks re-run. \
Errors: "not_found: ..." (no such suggestion), "conflict: ...".

Example: submit_research(suggestion_id=412, markdown="# The Darien Scheme\\n...") -> \
{"accepted": true, "action": "inserted", "replayed": false, "suggestion_id": 412, \
"research_id": 233, "status": "PENDING", "word_count": 6120, \
"references_generated": false, "checks": {...}, "truncated": false}"""


def submit_research(
    suggestion_id: Annotated[int, Field(ge=1)],
    markdown: Annotated[str, Field(min_length=1, max_length=MAX_RESEARCH_CHARS)],
    replace: bool = False,
) -> dict[str, Any]:
    failure: str | None = None
    outcome: research_intake.ResearchIntakeResult | None = None
    db = database.SessionLocal()
    try:
        try:
            outcome = research_intake.submit_research(
                db,
                suggestion_id,
                markdown,
                replace=replace,
                converge_identical=True,
                force_available=False,
            )
        except research_intake.SuggestionNotFoundError:
            failure = "not_found: no article suggestion has that id."
        except research_intake.ResearchConflictError as err:
            failure = f"conflict: {err}"
        if failure is not None:
            db.rollback()
    finally:
        db.close()
    if failure is not None:
        raise ToolError(failure)
    assert outcome is not None
    return bound_result(
        {
            "accepted": outcome.accepted,
            "action": outcome.action,
            "replayed": outcome.replayed,
            "suggestion_id": outcome.suggestion_id,
            "suggestion_title": outcome.suggestion_title,
            "research_id": outcome.research_id,
            "status": outcome.status,
            "word_count": outcome.word_count,
            "references_generated": outcome.references_generated,
            "checks": outcome.checks,
        }
    )


# --- retry_agent_task / skip_agent_task ----------------------------------------------

_RETRY_AND_SKIP = frozenset({AgentTaskAction.RETRY, AgentTaskAction.SKIP})

# Kinds the MCP retry/skip tools refuse, and which actions (#686, #690). Only the
# admin Agent Tasks queue (``POST .../agent-tasks/{id}/action``) may act on them:
#
# * The operator's wait gates may not be **skipped**. SKIPPED satisfies a
#   dependency exactly like DONE, so skipping one lets the driver run
#   APPROVE_ARTICLE -> SET_PUBLISH_DATE with unresolved claims or no Grammarly
#   pass. Retrying one stays allowed: it only makes the driver re-check the
#   condition now ("check again"), which still has to hold.
# * The check steps that feed those gates may not be **skipped** either (#690).
#   WAIT_ARTICLE_CLAIMS_RESOLVED holds when the article has *no* claims, so a
#   skipped ARTICLE_FACT_CHECK hollows the gate out, and the
#   APPLY_ARTICLE_CORRECTIONS that then 400s ("run a fact-check first") could be
#   skipped too; blog articles are not fact-check-gated at publish. The legacy
#   kinds of goals planned before the wait gates (RESEARCH_FACT_CHECK /
#   APPLY_RESEARCH_CORRECTIONS, GRAMMAR_CHECK_EN / _ES) are the checks
#   themselves. Retrying one stays allowed: it re-runs the check.
# * The publish-path steps may not be **retried or skipped**. Each one approves,
#   publishes or schedules, which over MCP is an admin-approved request (#642),
#   never an action. Verified against every ``KIND_CALLS`` entry: no other kind
#   approves, publishes or schedules (GENERATE_ARTICLE_PROMO / GENERATE_SABIAS_QUE
#   write PENDING drafts, RENDER_IG_POST only renders).
MCP_REFUSED_TASK_ACTIONS: dict[str, frozenset[AgentTaskAction]] = {
    AgentTaskKind.WAIT_RESEARCH_READY.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.WAIT_ARTICLE_CLAIMS_RESOLVED.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_EN.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.WAIT_GRAMMAR_CHECKED_ES.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.RESEARCH_FACT_CHECK.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.APPLY_RESEARCH_CORRECTIONS.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.ARTICLE_FACT_CHECK.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.APPLY_ARTICLE_CORRECTIONS.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.GRAMMAR_CHECK_EN.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.GRAMMAR_CHECK_ES.value: frozenset({AgentTaskAction.SKIP}),
    AgentTaskKind.APPROVE_ARTICLE.value: _RETRY_AND_SKIP,
    AgentTaskKind.SET_PUBLISH_DATE.value: _RETRY_AND_SKIP,
    AgentTaskKind.APPROVE_IG_POST.value: _RETRY_AND_SKIP,
    AgentTaskKind.SCHEDULE_IG_POST.value: _RETRY_AND_SKIP,
}


def mcp_refuses(kind: str, action: AgentTaskAction) -> bool:
    """True when the MCP retry/skip tools refuse ``action`` on a ``kind`` task."""
    return action in MCP_REFUSED_TASK_ACTIONS.get(kind, frozenset())


_SATISFIED = (AgentTaskStatus.DONE.value, AgentTaskStatus.SKIPPED.value)


def _skips_ahead(db, task_id: int) -> bool:
    """True when ``task_id`` is PENDING behind a dependency that has not finished.

    The article chain is linear and a skip promotes what it unblocks, so skipping
    a PENDING row downstream of a FAILED ARTICLE_FACT_CHECK (e.g. its
    GENERATE_SERIES_SECTIONS) would promote APPROVE_ARTICLE past the fact-check
    (#690). Read unlocked: DONE / SKIPPED never revert, so a dependency seen
    satisfied stays satisfied. A dependency outside the goal does not count,
    which keeps the "not promoted" PENDING row explain_stuck_goal reports
    skippable.
    """
    row = (
        db.query(AgentTask.status, AgentTask.goal_key, AgentTask.depends_on)
        .filter(AgentTask.id == task_id)
        .one()
    )
    if row.status != AgentTaskStatus.PENDING.value or not row.depends_on:
        return False
    return (
        db.query(AgentTask.id)
        .filter(
            AgentTask.goal_key == row.goal_key,
            AgentTask.id.in_(row.depends_on),
            AgentTask.status.notin_(_SATISFIED),
        )
        .first()
        is not None
    )


def _refusal_why(kind: str) -> str:
    """What an MCP token may not do to ``kind``, for the ``refused:`` error."""
    if kind.startswith("WAIT_"):
        return "skip an operator wait gate."
    if MCP_REFUSED_TASK_ACTIONS[kind] == {AgentTaskAction.SKIP}:
        return "skip a fact-check, corrections or grammar step."
    return "retry or skip a step that approves, publishes or schedules."


_ACTION_RETURNS = """\
Returns {"task": {"task_id", "goal_key", "kind", "subject_type", "subject_id", "status", \
"attempts"}, "promoted": [task ids moved PENDING -> READY], "replayed": bool, \
"last_action": {"action", "previous_status", "reason", "actor", "at"} | null, \
"truncated"}. Errors: "not_found: ..." (no such task), "conflict: ..." (the task's status \
does not allow the action; the message names the status), "refused: ..." (only the \
operator may do this to the task's kind, from the admin Agent Tasks queue; the message \
names the kind)."""

RETRY_AGENT_TASK_DESCRIPTION = f"""\
Retry one task of Panama In Context's content pipeline (an agent_tasks row). Same \
service as the admin queue's retry action. On a FAILED task it resets attempts to 0 and \
returns it to READY for the deterministic driver; on a WAITING task (blocked on operator \
work) it means "check again now". The reason is stored with your token name on an \
agent_task_actions row.

Use it when explain_stuck_goal suggests retry (a transient error such as a timeout, \
rate limit or 5xx), or after the cause of a failure was fixed. Do not use it on a LEASED \
task (a driver is working on it), to force a step that keeps failing for the same \
reason (investigate instead), or as a substitute for skip. Refused on the publish-path \
kinds APPROVE_ARTICLE, SET_PUBLISH_DATE, APPROVE_IG_POST and SCHEDULE_IG_POST: they \
approve, publish or schedule, which only the operator does (admin Agent Tasks queue).

Arguments: task_id, reason (why, 1-1000 characters).

{_ACTION_RETURNS} Replays converge: a task already READY (the state a retry produces) \
comes back unchanged with replayed true and no new action row. Once the driver has \
claimed or finished it, a repeat is a conflict naming LEASED / DONE, which means your \
first retry took effect.

Example: retry_agent_task(task_id=640, reason="Gemini 429 at 09:12; quota reset") -> \
{{"task": {{"task_id": 640, "kind": "GENERATE_FEATURE_IMAGE", "status": "READY", \
"attempts": 0, ...}}, "promoted": [], "replayed": false, "last_action": {{"action": \
"retry", "previous_status": "FAILED", "reason": "Gemini 429 at 09:12; quota reset", \
"actor": "mcp:supervisor", "at": "2026-10-11T12:00:00+00:00"}}, "truncated": false}}"""

SKIP_AGENT_TASK_DESCRIPTION = f"""\
Skip one task of Panama In Context's content pipeline (an agent_tasks row). Same service \
as the admin queue's skip action. The task becomes SKIPPED, which satisfies its \
dependents exactly like DONE, so the tasks it blocked are promoted to READY. It cannot \
be undone. The reason is stored with your token name on an agent_task_actions row.

Use it when explain_stuck_goal suggests skip (an optional step such as a feature image \
or an Instagram draft that will not succeed), or when the step's work was done by hand. \
Do not use it to get past a required step that simply failed (the dependents would run \
without its output; retry or investigate instead), or on a LEASED task (allowed from \
PENDING, READY, FAILED and WAITING only). Refused on the operator's wait gates \
(WAIT_RESEARCH_READY, WAIT_ARTICLE_CLAIMS_RESOLVED, WAIT_GRAMMAR_CHECKED_EN, \
WAIT_GRAMMAR_CHECKED_ES), on the check steps that feed them (ARTICLE_FACT_CHECK, \
APPLY_ARTICLE_CORRECTIONS, RESEARCH_FACT_CHECK, APPLY_RESEARCH_CORRECTIONS, \
GRAMMAR_CHECK_EN, GRAMMAR_CHECK_ES; retry them instead) and on the publish-path kinds \
(APPROVE_ARTICLE, SET_PUBLISH_DATE, APPROVE_IG_POST, SCHEDULE_IG_POST): skipping them \
would publish without the operator's checks, so only the operator does it (admin Agent \
Tasks queue). Also refused on a PENDING task whose dependencies have not all finished \
(DONE or SKIPPED): skipping it would run the steps after it ahead of the unfinished \
ones; act on the blocking step instead.

Arguments: task_id, reason (why, 1-1000 characters).

{_ACTION_RETURNS} Replays converge: a task already SKIPPED comes back unchanged with \
replayed true and no new action row.

Example: skip_agent_task(task_id=655, reason="Gemini quota exhausted for the week") -> \
{{"task": {{"task_id": 655, "kind": "GENERATE_FEATURE_IMAGE", "status": "SKIPPED", ...}}, \
"promoted": [656], "replayed": false, "last_action": {{"action": "skip", \
"previous_status": "FAILED", "reason": "Gemini quota exhausted for the week", "actor": \
"mcp:supervisor", "at": "..."}}, "truncated": false}}"""


def _action_view(entry: AgentTaskActionLog | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "action": entry.action,
        "previous_status": entry.previous_status,
        "reason": entry.reason,
        "actor": entry.actor,
        "at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _task_view(task: AgentTask) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "goal_key": task.goal_key,
        "kind": task.kind,
        "subject_type": task.subject_type,
        "subject_id": task.subject_id,
        "status": task.status,
        "attempts": task.attempts,
    }


def _task_action(task_id: int, action: AgentTaskAction, reason: str) -> dict[str, Any]:
    failure: str | None = None
    payload: dict[str, Any] | None = None
    db = database.SessionLocal()
    try:
        # The kind column only, not the entity: an AgentTask in the identity map
        # would not be refreshed by apply_task_action's locking read. A kind
        # never changes, so reading it unlocked is safe. A missing task falls
        # through to the service's 404.
        kind = db.query(AgentTask.kind).filter(AgentTask.id == task_id).scalar()
        if kind is not None and mcp_refuses(kind, action):
            # Checked before apply_task_action: nothing is written, no action row.
            failure = (
                f"refused: {action.value} on a {kind} task is reserved for the operator "
                f"(admin Agent Tasks queue); MCP tokens cannot {_refusal_why(kind)}"
            )
            db.rollback()
        elif kind is not None and action == AgentTaskAction.SKIP and _skips_ahead(db, task_id):
            failure = (
                f"refused: skip on a PENDING {kind} task whose dependencies have not "
                "finished is reserved for the operator (admin Agent Tasks queue); it would "
                "promote the steps after it past the unfinished ones. Retry or skip the "
                "blocking step instead."
            )
            db.rollback()
        else:
            try:
                outcome = apply_task_action(
                    db, task_id, action.value, reason=reason, actor=_caller()
                )
            except HTTPException as err:
                prefix = "not_found" if err.status_code == 404 else "conflict"
                failure = f"{prefix}: {err.detail}"
                db.rollback()
            else:
                payload = {
                    "task": _task_view(outcome.task),
                    "promoted": outcome.promoted,
                    "replayed": outcome.replayed,
                    "last_action": _action_view(outcome.last_action),
                }
    finally:
        db.close()
    if failure is not None:
        raise ToolError(failure)
    assert payload is not None
    return bound_result(payload)


Reason = Annotated[str, Field(min_length=1, max_length=MAX_ACTION_REASON_CHARS)]


def retry_agent_task(task_id: Annotated[int, Field(ge=1)], reason: Reason) -> dict[str, Any]:
    return _task_action(task_id, AgentTaskAction.RETRY, reason)


def skip_agent_task(task_id: Annotated[int, Field(ge=1)], reason: Reason) -> dict[str, Any]:
    return _task_action(task_id, AgentTaskAction.SKIP, reason)


__all__ = [
    "GET_DEEP_RESEARCH_BATCH_DESCRIPTION",
    "MCP_REFUSED_TASK_ACTIONS",
    "RETRY_AGENT_TASK_DESCRIPTION",
    "SKIP_AGENT_TASK_DESCRIPTION",
    "SUBMIT_FACT_CHECK_VERDICTS_DESCRIPTION",
    "SUBMIT_RESEARCH_DESCRIPTION",
    "get_deep_research_batch",
    "mcp_refuses",
    "retry_agent_task",
    "skip_agent_task",
    "submit_fact_check_verdicts",
    "submit_research",
]
