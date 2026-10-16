"""Admin read surface for the MCP tool-call audit log (#639, epic #620).

``GET /admin/dashboard/mcp/calls`` lists ``mcp_tool_calls`` rows newest
first, filtered by token (name or id), tool and outcome. Admin-gated like the
rest of ``/admin/dashboard``, and deliberately **not** on
``DRIVER_ALLOWED_CALLS``: no driver task needs it, and the log of what every
agent token did is operator oversight, not pipeline input.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_or_allowlisted_driver_token
from app.core.database import get_db
from app.models.enums import McpCallOutcome
from app.models.mcp_tool_call import McpToolCall
from app.schemas.mcp import McpToolCallItem, McpToolCallList

router = APIRouter(
    prefix="/admin/dashboard/mcp",
    tags=["mcp"],
    dependencies=[Depends(validate_admin_or_allowlisted_driver_token)],
)


@router.get("/calls", response_model=McpToolCallList)
def list_mcp_tool_calls(
    token: str | None = Query(None, max_length=200, description="Token name (exact)"),
    token_id: int | None = Query(None, ge=1),
    tool: str | None = Query(None, max_length=100),
    outcome: McpCallOutcome | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List MCP tool calls, newest first. Arguments are stored redacted."""
    query = db.query(McpToolCall)
    if token is not None:
        query = query.filter(McpToolCall.token_name == token)
    if token_id is not None:
        query = query.filter(McpToolCall.token_id == token_id)
    if tool is not None:
        query = query.filter(McpToolCall.tool == tool)
    if outcome is not None:
        query = query.filter(McpToolCall.outcome == outcome.value)
    total = query.count()
    rows = query.order_by(McpToolCall.id.desc()).offset(offset).limit(limit).all()
    return McpToolCallList(
        items=[McpToolCallItem.model_validate(row) for row in rows],
        total=total,
    )
