"""Order model for tour bookings."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import Language, OrderStatus, PaymentMethod, status_check

if TYPE_CHECKING:
    from app.models.booking import Booking

__all__ = ["Order", "OrderStatus", "PaymentMethod"]


class Order(Base):
    """
    Cart checkout creates an order containing one or more bookings.

    Order workflow:
    1. PENDING - Order created, waiting for payment method selection
    2. PAYMENT_LINK_SENT - Payment link generated and sent to customer
    3. PAID - Payment confirmed
    4. CANCELLED - Customer or admin cancelled the order
    5. EXPIRED - Payment link expired without payment
    """

    __tablename__ = "orders"
    __table_args__ = (
        status_check("orders", "status", OrderStatus),
        status_check("orders", "payment_method", PaymentMethod),
        status_check("orders", "customer_language", Language),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # ORD-XXXXX

    # Customer info (no user account)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    customer_hotel: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_language: Mapped[str] = mapped_column(String(2), default="en")  # en/es

    # Status
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING.value)

    # Payment
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)  # PAYPAL, CUANTO
    payment_link_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_link_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # PayPal specific
    paypal_invoice_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    paypal_invoice_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Reservation expiry
    reservation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Follow-up tracking
    followup_1_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    followup_2_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    followup_3_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Totals
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Notes (internal)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    bookings: Mapped[list["Booking"]] = relationship("Booking", back_populates="order")
