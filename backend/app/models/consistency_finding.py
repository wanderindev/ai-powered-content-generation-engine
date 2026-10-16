"""Cross-article contradictions found after an article fact-check (#631, epic #618).

**Shadow mode.** ``app.services.consistency_check`` runs after an ``ARTICLE``
fact-check job succeeds (behind ``CROSS_ARTICLE_CONSISTENCY_ENABLED``, default
off) and records here each pair the judge labelled ``CONTRADICTS``: one of
this article's claims against a fact from **another** article (an
``ARTICLE_CLAIM`` chunk) or from the verified-claims KB. Nothing reads these
rows except the admin endpoints: they never change a ``fact_check_claims``
verdict, never gate publishing and are invisible to the agent driver.

Columns worth knowing:

* ``claim_id`` is nullable with ``ON DELETE SET NULL`` and ``claim_text`` is a
  copy. A fact-check re-run deletes and re-inserts the article's claims with
  new ids, so a CASCADE FK would wipe every finding (resolved ones included)
  on every re-run; the copied text is what the UI shows and what de-duplicates.
* ``other_source_type`` / ``other_source_id`` are polymorphic (a
  ``fact_check_claims`` id or a ``verified_claims`` id) and so have no FK;
  ``other_text`` is the fact the judge was shown (a CORRECTED claim's
  correction, an INACCURATE KB row's correction). ``other_article_id`` is the
  other article (NULL for a KB row), ``ON DELETE CASCADE`` like ``article_id``.
* Open = ``resolved_at IS NULL``. A re-run replaces the article's open findings
  and keeps resolved ones; a new pair whose (claim text, other text) matches a
  resolved finding is not re-recorded, so a dismissal sticks.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import (
    ConsistencyResolution,
    ConsistencySource,
    ConsistencyVerdict,
    status_check,
)

__all__ = ["ConsistencyFinding"]


class ConsistencyFinding(Base):
    __tablename__ = "consistency_findings"
    __table_args__ = (
        status_check("consistency_findings", "verdict", ConsistencyVerdict),
        status_check("consistency_findings", "other_source_type", ConsistencySource),
        status_check("consistency_findings", "resolution", ConsistencyResolution),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("fact_check_claims.id", ondelete="SET NULL")
    )
    claim_text: Mapped[str] = mapped_column(Text)

    other_source_type: Mapped[str] = mapped_column(String(20))
    other_source_id: Mapped[int] = mapped_column(Integer)
    other_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    other_text: Mapped[str] = mapped_column(Text)

    verdict: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)
    similarity: Mapped[float | None] = mapped_column()

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
