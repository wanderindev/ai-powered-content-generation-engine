"""One public Ask PIC request: the abuse / cost ledger (#634, epic #619).

Every request that gets past the kill switch writes one row, and the
controls in ``app.services.ask_pic_controls`` read the same table back:

* **Rate limits** are ``COUNT``s over it (per ``ip_hash`` in the last hour,
  global since UTC midnight) of the rows with ``counts_toward_limits``, so
  they hold across both uvicorn workers.
* **The daily USD cap** is a ``SUM(cost_usd)`` since UTC midnight. While a
  query is in flight (``status IS NULL``) its ``cost_usd`` holds the
  worst-case reservation of its pending LLM call; the final cost replaces it.

Privacy:

* ``ip_hash`` is ``HMAC-SHA256(secret, "<UTC date>|<ip>")``: it groups one
  client's requests within a UTC day and cannot be linked across days.
  The raw IP is never stored.
* ``question`` is NULL unless ``ask_pic_store_questions`` is on.
  ``scripts.prune_ask_pic_queries`` deletes rows past ``ask_pic_retention_days``.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text, func, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import AskPicQueryStatus, Language, status_check

__all__ = ["AskPicQuery"]


class AskPicQuery(Base):
    __tablename__ = "ask_pic_queries"
    __table_args__ = (
        status_check("ask_pic_queries", "status", AskPicQueryStatus),
        status_check("ask_pic_queries", "language", Language),
        Index("ix_ask_pic_queries_ip_hash_created_at", "ip_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Request ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    ip_hash: Mapped[str] = mapped_column(String(64))
    # Nullable: an INVALID request may carry no supported language.
    language: Mapped[str | None] = mapped_column(String(2))
    question: Mapped[str | None] = mapped_column(Text)

    # --- Outcome (NULL status = admitted, still in flight) ---
    status: Mapped[str | None] = mapped_column(String(20))
    cited_article_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    # False only for a request refused at admission (RATE_LIMITED, or
    # BUDGET_EXCEEDED before retrieval). Every other row counts toward the
    # per-IP and global limits, including one the budget check refused after
    # paid retrieval.
    counts_toward_limits: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True)

    # --- Cost and timing ---
    retrieval_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(Integer)
    embed_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0", default=0)
