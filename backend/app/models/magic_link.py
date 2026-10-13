"""Magic link model for passwordless authentication."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import MagicLinkAction, status_check

__all__ = ["AdminEmail", "MagicLink", "MagicLinkAction"]


class MagicLink(Base):
    """
    Passwordless authentication for customers and admins.

    Actions:
    - VIEW_ORDER: View order details
    - CANCEL_BOOKING: Cancel an unpaid booking
    - RENEW_PAYMENT: Request a new payment link
    - ADMIN_ACCESS: Access admin panel
    """

    __tablename__ = "magic_links"
    __table_args__ = (status_check("magic_links", "action", MagicLinkAction),)

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Target
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id"), nullable=True, index=True
    )
    admin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)  # For admin links

    # Type and status
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminEmail(Base):
    """
    Hardcoded admin emails for access control.

    Simple approach: only emails in this table can request admin magic links.
    """

    __tablename__ = "admin_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
