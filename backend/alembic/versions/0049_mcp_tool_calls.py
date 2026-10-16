"""mcp_tool_calls: audit log of MCP tool calls (#639, epic #620).

Purely additive: one new table, two CHECKs, three indexes, no data writes.
Rows are written by ``app.mcp.audit`` for every tool call (OK / ERROR / DENIED).

* ``outcome`` is ``String(10)`` + named CHECK ``ck_mcp_tool_calls_outcome``
  over ``McpCallOutcome``.
* ``required_scope`` is ``String(20)`` + named CHECK
  ``ck_mcp_tool_calls_required_scope`` over ``McpScope`` -- a new scope is a
  drop-and-recreate here as well as on the ``mcp_tokens.scopes`` CHECK.
* ``args_redacted`` is JSONB (long strings hashed, credential keys dropped).
* ``token_id`` references ``mcp_tokens`` (rows there are never deleted).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0049_mcp_tool_calls"
down_revision = "0048_mcp_tokens"
branch_labels = None
depends_on = None

_TABLE = "mcp_tool_calls"
_OUTCOME_CONSTRAINT = "ck_mcp_tool_calls_outcome"
_SCOPE_CONSTRAINT = "ck_mcp_tool_calls_required_scope"
_OUTCOMES = ("OK", "ERROR", "DENIED")
_SCOPES = ("read", "pipeline", "publish_request")


def _in(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("mcp_tokens.id"), nullable=True),
        sa.Column("token_name", sa.String(200), nullable=True),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("required_scope", sa.String(20), nullable=False),
        sa.Column("args_redacted", postgresql.JSONB(), nullable=True),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("error_class", sa.String(200), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(_in("outcome", _OUTCOMES), name=_OUTCOME_CONSTRAINT),
        sa.CheckConstraint(_in("required_scope", _SCOPES), name=_SCOPE_CONSTRAINT),
    )
    op.create_index("ix_mcp_tool_calls_created_at", _TABLE, ["created_at"])
    op.create_index("ix_mcp_tool_calls_token_id", _TABLE, ["token_id"])
    op.create_index("ix_mcp_tool_calls_tool", _TABLE, ["tool"])


def downgrade() -> None:
    op.drop_table(_TABLE)
