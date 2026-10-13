"""Educator model for gated access to educational materials."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import EducatorStatus, Language, status_check


class Educator(Base):
    """
    Tracks educators who request access to educational materials.

    Two concerns share this row, intentionally decoupled:

    * **Gate access** (low-friction, no email verification — issue #50/#52/#53).
      Being in the table is the entitlement. ``access_token`` (opaque, server
      side) + ``access_expires_at`` (7-day window) define a session; gate access
      does NOT depend on ``status`` except that UNSUBSCRIBED revokes it.
        1. Enter email → in table & not UNSUBSCRIBED → fresh token + 7-day window.
        2. Not in table → submit info → row created + token + window immediately.
        3. After expiry → re-enter email → fresh token. (No confirm link, no code.)

    * **Mailing list** (double opt-in — see ``services/mailing_list.py``). Uses
      ``status`` (PENDING→CONFIRMED) + ``confirm_token`` + ``mailing_list``. A
      gate-only signup stays ``PENDING`` (email never proven); it only becomes
      ``CONFIRMED`` by confirming a mailing-list subscription.

    ``UNSUBSCRIBED`` is the shared kill switch — it revokes both gate access and
    list membership.
    """

    __tablename__ = "educators"
    __table_args__ = (
        status_check("educators", "status", EducatorStatus),
        status_check("educators", "language", Language),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    institution: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Status: PENDING, CONFIRMED, UNSUBSCRIBED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EducatorStatus.PENDING.value
    )

    # Language preference
    language: Mapped[str] = mapped_column(String(5), nullable=False, default=Language.ES.value)

    # Source: which CTA triggered signup (e.g., "home_cta", "media_library",
    # "MailingListBlog"). Open provenance vocabulary — intentionally unconstrained.
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Mailing-list confirmation token (UUID, single-use). Used only by the
    # double-opt-in mailing-list flow (services/mailing_list.py), NOT by the gate.
    confirm_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Opaque gate session token (secrets.token_urlsafe). Issued on access/signup,
    # sent by the frontend as `Authorization: Bearer <token>` on every gated
    # request; validated server-side against this column + access_expires_at.
    # Replaces the old forgeable base64(email:expiry) "token" (#52).
    access_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Gate access window (7 days). Token is valid only while this is in the future.
    access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Also subscribe to mailing list?
    mailing_list: Mapped[bool] = mapped_column(default=False)
