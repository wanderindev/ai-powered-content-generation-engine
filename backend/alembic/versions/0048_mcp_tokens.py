"""mcp_tokens: scoped bearer tokens for the MCP server (#638, epic #620).

Purely additive: one new table, one CHECK, no data writes. Nothing reads it
until a token is minted with ``python -m scripts.mcp_tokens create``.

* ``token_hash`` is the SHA-256 hex digest of the token (the plaintext is
  shown once and never stored); unique.
* ``scopes`` is ``varchar[]`` + named CHECK ``ck_mcp_tokens_scopes`` (every
  element in ``McpScope``), the array form of the #24 convention.
* ``revoked_at`` revokes; rows are never deleted.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0048_mcp_tokens"
down_revision = "0047_ask_pic_queries"
branch_labels = None
depends_on = None

_TABLE = "mcp_tokens"
_CONSTRAINT = "ck_mcp_tokens_scopes"
_SCOPES = ("read", "pipeline", "publish_request")


def upgrade() -> None:
    allowed = ", ".join(f"'{v}'" for v in _SCOPES)
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", postgresql.ARRAY(sa.String(20)), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"scopes <@ ARRAY[{allowed}]::varchar[]", name=_CONSTRAINT),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
