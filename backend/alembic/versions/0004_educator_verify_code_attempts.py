"""Add verify_code_attempts and verify_code_last_attempt_at columns to educators.

Revision ID: 0004_educator_verify_code_attempts
Revises: 0003a_widen_alembic_version
Create Date: 2026-05-30

Pure additive schema change that adds two attempt-tracking columns to the
``educators`` table, unblocking the rate-limiting logic planned for issue #53.

- ``verify_code_attempts`` (integer, NOT NULL, DEFAULT 0): counts how many times
  a returning educator has attempted to verify their 6-digit code within the
  current code's lifetime. Reset to zero on a successful verify (by #53).
- ``verify_code_last_attempt_at`` (timestamp without time zone, nullable): records
  when the most recent verify attempt occurred, enabling time-window throttling.

As of this revision the columns exist in the schema but are neither read nor
written by any application code. The rate-limiting logic that consumes them
(incrementing, resetting on success, invalidating the code past the cap) is
deferred to issue #53. This migration is intentionally scoped to the schema
prerequisite only (closes #64).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_educator_verify_code_attempts"
down_revision = "0003a_widen_alembic_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.educators "
        "ADD COLUMN IF NOT EXISTS verify_code_attempts integer NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE public.educators "
        "ADD COLUMN IF NOT EXISTS verify_code_last_attempt_at timestamp without time zone"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.educators DROP COLUMN IF EXISTS verify_code_last_attempt_at"
    )
    op.execute(
        "ALTER TABLE public.educators DROP COLUMN IF EXISTS verify_code_attempts"
    )
