"""Educator gate: add opaque access_token, drop unused verify_code columns.

Part of the educator-access-gate epic (#50/#51/#52/#53). The gate is reworked
to be low-friction and token-based:

- ADD ``access_token`` (varchar(64), nullable, indexed): the opaque server-side
  session token (``secrets.token_urlsafe``) that replaces the old forgeable
  base64(email:expiry) string (#52). The frontend sends it as a Bearer token on
  every gated public-media request; the server validates it against this column
  plus ``access_expires_at``.
- DROP ``verify_code``, ``verify_code_expires_at``, ``verify_code_attempts``,
  ``verify_code_last_attempt_at``: the 6-digit re-verification flow is removed
  entirely (#53). Being in the table is now the entitlement, so a returning
  educator just re-enters their email — no code to brute-force. These columns
  (the latter two added in 0004) carried only transient values, so dropping
  them loses nothing of record.

``status`` keeps its existing vocabulary (PENDING/CONFIRMED/UNSUBSCRIBED) and
its ``ck_educators_status`` CHECK — gate access is decoupled from ``status``, so
no constraint change is needed.

Revision ID: 0007_educator_access_token
Revises: 0006_status_check_constraints
Create Date: 2026-05-31
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_educator_access_token"
down_revision = "0006_status_check_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.educators "
        "ADD COLUMN IF NOT EXISTS access_token varchar(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_educators_access_token "
        "ON public.educators (access_token)"
    )
    op.execute("ALTER TABLE public.educators DROP COLUMN IF EXISTS verify_code")
    op.execute("ALTER TABLE public.educators DROP COLUMN IF EXISTS verify_code_expires_at")
    op.execute("ALTER TABLE public.educators DROP COLUMN IF EXISTS verify_code_attempts")
    op.execute("ALTER TABLE public.educators DROP COLUMN IF EXISTS verify_code_last_attempt_at")


def downgrade() -> None:
    # Re-add the dropped columns (matching their original definitions) before
    # removing access_token, so a downgrade restores the prior schema shape.
    op.execute("ALTER TABLE public.educators ADD COLUMN IF NOT EXISTS verify_code varchar(6)")
    op.execute(
        "ALTER TABLE public.educators "
        "ADD COLUMN IF NOT EXISTS verify_code_expires_at timestamp without time zone"
    )
    op.execute(
        "ALTER TABLE public.educators "
        "ADD COLUMN IF NOT EXISTS verify_code_attempts integer NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE public.educators "
        "ADD COLUMN IF NOT EXISTS verify_code_last_attempt_at timestamp without time zone"
    )
    op.execute("DROP INDEX IF EXISTS ix_educators_access_token")
    op.execute("ALTER TABLE public.educators DROP COLUMN IF EXISTS access_token")
