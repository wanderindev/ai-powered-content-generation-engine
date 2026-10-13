"""Add paypal_event_id idempotency key to booking_status_log (#34).

PayPal redelivers webhooks on transient failures, and concurrent workers can
both pass the ``status != PAID`` check and double-process an order (duplicate
status-log rows, duplicate customer payment-confirmation emails). The webhook
handler now records PayPal's top-level event ``id`` on the log row it writes;
this column's UNIQUE constraint makes a redelivered event fail the insert
instead of committing a second transition.

- ADD ``paypal_event_id`` (varchar(64), nullable): the PayPal webhook event id.
  NULL for admin-driven logs (mark-paid, cancel, etc.) — those have no PayPal
  event. Postgres allows multiple NULLs under a UNIQUE constraint, so existing
  and future admin rows are unaffected.
- ADD UNIQUE constraint ``uq_booking_status_log_paypal_event_id`` (stable name,
  per the issue-#24 convention) so a redelivery is rejected at the DB level.

Safe on prod: ``booking_status_log`` has zero ``paypal_webhook`` rows today, so
no existing value can collide.

Revision ID: 0008_booking_status_log_paypal_event_id
Revises: 0007_educator_access_token
Create Date: 2026-05-31
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_booking_status_log_paypal_event_id"
down_revision = "0007_educator_access_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.booking_status_log "
        "ADD COLUMN IF NOT EXISTS paypal_event_id varchar(64)"
    )
    # DROP IF EXISTS first so the migration is idempotent / re-runnable.
    op.execute(
        "ALTER TABLE public.booking_status_log "
        "DROP CONSTRAINT IF EXISTS uq_booking_status_log_paypal_event_id"
    )
    op.execute(
        "ALTER TABLE public.booking_status_log "
        "ADD CONSTRAINT uq_booking_status_log_paypal_event_id UNIQUE (paypal_event_id)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.booking_status_log "
        "DROP CONSTRAINT IF EXISTS uq_booking_status_log_paypal_event_id"
    )
    op.execute(
        "ALTER TABLE public.booking_status_log DROP COLUMN IF EXISTS paypal_event_id"
    )
