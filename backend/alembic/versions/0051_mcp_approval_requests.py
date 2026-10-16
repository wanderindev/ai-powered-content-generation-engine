"""mcp_approval_requests: publish / unpublish / approve requests from MCP (#642).

Purely additive: one new table, two CHECKs, three indexes, no data writes.
Rows are inserted by the MCP ``publish_request`` tools (``request_publish``,
``request_unpublish``, ``request_article_approval``) and decided by an admin
at ``POST /admin/dashboard/mcp/approvals/{id}/approve|reject``.

* ``action`` is ``String(20)`` + named CHECK ``ck_mcp_approval_requests_action``
  over ``McpApprovalAction``.
* ``status`` is ``String(20)`` + named CHECK ``ck_mcp_approval_requests_status``
  over ``McpApprovalStatus``.
* ``uq_mcp_approval_requests_pending`` is a partial unique index: one PENDING
  request per (article, action), so a replayed request converges.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0051_mcp_approval_requests"
down_revision = "0050_agent_task_actions"
branch_labels = None
depends_on = None

_TABLE = "mcp_approval_requests"
_ACTION_CONSTRAINT = "ck_mcp_approval_requests_action"
_STATUS_CONSTRAINT = "ck_mcp_approval_requests_status"
_ACTIONS = ("PUBLISH", "UNPUBLISH", "APPROVE_ARTICLE")
_STATUSES = ("PENDING", "REJECTED", "EXECUTED", "FAILED")


def _in(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "requested_by_token_id",
            sa.Integer(),
            sa.ForeignKey("mcp_tokens.id"),
            nullable=True,
        ),
        sa.Column("requested_by", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by", sa.String(254), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_in("action", _ACTIONS), name=_ACTION_CONSTRAINT),
        sa.CheckConstraint(_in("status", _STATUSES), name=_STATUS_CONSTRAINT),
    )
    op.create_index("ix_mcp_approval_requests_article_id", _TABLE, ["article_id"])
    op.create_index("ix_mcp_approval_requests_status", _TABLE, ["status"])
    op.create_index(
        "uq_mcp_approval_requests_pending",
        _TABLE,
        ["article_id", "action"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
