"""Fact-check claims — atomic factual claims extracted from pipeline content.

Foundation of the fact-checking pipeline (epic #373). Each row is one atomic
claim (event, person↔event, date, place, quantity) extracted by Haiku from an
article suggestion, a research document, or an article, plus the verdict a
verification tier assigned to it:

* **GROUNDING** — article claims judged against their approved research doc
  (#375; the research *is* the ground truth for an article).
* **WEB_SEARCH** — Sonnet + the web-search tool for suggestion/research claims
  (#376); punts to ``NEEDS_DEEP_RESEARCH`` rather than guessing.
* **DEEP_RESEARCH** — the manual claude.ai deep-research loop (#377).
* **KB_MATCH** — inherited from an already-verified claim (#378).

The subject reference is polymorphic (``subject_type`` + ``subject_id``) so one
table serves all three pipeline stages; no FK is declared because the target
table varies per row. Re-running a check replaces the subject's rows wholesale.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import (
    FactCheckMethod,
    FactCheckSubject,
    FactCheckVerdict,
    FactClaimType,
    status_check,
)

__all__ = [
    "FactCheckClaim",
    "FactCheckMethod",
    "FactCheckSubject",
    "FactCheckVerdict",
    "FactClaimType",
]


class FactCheckClaim(Base):
    __tablename__ = "fact_check_claims"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Polymorphic subject — closed vocabularies are String + named CHECK (#24).
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[int] = mapped_column(Integer)

    claim_text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(20), default=FactClaimType.OTHER.value)

    verdict: Mapped[str] = mapped_column(
        String(30),
        default=FactCheckVerdict.PENDING.value,
        server_default=FactCheckVerdict.PENDING.value,
    )
    # For INACCURATE/DISPUTED: the corrected fact (grounding stores what the
    # research actually says). For VERIFIED via external tiers: optional note.
    correction: Mapped[str | None] = mapped_column(Text)
    # Source URL(s)/reference backing a WEB_SEARCH / DEEP_RESEARCH verdict.
    citation: Mapped[str | None] = mapped_column(Text)

    checked_via: Mapped[str | None] = mapped_column(String(20))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        status_check("fact_check_claims", "subject_type", FactCheckSubject),
        status_check("fact_check_claims", "claim_type", FactClaimType),
        status_check("fact_check_claims", "verdict", FactCheckVerdict),
        status_check("fact_check_claims", "checked_via", FactCheckMethod),
        Index("ix_fact_check_claims_subject", "subject_type", "subject_id"),
    )
