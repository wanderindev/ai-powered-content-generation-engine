"""Audit log of MCP tool calls (#639, epic #620).

``app.mcp.audit.audit_tool`` (applied to every tool by ``pic_tool``) writes
one row per tool call, including calls refused for a missing scope:

* ``token_id`` / ``token_name`` identify the caller. ``token_name`` is a
  snapshot, so the log reads on its own.
* ``args_redacted`` is the call's arguments after
  ``app.mcp.audit.redact_args``: strings longer than ``MAX_INLINE_STRING``
  become ``{"len": n, "sha256": hex}`` and credential-named keys are
  dropped, so no research or article body is ever stored verbatim.
* ``outcome`` is ``OK`` / ``ERROR`` / ``DENIED``; ``error_class`` is the
  exception's class name on ``ERROR`` / ``DENIED``. The exception *message*
  is not stored (it can quote the arguments).

Calls the SDK rejects before the tool function runs (unknown tool, arguments
that fail the input schema) are not logged: the wrapper never sees them.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import McpCallOutcome, McpScope, status_check

__all__ = ["McpToolCall", "McpCallOutcome"]


class McpToolCall(Base):
    __tablename__ = "mcp_tool_calls"
    __table_args__ = (
        status_check("mcp_tool_calls", "outcome", McpCallOutcome),
        status_check("mcp_tool_calls", "required_scope", McpScope),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Nullable: a call reaches a tool only through an authenticated token, but
    # the row must still be writable if the token context is missing.
    token_id: Mapped[int | None] = mapped_column(ForeignKey("mcp_tokens.id"), index=True)
    token_name: Mapped[str | None] = mapped_column(String(200))
    tool: Mapped[str] = mapped_column(String(100), index=True)
    required_scope: Mapped[str] = mapped_column(String(20))
    args_redacted: Mapped[dict | None] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(String(10))
    error_class: Mapped[str | None] = mapped_column(String(200))
    duration_ms: Mapped[int] = mapped_column(Integer)
