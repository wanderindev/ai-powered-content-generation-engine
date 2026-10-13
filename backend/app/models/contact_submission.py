"""Contact form submission model."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ContactInterest, status_check


class ContactSubmission(Base):
    """Persists every contact form submission regardless of email delivery."""

    __tablename__ = "contact_submissions"
    __table_args__ = (status_check("contact_submissions", "interest", ContactInterest),)

    id: Mapped[int] = mapped_column(primary_key=True)
    interest: Mapped[str] = mapped_column(String(20), nullable=False)
    first_name: Mapped[str] = mapped_column(String(200), nullable=False)
    last_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Store type-specific fields as JSON to avoid sparse columns
    form_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Email delivery tracking
    email_sent: Mapped[bool] = mapped_column(default=False)
    email_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
