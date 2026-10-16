"""Grammar-check audit trail — one row per FIND/REPLACE pair the proofread returned.

The grammar stage (issue #409) runs one strict corrections-only Sonnet pass per
language over an article's title + excerpt + content and applies the returned
``FIND ||| REPLACE`` pairs in place. Every *parsed* pair lands here, applied or
not, so an editor can review exactly what the model changed (and what it wanted
to change but couldn't safely apply — see ``skipped_reason``).

Append-only: re-running the check re-stamps the article and adds new rows; prior
rows are never deleted. Lines that fail to parse produce no row at all — there
is no trustworthy find/replace to record.

Plain FK to ``articles`` with no ORM relationship attribute: this is an audit
side-table, not part of the article's object graph.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import GrammarFixSource, Language, status_check

__all__ = ["GrammarCheckFix"]


class GrammarCheckFix(Base):
    __tablename__ = "grammar_check_fixes"
    __table_args__ = (
        status_check("grammar_check_fixes", "language", Language),
        status_check("grammar_check_fixes", "source", GrammarFixSource),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)

    # Closed vocabulary — String + named CHECK (issue #24), reusing Language.
    language: Mapped[str] = mapped_column(String(5))

    find_text: Mapped[str] = mapped_column(Text)
    replace_text: Mapped[str] = mapped_column(Text)

    applied: Mapped[bool]
    skipped_reason: Mapped[str | None] = mapped_column(Text)

    # Who produced the pair: the LLM pass or the Grammarly web-editor pass
    # (migration 0032). Closed vocabulary — String + named CHECK (issue #24).
    source: Mapped[str] = mapped_column(
        String(20), default=GrammarFixSource.LLM.value, server_default=GrammarFixSource.LLM.value
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
