"""Add CHECK constraints to status / type columns (issue #24).

Establishes DB-level integrity for every ``String``-backed status/type column
that previously accepted any value. Mirrors the named ``CheckConstraint``
declarations now defined in the models' ``__table_args__`` (built via
``app.models.enums.status_check``). The three PG ENUM columns
(``content_status_type``, ``media_type_type``, ``media_source_type``) already
had DB-level integrity and are intentionally left unchanged.

Constraint names are stable (``ck_<table>_<column>``) so adding a value later is
a clean drop-and-recreate in a follow-up migration.

Revision ID: 0006_status_check_constraints
Revises: 0005_media_candidate_media_id_fk
Create Date: 2026-05-30
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_status_check_constraints"
down_revision = "0005_media_candidate_media_id_fk"
branch_labels = None
depends_on = None


# (table, column, allowed values) — snapshot of the enum value sets at authoring
# time. CHECK passes NULL automatically, so nullable columns stay nullable.
_CONSTRAINTS = [
    ("articles", "outline_status", ["PENDING", "APPROVED", "REJECTED"]),
    (
        "bookings",
        "status",
        ["PENDING_PAYMENT", "PAYMENT_LINK_SENT", "PAID", "CONFIRMED", "CANCELLED", "EXPIRED"],
    ),
    ("bookings", "time_slot", ["AM", "PM"]),
    ("bookings", "transport_direction", ["TO", "FROM", "BOTH"]),
    # The status log records both booking and order transitions -> union of both.
    (
        "booking_status_log",
        "previous_status",
        [
            "PENDING_PAYMENT",
            "PAYMENT_LINK_SENT",
            "PAID",
            "CONFIRMED",
            "CANCELLED",
            "EXPIRED",
            "PENDING",
        ],
    ),
    (
        "booking_status_log",
        "new_status",
        [
            "PENDING_PAYMENT",
            "PAYMENT_LINK_SENT",
            "PAID",
            "CONFIRMED",
            "CANCELLED",
            "EXPIRED",
            "PENDING",
        ],
    ),
    ("contact_submissions", "interest", ["field-trips", "tours"]),
    ("edu_materials", "material_type", ["guide", "slides", "infographic", "podcast", "video"]),
    ("educators", "status", ["PENDING", "CONFIRMED", "UNSUBSCRIBED"]),
    ("educators", "language", ["en", "es"]),
    # educators.source is intentionally unconstrained (open CTA-provenance vocab).
    (
        "magic_links",
        "action",
        ["VIEW_ORDER", "CANCEL_BOOKING", "RENEW_PAYMENT", "ADMIN_ACCESS"],
    ),
    ("media_candidates", "status", ["PENDING", "APPROVED", "REJECTED"]),
    ("media_candidates", "source", ["WIKIMEDIA", "LOC", "SMITHSONIAN"]),
    ("orders", "status", ["PENDING", "PAYMENT_LINK_SENT", "PAID", "CANCELLED", "EXPIRED"]),
    ("orders", "payment_method", ["PAYPAL", "CUANTO", "YAPPY"]),
    ("orders", "customer_language", ["en", "es"]),
]


def upgrade() -> None:
    for table, column, values in _CONSTRAINTS:
        name = f"ck_{table}_{column}"
        allowed = ", ".join(f"'{v}'" for v in values)
        # DROP IF EXISTS first so the migration is idempotent / re-runnable.
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ADD CONSTRAINT {name} CHECK ({column} IN ({allowed}))"
        )


def downgrade() -> None:
    for table, column, _values in _CONSTRAINTS:
        name = f"ck_{table}_{column}"
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {name}")
