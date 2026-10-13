"""Prevent double-booking a slot via a partial unique index (#40).

Revision ID: 0011_booking_slot_unique_index
Revises: 0010_tz_aware_datetimes
Create Date: 2026-05-31

``AvailabilityService.can_book_slot`` checks a slot with a plain SELECT and the
caller then INSERTs — a check-then-act race where two concurrent orders can both
pass the check and both insert, double-booking one slot. This shifts race
resolution into Postgres:

* Add ``bookings.allow_multiple_per_slot`` (NOT NULL, default false). Fixed-price
  (open-group) tours intentionally allow many bookings per slot and are set true.
* Backfill existing fixed-price bookings to true BEFORE building the index, so an
  already-legitimate duplicate slot among open-group tours cannot block creation.
* Create a partial UNIQUE index on ``(tour_date, time_slot)`` covering only the
  four slot-holding statuses and only rows with ``allow_multiple_per_slot = false``.
  The second concurrent active insert for the same private-tour slot now fails;
  ``create_order`` catches the IntegrityError and returns 409.

If this index fails to build on prod it means a real double-booking already
exists among private tours — investigate rather than force it.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_booking_slot_unique_index"
down_revision = "0010_tz_aware_datetimes"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_active_booking_slot"
# Must match _ACTIVE_SLOT_PREDICATE in app/models/booking.py.
_PREDICATE = (
    "status IN ('PENDING_PAYMENT', 'PAYMENT_LINK_SENT', 'PAID', 'CONFIRMED')"
    " AND allow_multiple_per_slot = false"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE bookings ADD COLUMN allow_multiple_per_slot BOOLEAN NOT NULL DEFAULT false"
    )
    # Exempt existing fixed-price bookings before enforcing uniqueness.
    op.execute(
        "UPDATE bookings SET allow_multiple_per_slot = true "
        "WHERE tour_id IN (SELECT id FROM tours WHERE fixed_price_config IS NOT NULL)"
    )
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX_NAME} ON bookings (tour_date, time_slot) WHERE {_PREDICATE}"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    op.execute("ALTER TABLE bookings DROP COLUMN allow_multiple_per_slot")
