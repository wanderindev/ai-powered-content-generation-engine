"""MCP approval-gate tools, ``publish_request`` scope (#642).

``request_publish`` / ``request_unpublish`` / ``request_article_approval``
**never execute**: each inserts (or, on replay, returns) a PENDING
``mcp_approval_requests`` row through ``mcp_approvals.create_request``. The
action runs only when an admin approves it in the admin UI
(``POST /admin/dashboard/mcp/approvals/{id}/approve``), through the same
``article_publishing`` functions as ``PATCH .../publish`` / ``.../unpublish``
/ ``.../status``. ``get_approval_status`` reads one request.

Each tool opens its own ``SessionLocal()`` and closes it before returning.
Errors are ``ToolError``s raised outside any ``except`` block and never
quote the caller's rationale.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AwareDatetime, Field

from app.core import database
from app.mcp.audit import _token_identity
from app.mcp.tools.bounds import bound_result
from app.models.article import Article
from app.models.enums import McpApprovalAction
from app.models.mcp_approval_request import McpApprovalRequest
from app.services import mcp_approvals

Rationale = Annotated[str, Field(min_length=1, max_length=mcp_approvals.MAX_RATIONALE_CHARS)]
ArticleId = Annotated[int, Field(ge=1)]

_RETURNS = """\
Returns {"request_id": int, "action": "PUBLISH" | "UNPUBLISH" | "APPROVE_ARTICLE", \
"article_id", "article_title", "status": "PENDING" | "REJECTED" | "EXECUTED" | "FAILED", \
"params": {...}, "rationale", "requested_by": token name, "created_at", "decided_by": \
admin email | null, "decided_at" | null, "result": {"article_status", "published_at"} | \
{"error": reason} | null, "replayed": bool, "publish_blockers": [str], "truncated"}. \
publish_blockers lists the article's currently unmet publish preconditions (empty when \
none); the admin's approval re-checks them. Replays converge: asking again while your \
request is PENDING returns the same request with replayed true. Errors: "not_found: \
...", "conflict: ..." (a PENDING request with different parameters, or an action that \
would do nothing)."""

REQUEST_PUBLISH_DESCRIPTION = f"""\
Ask the Panama In Context operator to publish one blog article. This does NOT publish: \
it files an approval request that an admin approves or rejects in the admin UI; only \
then does the publish run, with every publish precondition checked at that moment \
(article APPROVED, Spanish content, a feature image, and for a series article its \
About section). If one fails, the request ends FAILED with the reason.

Use it when an article is ready to go live, or to move its publish date. Do not use it \
to approve a draft (use request_article_approval first), and do not poll it in a loop; \
check later with get_approval_status.

Arguments: article_id; rationale (why, 1-2000 characters, shown to the admin); \
publish_at (optional ISO-8601 datetime with a timezone; omitted, the server picks the \
article's existing date or the series' next free 12:00 UTC slot).

{_RETURNS}

Example: request_publish(article_id=251, rationale="Fact check resolved, ES reviewed") -> \
{{"request_id": 7, "action": "PUBLISH", "article_id": 251, "status": "PENDING", \
"params": {{"publish_at": null}}, "publish_blockers": [], "replayed": false, ...}}"""

REQUEST_UNPUBLISH_DESCRIPTION = f"""\
Ask the Panama In Context operator to unpublish one blog article (clear its publish \
date, which also unschedules its derived Instagram promo). This does NOT unpublish: it \
files an approval request that an admin approves or rejects in the admin UI.

Use it when a live or scheduled article must come down (a factual error found after \
publishing, a duplicate). Do not use it to change a publish date (use request_publish \
with publish_at), or for an article that has no publish date (that is a conflict).

Arguments: article_id; rationale (why, 1-2000 characters, shown to the admin).

{_RETURNS}

Example: request_unpublish(article_id=198, rationale="Claim 4512 judged INACCURATE") -> \
{{"request_id": 8, "action": "UNPUBLISH", "article_id": 198, "status": "PENDING", \
"params": {{}}, "replayed": false, ...}}"""

REQUEST_ARTICLE_APPROVAL_DESCRIPTION = f"""\
Ask the Panama In Context operator to approve one blog article (status PENDING or \
REJECTED -> APPROVED). This does NOT approve: it files an approval request that an \
admin approves or rejects in the admin UI. Approval does not publish; publishing is a \
separate request_publish.

Use it when a drafted article has passed its pipeline checks and needs the operator's \
sign-off. Do not use it on an article that is already APPROVED (that is a conflict) or \
as a way to publish.

Arguments: article_id; rationale (why, 1-2000 characters, shown to the admin).

{_RETURNS}

Example: request_article_approval(article_id=260, rationale="Grammar + ES accuracy \
done, no open claims") -> {{"request_id": 9, "action": "APPROVE_ARTICLE", \
"article_id": 260, "status": "PENDING", "params": {{}}, "replayed": false, ...}}"""

GET_APPROVAL_STATUS_DESCRIPTION = """\
Read one approval request filed by request_publish, request_unpublish or \
request_article_approval: whether the operator has decided it and what happened.

Use it to follow up on a request you filed earlier (for example once per pipeline \
cycle). Do not use it to list requests or to look up an article (use get_article). It \
changes nothing.

Arguments: request_id.

Returns the same shape as the request tools, without replayed: status PENDING (not \
decided yet), REJECTED (the admin declined; nothing ran), EXECUTED (the action ran; \
result has the article's status and publish date) or FAILED (the admin approved but a \
precondition failed; result.error says which, and nothing changed). Error: \
"not_found: ...".

Example: get_approval_status(request_id=7) -> {"request_id": 7, "action": "PUBLISH", \
"status": "EXECUTED", "decided_by": "operator@example.com", "result": \
{"article_status": "APPROVED", "published_at": "2026-10-14T12:00:00+00:00"}, ...}"""


def _view(db, request: McpApprovalRequest, *, replayed: bool | None) -> dict[str, Any]:
    article = db.get(Article, request.article_id)
    view = mcp_approvals.request_view(request, article)
    view["publish_blockers"] = mcp_approvals.publish_blockers(db, request.article_id)
    if replayed is not None:
        view["replayed"] = replayed
    return bound_result(view)


def _request(action: McpApprovalAction, article_id: int, params: dict, rationale: str):
    token_id, token_name = _token_identity(get_access_token())
    failure: str | None = None
    payload: dict[str, Any] | None = None
    db = database.SessionLocal()
    try:
        try:
            outcome = mcp_approvals.create_request(
                db,
                action=action,
                article_id=article_id,
                params=params,
                rationale=rationale,
                token_id=token_id,
                token_name=token_name,
            )
        except mcp_approvals.ApprovalNotFoundError as err:
            failure = f"not_found: {err}"
        except mcp_approvals.ApprovalConflictError as err:
            failure = f"conflict: {err}"
        else:
            payload = _view(db, outcome.request, replayed=outcome.replayed)
        if failure is not None:
            db.rollback()
    finally:
        db.close()
    if failure is not None:
        raise ToolError(failure)
    assert payload is not None
    return payload


def request_publish(
    article_id: ArticleId,
    rationale: Rationale,
    publish_at: AwareDatetime | None = None,
) -> dict[str, Any]:
    params = {"publish_at": publish_at.isoformat() if publish_at is not None else None}
    return _request(McpApprovalAction.PUBLISH, article_id, params, rationale)


def request_unpublish(article_id: ArticleId, rationale: Rationale) -> dict[str, Any]:
    return _request(McpApprovalAction.UNPUBLISH, article_id, {}, rationale)


def request_article_approval(article_id: ArticleId, rationale: Rationale) -> dict[str, Any]:
    return _request(McpApprovalAction.APPROVE_ARTICLE, article_id, {}, rationale)


def get_approval_status(request_id: Annotated[int, Field(ge=1)]) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    db = database.SessionLocal()
    try:
        request = db.get(McpApprovalRequest, request_id)
        if request is not None:
            payload = _view(db, request, replayed=None)
    finally:
        db.close()
    if payload is None:
        raise ToolError("not_found: no approval request has that id.")
    return payload


__all__ = [
    "GET_APPROVAL_STATUS_DESCRIPTION",
    "REQUEST_ARTICLE_APPROVAL_DESCRIPTION",
    "REQUEST_PUBLISH_DESCRIPTION",
    "REQUEST_UNPUBLISH_DESCRIPTION",
    "get_approval_status",
    "request_article_approval",
    "request_publish",
    "request_unpublish",
]
