"""Verified-claims knowledge base — resolved fact-check verdicts, reusable.

Part of the fact-checking pipeline (epic #373, issue #378). Panama-history
claims repeat constantly across suggestions and research documents ("Panama
Viejo was founded in 1519" shows up in dozens of subjects). Once a claim has
been externally verified — by the web-search tier (#376) or the manual
deep-research loop (#377) — its verdict is upserted here so later fact-check
runs can inherit it (``checked_via=KB_MATCH``) instead of re-verifying.

``claim_key`` is the dedup key: the claim text normalized to lowercase
alphanumerics with collapsed whitespace (see ``fact_check._normalize``; the
``/fact-check-process`` skill mirrors the same normalization in SQL). Only
resolved verdicts live here — never PENDING / UNVERIFIABLE /
NEEDS_DEEP_RESEARCH, which carry no reusable information.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import FactCheckMethod, FactCheckVerdict, FactClaimType, status_check

__all__ = ["VerifiedClaim"]


class VerifiedClaim(Base):
    __tablename__ = "verified_claims"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Normalized dedup key (unique); claim_text keeps the readable original.
    claim_key: Mapped[str] = mapped_column(Text, unique=True)
    claim_text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(20), default=FactClaimType.OTHER.value)

    verdict: Mapped[str] = mapped_column(String(30))
    correction: Mapped[str | None] = mapped_column(Text)
    citation: Mapped[str | None] = mapped_column(Text)
    # How the source verdict was reached (DEEP_RESEARCH; WEB_SEARCH is historical, #751).
    source_method: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        status_check("verified_claims", "claim_type", FactClaimType),
        status_check("verified_claims", "verdict", FactCheckVerdict),
        status_check("verified_claims", "source_method", FactCheckMethod),
    )
