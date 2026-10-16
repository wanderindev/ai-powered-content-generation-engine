"""Ask PIC abuse and cost controls (#634, epic #619).

Ask Panama in Context is the first unauthenticated endpoint that spends LLM
money, and nothing else in the stack rate-limits inbound requests (no
middleware, no nginx ``limit_req``). The answer endpoint (#635) calls this
module in this order::

    @router.post("/ask", dependencies=[Depends(require_ask_pic_enabled)])
    def ask(body, request, db):
        ip_hash = hash_client_ip(request)
        query = admit_query(db, ip_hash=ip_hash, language=..., question=...)
        if query.status is not None:            # RATE_LIMITED / BUDGET_EXCEEDED
            ... answer 429 / 503 ...
        ... retrieval (embed_tokens, retrieval_ms) ...
        if not reserve_llm_call(db, query, model=..., input_tokens=..., max_tokens=...,
                                embed_tokens=..., extra_cost_usd=rerank_cost):
            ... BUDGET_EXCEEDED (retrieval cost recorded): answer without the model ...
        message = create_message(feature="ask_pic", ...)
        finish_query(db, query, status=..., usage=usage_from_message(message),
                     extra_cost_usd=rerank_cost, ...)

Properties worth knowing:

* **Kill switch.** ``require_ask_pic_enabled`` answers 503 unless
  ``ask_pic_enabled`` is on **and** ``ask_pic_ip_hash_secret`` is set (an
  empty secret warns once and disables, per the "empty credentials disable a
  feature" convention). No row is written while disabled.
* **DB-backed, cross-worker.** Every limit is a ``COUNT`` / ``SUM`` over
  ``ask_pic_queries``, and each check-then-write runs under one
  transaction-scoped advisory lock, so two workers cannot both pass the last
  slot. The lock is held only for that short transaction, never across
  retrieval or the LLM call (Connection Pool rule): every function commits
  before it returns.
* **What counts.** The per-IP (rolling hour) and global (UTC day) limits
  count every row with ``counts_toward_limits`` true: everything except a
  request refused **at admission** (``RATE_LIMITED``, or ``BUDGET_EXCEEDED``
  before any retrieval ran). Such a refusal is recorded but does not extend
  its own lock-out. A query admitted and later refused by
  ``reserve_llm_call`` *does* count (its retrieval was paid for), as does a
  recorded ``INVALID``. The per-IP window also restarts at UTC midnight,
  because the IP hash rotates there.
* **Budget = reservation.** ``reserve_llm_call`` checks today's
  ``SUM(cost_usd)`` plus the call's **worst case** (``worst_case_cost_usd``:
  all input at the cache-write rate, full ``max_tokens`` of output) against
  ``ask_pic_daily_budget_usd`` *before* the call, and adds that worst case to
  the row, so a concurrent request sees it. ``finish_query`` replaces it with
  the real cost. A request that dies in between leaves its reservation in
  place: conservative, and it falls out at UTC midnight.
* **Privacy.** The IP is kept only as ``HMAC-SHA256(secret, "<UTC date>|<ip>")``
  (``hash_ip``), so one client's requests group within a day but not across
  days. Question text is stored only with ``ask_pic_store_questions`` on.
  Nothing here passes user text to Sentry (``add_breadcrumb`` / ``set_context``
  / ``set_extra`` are never called, guard-tested), and log lines carry no
  question text.
"""

import hashlib
import hmac
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ask_pic_query import AskPicQuery
from app.models.enums import AskPicQueryStatus, Language
from app.services import embeddings
from app.services.llm import LLMUsage, estimate_cost_usd, worst_case_cost_usd

logger = logging.getLogger(__name__)

__all__ = [
    "admit_query",
    "ask_pic_available",
    "client_ip",
    "estimate_input_tokens",
    "finish_query",
    "hash_client_ip",
    "hash_ip",
    "require_ask_pic_enabled",
    "reserve_llm_call",
    "spent_today_usd",
]

# Arbitrary constant key for pg_advisory_xact_lock: serialises every
# check-then-write on ask_pic_queries across workers.
_LOCK_KEY = 634_619

# Conservative chars-per-token for a pre-call estimate (over-counts, like the
# embeddings seam's batching estimate).
_CHARS_PER_TOKEN = 3

_warned_no_secret = False


# --- Kill switch ----------------------------------------------------------------


def ask_pic_available() -> bool:
    """True iff the feature is switched on and an IP-hash secret is configured."""
    global _warned_no_secret
    settings = get_settings()
    if not settings.ask_pic_enabled:
        return False
    if not settings.ask_pic_ip_hash_secret:
        if not _warned_no_secret:
            logger.warning("ask_pic_enabled is on but ASK_PIC_IP_HASH_SECRET is empty: disabled")
            _warned_no_secret = True
        return False
    return True


UNAVAILABLE_DETAIL = "Ask Panama in Context is not available"


def require_ask_pic_enabled() -> None:
    """FastAPI dependency: 503 while the feature is off.

    ``POST /api/v1/ask`` does not use it: a *raised* 5xx is captured by
    sentry-sdk, so that route returns the same 503 from inside its handler
    with ``ask_pic_available()`` (#635). Use the same check for any new route
    that carries reader text."""
    if not ask_pic_available():
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL)


# --- Client identity ------------------------------------------------------------


def client_ip(request: Request) -> str:
    """The client IP: ``X-Real-IP`` (set by the edge nginx on the ``api.`` vhost,
    overwriting any client-sent value), else the socket peer, else ``"unknown"``."""
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def hash_ip(ip: str, *, secret: str, day: date) -> str:
    """Keyed, daily-rotating hash of ``ip`` (64 hex chars). The UTC ``day`` is
    part of the message, so the same IP hashes differently on another day."""
    message = f"{day.isoformat()}|{ip}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def hash_client_ip(request: Request, *, now: datetime | None = None) -> str:
    """``hash_ip`` of the request's client IP for the current UTC day."""
    now = now or datetime.now(UTC)
    return hash_ip(
        client_ip(request),
        secret=get_settings().ask_pic_ip_hash_secret,
        day=now.astimezone(UTC).date(),
    )


# --- Counts and sums ------------------------------------------------------------


def _utc_midnight(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})


def _counted_since(db: Session, since: datetime, *, ip_hash: str | None = None) -> int:
    stmt = select(func.count(AskPicQuery.id)).where(
        AskPicQuery.created_at >= since,
        AskPicQuery.counts_toward_limits.is_(True),
    )
    if ip_hash is not None:
        stmt = stmt.where(AskPicQuery.ip_hash == ip_hash)
    return db.execute(stmt).scalar_one()


def spent_today_usd(
    db: Session, *, now: datetime | None = None, exclude_id: int | None = None
) -> float:
    """Spent plus reserved USD since UTC midnight (``exclude_id`` leaves one row out)."""
    now = now or datetime.now(UTC)
    stmt = select(func.coalesce(func.sum(AskPicQuery.cost_usd), 0)).where(
        AskPicQuery.created_at >= _utc_midnight(now)
    )
    if exclude_id is not None:
        stmt = stmt.where(AskPicQuery.id != exclude_id)
    return float(db.execute(stmt).scalar_one())


# --- Lifecycle --------------------------------------------------------------------


def _stored_question(question: str | None) -> str | None:
    settings = get_settings()
    if not settings.ask_pic_store_questions or not question:
        return None
    return question[: settings.ask_pic_max_question_chars]


def _language(language: str | None) -> str | None:
    return language if language in Language.values() else None


def admit_query(
    db: Session,
    *,
    ip_hash: str,
    language: str | None,
    question: str | None,
    status: AskPicQueryStatus | None = None,
    min_cost_usd: float = 0.0,
    now: datetime | None = None,
) -> AskPicQuery:
    """Check the per-IP, global and budget limits and write the query's row.

    Returns the committed row. ``row.status`` is ``None`` when the query is
    admitted (in flight), ``RATE_LIMITED`` when the per-IP hourly or the global
    daily count is reached, or ``BUDGET_EXCEEDED`` when today's spend plus
    ``min_cost_usd`` reaches the cap. Pass the planned LLM call's worst case
    as ``min_cost_usd``, so a query that could never afford its call is
    refused before it pays for retrieval. Neither refusal counts toward the
    limits.

    Passing ``status`` (e.g. ``INVALID`` for an over-long question) records a
    finished row that does count toward the limits, so validation failures
    cannot be spammed for free. Past the limit it is recorded as
    ``RATE_LIMITED`` instead, like any other request.
    """
    settings = get_settings()
    now = now or datetime.now(UTC)
    _lock(db)
    refused = None
    if _counted_since(db, now - timedelta(hours=1), ip_hash=ip_hash) >= max(
        settings.ask_pic_per_ip_per_hour, 0
    ) or _counted_since(db, _utc_midnight(now)) >= max(settings.ask_pic_global_per_day, 0):
        refused = AskPicQueryStatus.RATE_LIMITED
    elif (
        status is None
        and spent_today_usd(db, now=now) + max(min_cost_usd, 0.0)
        >= settings.ask_pic_daily_budget_usd
    ):
        refused = AskPicQueryStatus.BUDGET_EXCEEDED
    if refused is not None:
        status = refused
    row = AskPicQuery(
        created_at=now,
        ip_hash=ip_hash,
        language=_language(language),
        question=_stored_question(question),
        status=status.value if status is not None else None,
        counts_toward_limits=refused is None,
        cost_usd=Decimal(0),
    )
    db.add(row)
    db.commit()
    if refused is not None:
        logger.info("ask_pic query %d refused by controls: status=%s", row.id, row.status)
    return row


def estimate_input_tokens(*texts: str) -> int:
    """Conservative token estimate of a prompt, for ``reserve_llm_call``."""
    chars = sum(len(t) for t in texts if t)
    return -(-chars // _CHARS_PER_TOKEN)


def reserve_llm_call(
    db: Session,
    query: AskPicQuery,
    *,
    model: str,
    input_tokens: int,
    max_tokens: int,
    embed_tokens: int | None = None,
    embedding_model: str | None = None,
    extra_cost_usd: float = 0.0,
    now: datetime | None = None,
) -> bool:
    """Budget check before one LLM call, using that call's worst-case cost.

    The retrieval already paid for (``embed_tokens`` on ``embedding_model``,
    plus ``extra_cost_usd`` such as the rerank) is added to the row first,
    whatever the outcome. If today's spend on every other row, plus this
    row's cost so far, plus the worst case fits ``ask_pic_daily_budget_usd``,
    the worst case is added too and ``True`` is returned: call the model.
    Otherwise the row is closed as ``BUDGET_EXCEEDED`` and ``False`` is
    returned: do not call it. That row keeps its retrieval cost and still
    counts toward the rate limits. An unpriced model raises ``ValueError``
    (fails closed).
    """
    settings = get_settings()
    worst = Decimal(
        str(worst_case_cost_usd(model, input_tokens=input_tokens, max_tokens=max_tokens))
    )
    retrieval = Decimal(str(_retrieval_cost(embed_tokens, embedding_model, extra_cost_usd)))
    _lock(db)
    db.refresh(query)
    if embed_tokens is not None:
        query.embed_tokens = embed_tokens
    others = Decimal(str(spent_today_usd(db, now=now, exclude_id=query.id)))
    own = Decimal(query.cost_usd or 0) + retrieval
    if others + own + worst > Decimal(str(settings.ask_pic_daily_budget_usd)):
        query.status = AskPicQueryStatus.BUDGET_EXCEEDED.value
        query.model = model
        query.cost_usd = own
        db.commit()
        logger.info("ask_pic query %d refused by controls: status=BUDGET_EXCEEDED", query.id)
        return False
    query.cost_usd = own + worst
    query.model = model
    db.commit()
    return True


def _retrieval_cost(
    embed_tokens: int | None, embedding_model: str | None, extra_cost_usd: float
) -> float:
    cost = extra_cost_usd
    if embed_tokens is not None:
        model = embedding_model or get_settings().embedding_model
        cost += embeddings.estimate_cost(model, embed_tokens)
    return cost


def finish_query(
    db: Session,
    query: AskPicQuery,
    *,
    status: AskPicQueryStatus,
    model: str | None = None,
    usage: LLMUsage | None = None,
    embed_tokens: int | None = None,
    embedding_model: str | None = None,
    extra_cost_usd: float = 0.0,
    retrieval_ms: int | None = None,
    llm_ms: int | None = None,
    cited_article_ids: list[int] | None = None,
) -> AskPicQuery:
    """Record a query's outcome and **actual** cost, replacing any reservation.

    ``cost_usd`` = the LLM call's list-price cost (``usage`` on ``model``, or
    the model reserved earlier) + the query embedding (``embed_tokens`` on
    ``embedding_model``, default the configured one) + ``extra_cost_usd``
    (e.g. the rerank call). It replaces the row's cost outright, so pass the
    rerank cost again even if ``reserve_llm_call`` was given it;
    ``embed_tokens`` defaults to the value already on the row. Call it on
    every exit path after
    ``admit_query`` admitted the row, ``ERROR`` included.
    """
    model = model or query.model
    cost = 0.0
    if usage is not None:
        if model is None:
            raise ValueError("finish_query needs the model to price usage")
        cost += estimate_cost_usd(model, usage)
        query.input_tokens = usage.input_tokens
        query.output_tokens = usage.output_tokens
        query.cache_read_input_tokens = usage.cache_read_input_tokens
        query.cache_creation_input_tokens = usage.cache_creation_input_tokens
    if embed_tokens is None:
        embed_tokens = query.embed_tokens
    cost += _retrieval_cost(embed_tokens, embedding_model, extra_cost_usd)
    query.embed_tokens = embed_tokens
    if usage is None and status == AskPicQueryStatus.ERROR:
        # The call may have been billed without returning usage: keep the
        # worst-case reservation rather than under-count the day.
        cost = max(cost, float(query.cost_usd or 0))
    query.status = status.value
    query.model = model
    query.cost_usd = Decimal(str(round(cost, 6)))
    query.retrieval_ms = retrieval_ms
    query.llm_ms = llm_ms
    query.cited_article_ids = list(cited_article_ids) if cited_article_ids is not None else None
    db.commit()
    logger.info(
        "ask_pic query %d: status=%s model=%s cost_usd=%.6f retrieval_ms=%s llm_ms=%s",
        query.id,
        query.status,
        query.model,
        cost,
        retrieval_ms,
        llm_ms,
    )
    return query
