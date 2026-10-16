"""Bearer tokens for the PIC MCP server (#638, epic #620).

PIC is an MCP *resource server*: a client presents ``Authorization: Bearer
<token>`` on ``/mcp`` and ``app.mcp.auth.McpTokenVerifier`` looks up the row
whose ``token_hash`` is the token's SHA-256 hex digest. The plaintext is
printed once by ``python -m scripts.mcp_tokens create`` and never stored.

* ``scopes`` is an array over ``McpScope`` (CHECK ``ck_mcp_tokens_scopes``).
* ``revoked_at`` set = the token is dead (rows are never deleted, so the
  ``mcp_tool_calls`` audit log, #639, keeps a stable ``token_id``).
* ``last_used_at`` is stamped on successful verification, at most once a
  minute per token (see ``app.mcp.auth``).

This table is separate from ``magic_links`` / ``admin_emails`` (admin) and
``agent_driver_token`` (driver): an MCP token can never reach those surfaces.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import McpScope, array_subset_check

__all__ = ["McpToken", "McpScope"]


class McpToken(Base):
    __tablename__ = "mcp_tokens"
    __table_args__ = (array_subset_check("mcp_tokens", "scopes", McpScope),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
