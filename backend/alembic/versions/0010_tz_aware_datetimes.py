"""Make all datetime columns timezone-aware (#26).

Revision ID: 0010_tz_aware_datetimes
Revises: 0009_fk_indexes_and_widths
Create Date: 2026-05-31

The codebase had two competing datetime conventions: a handful of models
(``article``, ``category``, ``tag``, ``media``, ``research``, ``user``,
``article_suggestion``, ``media_candidate``) declared ``DateTime(timezone=True)``
(stored ``timestamptz``), while the rest used the SQLAlchemy default
(``timestamp without time zone``). This unifies on **timezone-aware everywhere**
(the convention is now documented in ``CLAUDE.md``).

This migration converts the remaining naive ``timestamp`` columns to
``timestamptz``. Existing values were written as UTC (``datetime.utcnow()`` /
``func.now()`` under a GMT database session — verified ``SHOW timezone`` = GMT on
prod), so the ``USING col AT TIME ZONE 'UTC'`` clause reinterprets them as UTC
with **no clock shift**. The companion application change replaces naive
``datetime.utcnow()`` with aware ``datetime.now(UTC)`` so reads/writes/comparisons
stay timezone-consistent.

The downgrade is the exact inverse (``timestamptz`` → naive ``timestamp`` taking
the UTC wall-clock), so the round-trip is lossless for UTC-stored data.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_tz_aware_datetimes"
down_revision = "0009_fk_indexes_and_widths"
branch_labels = None
depends_on = None

# table -> naive datetime columns to convert. The already-tz-aware models
# (articles, categories, tags, media, research, users, article_suggestions,
# media_candidates) are intentionally absent — they are already timestamptz.
_TZ_COLUMNS = {
    "bookings": ["created_at", "updated_at"],
    "booking_status_log": ["created_at"],
    "orders": [
        "payment_link_sent_at",
        "payment_link_expires_at",
        "paid_at",
        "reservation_expires_at",
        "followup_1_sent_at",
        "followup_2_sent_at",
        "followup_3_sent_at",
        "created_at",
        "updated_at",
    ],
    "tours": ["created_at", "updated_at"],
    "educators": [
        "access_expires_at",
        "created_at",
        "confirmed_at",
        "last_access_at",
        "unsubscribed_at",
    ],
    "magic_links": ["expires_at", "used_at", "created_at"],
    "edu_materials": ["created_at", "updated_at"],
    "edu_material_files": ["created_at"],
    "edu_suggestions": ["created_at", "updated_at"],
    "edu_research": ["created_at", "updated_at"],
    "contact_submissions": ["created_at"],
    "pricing_config": ["updated_at"],
}


def upgrade() -> None:
    for table, columns in _TZ_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                type_=sa.DateTime(timezone=True),
                existing_type=sa.DateTime(timezone=False),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    for table, columns in _TZ_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                type_=sa.DateTime(timezone=False),
                existing_type=sa.DateTime(timezone=True),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )
