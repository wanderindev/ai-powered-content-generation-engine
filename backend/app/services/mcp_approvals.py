"""MCP approval requests: create, decide, execute (#642, epic #620).

The MCP ``publish_request`` tools call :func:`create_request` and nothing
else: a request never executes. Only :func:`decide` (behind the admin-only
``POST /admin/dashboard/mcp/approvals/{id}/approve``) runs the action, through
the same ``app.services.article_publishing`` functions as the admin routes,
so every publish precondition still applies at decision time.

* **Nothing half-applies.** The request row is locked ``FOR UPDATE``; the
  action runs inside a savepoint; an expected failure (article gone, a
  precondition, an invalid transition, a slug collision) rolls the savepoint
  back and records ``FAILED`` with the reason; the article change and the
  ``EXECUTED`` / ``FAILED`` status are committed together. An unexpected
  error propagates and the request stays ``PENDING``.
* **Executes once.** The lock serialises two admins approving at once; the
  second sees the decided row and gets it back unchanged (``replayed``).
* **Converges on replay (#495).** Asking for the same action on the same
  article while a request is ``PENDING`` returns that request (a partial
  unique index backs this under concurrency); different parameters are a
  conflict naming the pending request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.article import Article
from app.models.enums import ContentStatus, McpApprovalAction, McpApprovalStatus
from app.models.mcp_approval_request import McpApprovalRequest
from app.services import article_publishing
from app.services.article_slugs import SlugCollisionError

# Agents may explain themselves, but a rationale is not an essay.
MAX_RATIONALE_CHARS = 2000

# Failures recorded as FAILED; anything else propagates (request stays PENDING).
_EXPECTED_FAILURES = (
    article_publishing.ArticleNotFoundError,
    article_publishing.PublishPreconditionError,
    article_publishing.InvalidStatusTransitionError,
    SlugCollisionError,
)


class ApprovalNotFoundError(LookupError):
    """No approval request (or no article) has the given id."""


class ApprovalConflictError(ValueError):
    """The request cannot be created or decided as asked (message says why)."""


@dataclass
class ApprovalOutcome:
    request: McpApprovalRequest
    replayed: bool


def _same_params(a: dict | None, b: dict | None) -> bool:
    return (a or {}) == (b or {})


def _pending(db: Session, article_id: int, action: str) -> McpApprovalRequest | None:
    return (
        db.query(McpApprovalRequest)
        .filter(
            McpApprovalRequest.article_id == article_id,
            McpApprovalRequest.action == action,
            McpApprovalRequest.status == McpApprovalStatus.PENDING.value,
        )
        .first()
    )


def create_request(
    db: Session,
    *,
    action: McpApprovalAction,
    article_id: int,
    params: dict[str, Any],
    rationale: str,
    token_id: int | None,
    token_name: str | None,
) -> ApprovalOutcome:
    """Insert a PENDING request, or return the PENDING one already there.

    Refuses (``ApprovalConflictError``) a request that could only fail or do
    nothing: approving an APPROVED article, unpublishing an unpublished one,
    or a second pending request with different parameters. Commits.
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if article is None:
        raise ApprovalNotFoundError("no article has that id.")

    existing = _pending(db, article_id, action.value)
    if existing is not None:
        if _same_params(existing.params, params):
            return ApprovalOutcome(existing, replayed=True)
        raise ApprovalConflictError(
            f"approval request {existing.id} for this article and action is already "
            "PENDING with different parameters; wait for the decision "
            "(get_approval_status) before asking again."
        )

    if action is McpApprovalAction.APPROVE_ARTICLE and article.status == (
        ContentStatus.APPROVED.value
    ):
        raise ApprovalConflictError("the article is already APPROVED.")
    if action is McpApprovalAction.UNPUBLISH and article.published_at is None:
        raise ApprovalConflictError("the article is not published (no publish date).")

    request = McpApprovalRequest(
        action=action.value,
        article_id=article_id,
        params=params,
        rationale=rationale,
        requested_by_token_id=token_id,
        requested_by=token_name,
        status=McpApprovalStatus.PENDING.value,
    )
    db.add(request)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent identical request won the partial unique index.
        db.rollback()
        existing = _pending(db, article_id, action.value)
        if existing is None or not _same_params(existing.params, params):
            raise
        return ApprovalOutcome(existing, replayed=True)
    db.refresh(request)
    return ApprovalOutcome(request, replayed=False)


def publish_blockers(db: Session, article_id: int) -> list[str]:
    """The article's unmet publish preconditions right now (informational)."""
    article = (
        db.query(Article)
        .options(joinedload(Article.series_children))
        .filter(Article.id == article_id)
        .first()
    )
    return article_publishing.publish_blockers(article) if article is not None else []


def _execute(db: Session, request: McpApprovalRequest) -> dict[str, Any]:
    """Run the request's action through the shared publishing service."""
    action = McpApprovalAction(request.action)
    if action is McpApprovalAction.PUBLISH:
        raw = (request.params or {}).get("publish_at")
        when = datetime.fromisoformat(raw) if raw else None
        article = article_publishing.publish_article(db, request.article_id, when)
    elif action is McpApprovalAction.UNPUBLISH:
        article = article_publishing.unpublish_article(db, request.article_id)
    else:
        article = article_publishing.approve_article(db, request.article_id)
    db.flush()
    return {
        "article_status": article.status,
        "published_at": article.published_at.isoformat() if article.published_at else None,
    }


def decide(db: Session, request_id: int, *, approve: bool, admin_email: str) -> ApprovalOutcome:
    """Approve (execute) or reject a request, as ``admin_email``. Commits.

    Replays converge: approving an EXECUTED / FAILED request, or rejecting a
    REJECTED one, returns it unchanged. The opposite decision on a decided
    request is an ``ApprovalConflictError``.
    """
    request = (
        db.query(McpApprovalRequest)
        .filter(McpApprovalRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if request is None:
        raise ApprovalNotFoundError("Approval request not found")

    if request.status != McpApprovalStatus.PENDING.value:
        approved_states = {McpApprovalStatus.EXECUTED.value, McpApprovalStatus.FAILED.value}
        same = (
            (request.status in approved_states)
            if approve
            else (request.status == McpApprovalStatus.REJECTED.value)
        )
        db.rollback()  # release the row lock
        if same:
            return ApprovalOutcome(request, replayed=True)
        raise ApprovalConflictError(f"Approval request is already {request.status}")

    failure: str | None = None
    result: dict[str, Any] | None = None
    if approve:
        try:
            with db.begin_nested():
                result = _execute(db, request)
        except _EXPECTED_FAILURES as err:
            failure = str(err) or err.__class__.__name__
        if failure is not None:
            request.status = McpApprovalStatus.FAILED.value
            request.result = {"error": failure}
        else:
            request.status = McpApprovalStatus.EXECUTED.value
            request.result = result
    else:
        request.status = McpApprovalStatus.REJECTED.value

    request.decided_by = admin_email
    request.decided_at = datetime.now(UTC)
    db.commit()
    db.refresh(request)
    return ApprovalOutcome(request, replayed=False)


def request_view(request: McpApprovalRequest, article: Article | None = None) -> dict[str, Any]:
    """The JSON shape shared by the MCP tools and the admin API."""
    return {
        "request_id": request.id,
        "action": request.action,
        "article_id": request.article_id,
        "article_title": article.title if article is not None else None,
        "status": request.status,
        "params": request.params or {},
        "rationale": request.rationale,
        "requested_by": request.requested_by,
        "created_at": request.created_at.isoformat() if request.created_at else None,
        "decided_by": request.decided_by,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "result": request.result,
    }
