"""Create tour booking system tables.

Revision ID: 20260131_tour_booking
Revises: 20260103_bilingual
Create Date: 2026-01-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260131_tour_booking"
down_revision = "20260103_bilingual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all tour booking system tables."""

    # 1. pricing_config - Key-value store for pricing parameters
    op.create_table(
        "pricing_config",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )

    # 2. zones - Geographic zones for transport pricing
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_es", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("description_es", sa.String(255), nullable=True),
        sa.Column("display_order", sa.Integer(), default=0, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
    )

    # 3. hotels - Pre-populated hotels with zone assignments
    op.create_table(
        "hotels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_es", sa.String(200), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("address_es", sa.String(500), nullable=True),
        sa.Column("zone_id", sa.Integer(), sa.ForeignKey("zones.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
    )
    op.create_index("ix_hotels_zone_id", "hotels", ["zone_id"])
    op.create_index("ix_hotels_name", "hotels", ["name"])

    # 4. zone_transport_rates - Transport pricing between zones
    op.create_table(
        "zone_transport_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "from_zone_id", sa.Integer(), sa.ForeignKey("zones.id"), nullable=False
        ),
        sa.Column(
            "to_zone_id", sa.Integer(), sa.ForeignKey("zones.id"), nullable=False
        ),
        sa.Column("rate_small_group", sa.Numeric(10, 2), nullable=False),  # 1-3 pax
        sa.Column("rate_large_group", sa.Numeric(10, 2), nullable=False),  # 4-7 pax
        sa.UniqueConstraint("from_zone_id", "to_zone_id", name="uq_zone_transport_rates"),
    )

    # 5. attractions - Places visited with ticket pricing
    op.create_table(
        "attractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_es", sa.String(200), nullable=False),
        # Tourist prices
        sa.Column(
            "ticket_adult_tourist", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column(
            "ticket_senior_tourist", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column(
            "ticket_child_tourist", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column(
            "ticket_infant_tourist", sa.Numeric(10, 2), default=0, nullable=False
        ),
        # Resident prices
        sa.Column(
            "ticket_adult_resident", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column(
            "ticket_senior_resident", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column(
            "ticket_child_resident", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column(
            "ticket_infant_resident", sa.Numeric(10, 2), default=0, nullable=False
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
    )

    # 6. tours - Tour definitions
    op.create_table(
        "tours",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_es", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("description_es", sa.Text(), nullable=True),
        sa.Column("duration_hours", sa.Numeric(4, 2), nullable=False),
        sa.Column("max_group_size", sa.Integer(), default=7, nullable=False),
        sa.Column("zone_id", sa.Integer(), sa.ForeignKey("zones.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("display_order", sa.Integer(), default=0, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tours_slug", "tours", ["slug"])

    # 7. tour_attractions - Many-to-many linking tours to attractions
    op.create_table(
        "tour_attractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tour_id", sa.Integer(), sa.ForeignKey("tours.id"), nullable=False),
        sa.Column(
            "attraction_id", sa.Integer(), sa.ForeignKey("attractions.id"), nullable=False
        ),
        sa.Column("is_optional", sa.Boolean(), default=False, nullable=False),
        sa.Column("visit_order", sa.Integer(), default=0, nullable=False),
        sa.UniqueConstraint("tour_id", "attraction_id", name="uq_tour_attractions"),
    )

    # 8. orders - Cart checkout creates an order
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        # Customer info
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("customer_email", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(50), nullable=False),
        sa.Column("customer_hotel", sa.String(200), nullable=True),
        sa.Column("customer_language", sa.String(2), default="en", nullable=False),
        # Status
        sa.Column("status", sa.String(20), default="PENDING", nullable=False),
        # Payment
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment_link_sent_at", sa.DateTime(), nullable=True),
        sa.Column("payment_link_expires_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        # PayPal specific
        sa.Column("paypal_invoice_id", sa.String(50), nullable=True),
        sa.Column("paypal_invoice_url", sa.String(500), nullable=True),
        # Reservation expiry
        sa.Column("reservation_expires_at", sa.DateTime(), nullable=True),
        # Follow-up tracking
        sa.Column("followup_1_sent_at", sa.DateTime(), nullable=True),
        sa.Column("followup_2_sent_at", sa.DateTime(), nullable=True),
        sa.Column("followup_3_sent_at", sa.DateTime(), nullable=True),
        # Totals
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(10, 2), nullable=False),
        # Notes
        sa.Column("admin_notes", sa.Text(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_orders_reference", "orders", ["reference"])
    op.create_index("ix_orders_customer_email", "orders", ["customer_email"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # 9. bookings - Individual tour bookings
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("tour_id", sa.Integer(), sa.ForeignKey("tours.id"), nullable=False),
        # Scheduling
        sa.Column("tour_date", sa.Date(), nullable=False),
        sa.Column("time_slot", sa.String(2), nullable=False),  # AM/PM
        sa.Column("preferred_time", sa.String(20), nullable=True),
        # Status
        sa.Column("status", sa.String(20), default="PENDING_PAYMENT", nullable=False),
        # Participants (JSON)
        sa.Column("participants", postgresql.JSON(), nullable=False),
        sa.Column("total_participants", sa.Integer(), nullable=False),
        sa.Column("total_residents", sa.Integer(), default=0, nullable=False),
        sa.Column("total_tourists", sa.Integer(), default=0, nullable=False),
        # Transport
        sa.Column("needs_transport", sa.Boolean(), default=False, nullable=False),
        sa.Column("transport_direction", sa.String(10), nullable=True),
        sa.Column("hotel_id", sa.Integer(), sa.ForeignKey("hotels.id"), nullable=True),
        sa.Column("hotel_name_manual", sa.String(200), nullable=True),
        sa.Column(
            "transport_quote_required", sa.Boolean(), default=False, nullable=False
        ),
        # Pricing breakdown
        sa.Column("service_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("senior_discount", sa.Numeric(10, 2), default=0, nullable=False),
        sa.Column("tickets_total", sa.Numeric(10, 2), default=0, nullable=False),
        sa.Column("transport_total", sa.Numeric(10, 2), default=0, nullable=False),
        sa.Column("grand_total", sa.Numeric(10, 2), nullable=False),
        # Timestamps
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_bookings_reference", "bookings", ["reference"])
    op.create_index("ix_bookings_tour_date", "bookings", ["tour_date"])
    op.create_index(
        "ix_bookings_date_slot", "bookings", ["tour_date", "time_slot", "status"]
    )

    # 10. booking_status_log - Audit trail
    op.create_table(
        "booking_status_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "booking_id", sa.Integer(), sa.ForeignKey("bookings.id"), nullable=True
        ),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("previous_status", sa.String(20), nullable=True),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("changed_by", sa.String(100), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )

    # 11. magic_links - Passwordless authentication
    op.create_table(
        "magic_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(64), unique=True, nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column(
            "booking_id", sa.Integer(), sa.ForeignKey("bookings.id"), nullable=True
        ),
        sa.Column("admin_email", sa.String(255), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_magic_links_token", "magic_links", ["token"])

    # 12. admin_emails - Admin access control
    op.create_table(
        "admin_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
    )


def downgrade() -> None:
    """Drop all tour booking system tables."""
    op.drop_table("admin_emails")
    op.drop_table("magic_links")
    op.drop_table("booking_status_log")
    op.drop_table("bookings")
    op.drop_table("orders")
    op.drop_table("tour_attractions")
    op.drop_table("tours")
    op.drop_table("attractions")
    op.drop_table("zone_transport_rates")
    op.drop_table("hotels")
    op.drop_table("zones")
    op.drop_table("pricing_config")