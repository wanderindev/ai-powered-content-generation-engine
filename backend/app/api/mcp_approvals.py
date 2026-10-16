"""Admin decisions on MCP approval requests (#642, epic #620).

MCP ``publish_request`` tools only *file* publish / unpublish / approve
requests; these endpoints are where an admin runs or declines them:

* ``GET  /admin/dashboard/mcp/approvals`` — list (``status`` filter, newest first).
* ``POST /admin/dashboard/mcp/approvals/{id}/approve`` — execute through
  ``article_publishing`` (the same functions as ``PATCH .../publish``,
  ``.../unpublish`` and ``.../status``); a failed precondition is recorded as
  ``FAILED`` with the reason, and the response is still 200.
* ``POST /admin/dashboard/mcp/approvals/{id}/reject``.

Gated by plain ``validate_admin_token`` — **not** the driver-allowlisting
dependency — so the agent-driver token can never reach them whatever
``DRIVER_ALLOWED_CALLS`` says. ``decided_by`` is the admin's email.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_token
from app.core.database import get_db
from app.models.article import Article
from app.models.enums import McpApprovalStatus
from app.models.magic_link import MagicLink
from app.models.mcp_approval_request import McpApprovalRequest
from app.schemas.mcp import McpApprovalItem, McpApprovalList
from app.services import mcp_approvals

router = APIRouter(
    prefix="/admin/dashboard/mcp/approvals",
    tags=["mcp"],
    dependencies=[Depends(validate_admin_token)],
)


def _item(db: Session, request: McpApprovalRequest, *, replayed: bool = False) -> McpApprovalItem:
    article = db.get(Article, request.article_id)
    return McpApprovalItem(**mcp_approvals.request_view(request, article), replayed=replayed)


@router.get("", response_model=McpApprovalList)
def list_approval_requests(
    status: McpApprovalStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List MCP approval requests, newest first (``status=PENDING`` for the queue)."""
    query = db.query(McpApprovalRequest)
    if status is not None:
        query = query.filter(McpApprovalRequest.status == status.value)
    total = query.count()
    rows = query.order_by(McpApprovalRequest.id.desc()).offset(offset).limit(limit).all()
    return McpApprovalList(items=[_item(db, row) for row in rows], total=total)


def _decide(db: Session, request_id: int, approve: bool, admin_email: str) -> McpApprovalItem:
    try:
        outcome = mcp_approvals.decide(db, request_id, approve=approve, admin_email=admin_email)
    except mcp_approvals.ApprovalNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None
    except mcp_approvals.ApprovalConflictError as err:
        raise HTTPException(status_code=409, detail=str(err)) from None
    return _item(db, outcome.request, replayed=outcome.replayed)


@router.post("/{request_id}/approve", response_model=McpApprovalItem)
def approve_request(
    request_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """Approve and execute a request; the result is EXECUTED or FAILED (with reason)."""
    return _decide(db, request_id, True, magic_link.admin_email)


@router.post("/{request_id}/reject", response_model=McpApprovalItem)
def reject_request(
    request_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    magic_link: MagicLink = Depends(validate_admin_token),
):
    """Reject a request; nothing runs."""
    return _decide(db, request_id, False, magic_link.admin_email)
