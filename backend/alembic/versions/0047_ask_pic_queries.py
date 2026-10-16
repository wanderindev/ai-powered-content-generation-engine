"""ask_pic_queries: Ask PIC abuse / cost ledger (#634, epic #619).

Purely additive: one new table, two indexes, two CHECKs, no data writes.
Nothing writes it while ``ASK_PIC_ENABLED`` is off (the default), and the
answer endpoint that reads it (#635) ships after this migration.

* ``status`` is String + named CHECK (#24) over ``AskPicQueryStatus``; NULL
  means "admitted, still in flight".
* ``language`` is String + named CHECK over ``Language`` (nullable).
* ``counts_toward_limits`` is false only for a request refused at admission.
* ``(ip_hash, created_at)`` serves the per-IP hourly count; ``created_at``
  serves the global daily count / cost sum and the retention prune.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0047_ask_pic_queries"
down_revision = "0046_consistency_findings"
branch_labels = None
depends_on = None

_TABLE = "ask_pic_queries"
_STATUSES = (
    "ANSWERED",
    "NOT_COVERED",
    "REFUSED",
    "RATE_LIMITED",
    "BUDGET_EXCEEDED",
    "INVALID",
    "ERROR",
)
_LANGUAGES = ("en", "es")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


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
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("language", sa.String(2), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("cited_article_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("counts_toward_limits", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("retrieval_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True),
        sa.Column("embed_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_ask_pic_queries_status"),
        sa.CheckConstraint(_in("language", _LANGUAGES), name="ck_ask_pic_queries_language"),
    )
    op.create_index("ix_ask_pic_queries_created_at", _TABLE, ["created_at"])
    op.create_index("ix_ask_pic_queries_ip_hash_created_at", _TABLE, ["ip_hash", "created_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
