"""Paragraphs a human should look at after a Grammarly proofread (issue #518).

``grammar-apply`` writes the corrected body, but first Sonnet reviews every
changed paragraph (``app.services.grammar_verdicts``). Changes it could not
vouch for — a ``REVIEW`` verdict, a ``REVERT`` whose FIND was not unique, a
pair it returned no verdict for, or a failed model call — are **still
written** (the operator accepted them) and recorded here as a before/after
diff with the reason, so the admin grid can show "N paragraphs to review" and
an editor can read them without diffing the article by hand.

Resolving a row is bookkeeping only (``resolved_at``): the editor fixes the
text in the drawer if needed and marks the row seen. Rows are append-only
across runs. Plain FK to ``articles``, no ORM relationship — an audit
side-table like ``grammar_check_fixes``.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import Language, status_check

__all__ = ["GrammarReviewDiff"]


class GrammarReviewDiff(Base):
    __tablename__ = "grammar_review_diffs"
    __table_args__ = (status_check("grammar_review_diffs", "language", Language),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)

    # Closed vocabulary — String + named CHECK (issue #24), reusing Language.
    language: Mapped[str] = mapped_column(String(5))

    # 0-based position of the paragraph in the proofread body at the time of
    # the run; informational only (the body may have been edited since).
    block_index: Mapped[int] = mapped_column(Integer)
    before_text: Mapped[str] = mapped_column(Text)
    after_text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
