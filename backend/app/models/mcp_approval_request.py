"""Approval requests created by MCP ``publish_request`` tools (#642, epic #620).

An MCP agent can *ask* to publish, unpublish or approve an article; it can
never do it. ``request_publish`` / ``request_unpublish`` /
``request_article_approval`` insert a ``PENDING`` row here, and only an admin
(``POST /admin/dashboard/mcp/approvals/{id}/approve``) runs the action, through
the same ``app.services.article_publishing`` function as the admin routes.

* ``action`` (``McpApprovalAction``) and ``status`` (``McpApprovalStatus``)
  are ``String`` + named CHECKs.
* ``params`` holds the action's arguments (``{"publish_at": iso | null}``
  for a publish, ``{}`` otherwise); ``rationale`` is the agent's free text.
* ``requested_by_token_id`` / ``requested_by`` identify the MCP token (the
  name is a snapshot, like ``mcp_tool_calls.token_name``).
* ``decided_by`` is the admin email, ``decided_at`` the decision time and
  ``result`` the outcome (``{"published_at", "status"}`` or
  ``{"error": reason}``).
* A partial unique index allows one ``PENDING`` row per (article, action),
  which is what makes a replayed request converge under concurrency.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import McpApprovalAction, McpApprovalStatus, status_check

__all__ = ["McpApprovalRequest", "McpApprovalAction", "McpApprovalStatus"]


class McpApprovalRequest(Base):
    __tablename__ = "mcp_approval_requests"
    __table_args__ = (
        status_check("mcp_approval_requests", "action", McpApprovalAction),
        status_check("mcp_approval_requests", "status", McpApprovalStatus),
        Index(
            "uq_mcp_approval_requests_pending",
            "article_id",
            "action",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(20))
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str] = mapped_column(Text)
    requested_by_token_id: Mapped[int | None] = mapped_column(ForeignKey("mcp_tokens.id"))
    requested_by: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(20), default=McpApprovalStatus.PENDING.value, index=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(254))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
