"""Admin schemas for the MCP tool-call audit log (#639, epic #620)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class McpToolCallItem(BaseModel):
    """One ``mcp_tool_calls`` row (arguments already redacted at write time)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    token_id: int | None = None
    token_name: str | None = None
    tool: str
    required_scope: str
    args_redacted: dict[str, Any] | None = None
    outcome: str
    error_class: str | None = None
    duration_ms: int


class McpToolCallList(BaseModel):
    """A page of audit rows, newest first, plus the filtered total."""

    items: list[McpToolCallItem]
    total: int


class McpApprovalItem(BaseModel):
    """One ``mcp_approval_requests`` row, as the admin Approvals page shows it (#642)."""

    request_id: int
    action: str
    article_id: int
    article_title: str | None = None
    status: str
    params: dict[str, Any]
    rationale: str
    requested_by: str | None = None
    created_at: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    result: dict[str, Any] | None = None
    replayed: bool = False


class McpApprovalList(BaseModel):
    """A page of approval requests, newest first, plus the filtered total."""

    items: list[McpApprovalItem]
    total: int
