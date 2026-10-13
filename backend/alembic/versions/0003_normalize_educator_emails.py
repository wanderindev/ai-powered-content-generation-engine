"""Normalize existing educator emails (lowercase + trim), collision-safely.

Revision ID: 0003_normalize_educator_emails
Revises: 0002_drop_flask_orphans
Create Date: 2026-05-30

Application code now strips whitespace and lowercases educator emails on
every write/lookup path (see ``app/schemas`` email validators and the
educator / mailing-list services). This forward-only data migration brings
existing ``educators`` rows into the same canonical form so writes and reads
agree, preventing case-variant duplicate accounts (issue #63).

Collision safety: a row is only updated when its normalized value is not
already held by a *different* row. Rows whose normalized form would collide
with an existing row are left untouched, so the ``UNIQUE(email)`` constraint
can never fire. De-duplicating such colliding accounts is a separate,
human-reviewed task and is intentionally NOT done here.

This migration is irreversible: lowercasing/trimming is lossy, so
``downgrade`` is a no-op.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_normalize_educator_emails"
down_revision = "0002_drop_flask_orphans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Lowercase + trim every row whose normalized form is free. Rows that
    # would collide with an existing normalized email are skipped (left as-is)
    # so the UNIQUE(email) constraint can never fire.
    op.execute(
        """
        UPDATE educators e
        SET email = lower(btrim(e.email))
        WHERE e.email <> lower(btrim(e.email))
          AND NOT EXISTS (
              SELECT 1 FROM educators o
              WHERE o.id <> e.id
                AND o.email = lower(btrim(e.email))
          )
        """
    )


def downgrade() -> None:
    # No-op: lowercasing/trimming is lossy and cannot be reversed.
    pass
