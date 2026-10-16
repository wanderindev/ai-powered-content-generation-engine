"""Voyage AI embeddings + rerank seam (#622, epic #617).

Every Voyage call goes through this module, the way every Anthropic call goes
through ``app/services/llm.py``. It owns:

- **The model ids and the vector size.** They come from settings
  (``embedding_model``, ``embedding_dimensions``, ``rerank_model``) and are
  read nowhere else. The ``voyage-4`` family (``-lite`` / base / ``-large``)
  shares one embedding space, so changing the model within it does not mean
  re-embedding. Moving to another family does.
- **The price table** (``PRICE_PER_MILLION_TOKENS``), so cost estimates are
  computed in one place.
- **Batching.** Voyage caps a request at ``MAX_INPUTS_PER_REQUEST`` inputs and a
  per-model total token count (``MAX_TOKENS_PER_REQUEST``). Tokens are estimated
  conservatively from character counts (``_estimate_tokens``), so a batch stays
  under the limit without a tokenizer dependency.
- **Usage logging.** Each HTTP call logs one ``voyage call: feature=...`` line
  with the billed ``total_tokens`` and an estimated cost, in the same shape as
  ``llm.py``'s ``anthropic call:`` line. The API key is never logged. The same
  billed tokens accumulate per ``(endpoint, model)`` in a process-lifetime
  counter (:func:`get_usage_totals`), like ``llm.get_session_usage``, so the
  RAG eval (#627) can report tokens and cost per query. A caller that needs
  the tokens of *its own* calls (Ask PIC's per-query ledger, #635) wraps them
  in :func:`track_usage`, a ``ContextVar`` scope, so concurrent requests in
  other threads never mix into it.
- **A per-call model override** (``model=`` on :func:`embed_documents` /
  :func:`embed_query`) for that eval's model comparison only; production
  callers leave it unset and get ``settings.embedding_model``.

An empty ``voyage_api_key`` disables the feature: every public function raises
:class:`EmbeddingsDisabledError` before any I/O. Callers either degrade (for
example, fall back to full-text search) or answer 503. That follows the "empty
credentials disable a feature" rule, so startup and unrelated endpoints never
depend on the key.

Why plain ``httpx`` and not the hardened client in ``services/http_client.py``:
that transport pins DNS and blocks SSRF for *semi-trusted* crawl hosts. One fixed
first-party endpoint needs none of that. And why no SDK: ``voyageai`` would add a
dependency for two POSTs.

Why a retry layer, unlike ``gemini_image.py``: embeddings serve request paths
(query embedding for search), and a 429 under Voyage's rate limits is routine
and cheap to retry. Gemini's image call is expensive and driven by the agent
driver, whose attempt limit is the retry. This module reuses
``http_client.run_with_retry`` for the *policy* only. That policy retries on
429 / 5xx / transport errors with exponential backoff and never on a 4xx. The
helper is transport-agnostic, so the codebase keeps one retry definition
instead of a second tenacity configuration.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import NamedTuple

import httpx

from app.core.config import get_settings
from app.services.http_client import run_with_retry

logger = logging.getLogger(__name__)

API_BASE = "https://api.voyageai.com/v1"
EMBEDDINGS_URL = f"{API_BASE}/embeddings"
RERANK_URL = f"{API_BASE}/rerank"

# Document path (``embed_documents``): per-request timeout and total attempts
# (including the first) on 429 / 5xx / transport errors. A full 320K-token
# batch is the slowest case, and indexing runs in the background.
TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 4
# Query path (``embed_query``, ``rerank``): reader-facing search must not hang
# for minutes, so one short retry and then the caller degrades (#626).
QUERY_TIMEOUT_SECONDS = 8.0
QUERY_MAX_ATTEMPTS = 2

# --- Voyage limits (https://docs.voyageai.com/reference/embeddings-api, 2026-10) ---
MAX_INPUTS_PER_REQUEST = 1000
MAX_TOKENS_PER_REQUEST = {
    "voyage-4-lite": 1_000_000,
    "voyage-4": 320_000,
    "voyage-4-large": 120_000,
}
# An unlisted model gets the smallest documented limit.
_DEFAULT_MAX_TOKENS_PER_REQUEST = 120_000
# Fill a batch to at most this fraction of the limit. The estimate is
# conservative already; this leaves margin for scripts it under-counts.
_TOKEN_BUDGET_FRACTION = 0.8
# Characters per token used for the estimate. Real English / Spanish text is
# about 4; 3 over-counts, which is the safe direction for batching.
_CHARS_PER_TOKEN = 3

# Rerank limits (https://docs.voyageai.com/reference/reranker-api).
MAX_RERANK_DOCUMENTS = 1000

# --- Prices, USD per 1M tokens (design doc docs/design/rag-knowledge-layer.md) ---
# The free-token allowance is ignored, so logged costs are an upper bound.
PRICE_PER_MILLION_TOKENS = {
    "voyage-4-lite": 0.02,
    "voyage-4": 0.06,
    "voyage-4-large": 0.12,
    "rerank-2.5": 0.05,
    "rerank-2.5-lite": 0.02,
}

_ERROR_EXCERPT_CHARS = 200

# Billed tokens per (endpoint, model) for this process (see module doc).
_usage_totals: dict[tuple[str, str], int] = {}
# Billed tokens per (endpoint, model) inside the current track_usage() scope.
_usage_scope: ContextVar[dict[tuple[str, str], int] | None] = ContextVar(
    "voyage_usage_scope", default=None
)


class EmbeddingsDisabledError(RuntimeError):
    """``voyage_api_key`` is empty, so embeddings and rerank are off."""


class EmbeddingsError(RuntimeError):
    """A Voyage call failed after retries, or returned an unusable body."""


class RerankResult(NamedTuple):
    """One reranked document: its position in the input list and its score."""

    index: int
    relevance_score: float


@lru_cache(maxsize=1)
def _get_client() -> httpx.Client:
    """Process-wide pooled client. The key goes per request, not on the client."""
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def _api_key() -> str:
    # ``api_key`` (not ``key``): the Sentry scrubber redacts frame locals by
    # credential name (``app/core/observability.py`` ``_SECRET_KEYS``).
    api_key = get_settings().voyage_api_key
    if not api_key:
        raise EmbeddingsDisabledError("Embeddings are disabled: VOYAGE_API_KEY is not set")
    return api_key


def embeddings_enabled() -> bool:
    """True when a Voyage key is configured (no I/O). For callers that skip quietly."""
    return bool(get_settings().voyage_api_key)


def document_model() -> str:
    """The model ``embed_documents`` uses, for callers that record it on a row."""
    return get_settings().embedding_model


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN + 1


def estimate_cost(model: str, tokens: int) -> float:
    """Estimated USD cost of ``tokens`` on ``model``, or 0.0 for an unpriced model."""
    return tokens * PRICE_PER_MILLION_TOKENS.get(model, 0.0) / 1_000_000


def _batches(texts: list[str], model: str) -> list[list[str]]:
    """Split ``texts`` into request-sized batches, in order.

    A batch closes at ``MAX_INPUTS_PER_REQUEST`` inputs or when the next text
    would push the estimated token total past the model's budget. A single text
    over the budget still gets its own batch, because Voyage's default
    ``truncation=true`` cuts it to the context length.
    """
    limit = MAX_TOKENS_PER_REQUEST.get(model, _DEFAULT_MAX_TOKENS_PER_REQUEST)
    budget = int(limit * _TOKEN_BUDGET_FRACTION)
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for text in texts:
        tokens = _estimate_tokens(text)
        if current and (len(current) >= MAX_INPUTS_PER_REQUEST or current_tokens + tokens > budget):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(text)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def _post(
    url: str,
    payload: dict,
    api_key: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """POST with retry on 429 / 5xx / transport errors; return the JSON body."""

    def _call() -> httpx.Response:
        response = _get_client().post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    try:
        response = run_with_retry(_call, max_attempts=max_attempts)
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or "").strip()[:_ERROR_EXCERPT_CHARS]
        raise EmbeddingsError(f"Voyage returned HTTP {exc.response.status_code}: {body}") from None
    except httpx.TransportError as exc:
        raise EmbeddingsError(f"Voyage request failed: {type(exc).__name__}") from None
    try:
        return response.json()
    except ValueError:
        raise EmbeddingsError("Voyage returned a non-JSON body") from None


def get_usage_totals() -> dict[tuple[str, str], int]:
    """Billed tokens per ``(endpoint, model)`` since start (or the last reset)."""
    return dict(_usage_totals)


def reset_usage_totals() -> None:
    """Zero the usage counter (for tests and eval runs)."""
    _usage_totals.clear()


@contextmanager
def track_usage() -> Iterator[dict[tuple[str, str], int]]:
    """Collect the billed tokens per ``(endpoint, model)`` of the Voyage calls
    made inside the ``with`` block, in this context only (thread / task)."""
    scope: dict[tuple[str, str], int] = {}
    token = _usage_scope.set(scope)
    try:
        yield scope
    finally:
        _usage_scope.reset(token)


def _log_usage(*, feature: str, endpoint: str, model: str, inputs: int, total_tokens: int) -> None:
    _usage_totals[(endpoint, model)] = _usage_totals.get((endpoint, model), 0) + total_tokens
    scope = _usage_scope.get()
    if scope is not None:
        scope[(endpoint, model)] = scope.get((endpoint, model), 0) + total_tokens
    logger.info(
        "voyage call: feature=%s endpoint=%s model=%s inputs=%d total_tokens=%d est_cost_usd=%.6f",
        feature,
        endpoint,
        model,
        inputs,
        total_tokens,
        estimate_cost(model, total_tokens),
    )


def _embed(
    texts: list[str],
    input_type: str,
    feature: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    model: str | None = None,
) -> list[list[float]]:
    api_key = _api_key()
    if not texts:
        return []
    settings = get_settings()
    model = model or settings.embedding_model
    vectors: list[list[float]] = []
    for batch in _batches(texts, model):
        body = _post(
            EMBEDDINGS_URL,
            {
                "input": batch,
                "model": model,
                "input_type": input_type,
                "output_dimension": settings.embedding_dimensions,
            },
            api_key,
            timeout=timeout,
            max_attempts=max_attempts,
        )
        try:
            data = sorted(body["data"], key=lambda item: item["index"])
            batch_vectors = [item["embedding"] for item in data]
        except (KeyError, TypeError) as exc:
            raise EmbeddingsError(f"Malformed Voyage embeddings response: {exc!r}") from None
        if len(batch_vectors) != len(batch):
            raise EmbeddingsError(
                f"Voyage returned {len(batch_vectors)} embeddings for {len(batch)} inputs"
            )
        _log_usage(
            feature=feature,
            endpoint="embeddings",
            model=model,
            inputs=len(batch),
            total_tokens=int((body.get("usage") or {}).get("total_tokens") or 0),
        )
        vectors.extend(batch_vectors)
    return vectors


def embed_documents(
    texts: list[str], *, feature: str = "unlabeled", model: str | None = None
) -> list[list[float]]:
    """Embed corpus chunks (``input_type="document"``), one vector per text, in order.

    ``model`` overrides ``settings.embedding_model`` (eval only, see module doc).
    """
    return _embed(list(texts), "document", feature, model=model)


def embed_query(text: str, *, feature: str = "unlabeled", model: str | None = None) -> list[float]:
    """Embed one search query (``input_type="query"``), on the short query-path budget.

    ``model`` overrides ``settings.embedding_model`` (eval only, see module doc).
    """
    return _embed(
        [text],
        "query",
        feature,
        timeout=QUERY_TIMEOUT_SECONDS,
        max_attempts=QUERY_MAX_ATTEMPTS,
        model=model,
    )[0]


def embed_queries(
    texts: list[str], *, feature: str = "unlabeled", model: str | None = None
) -> list[list[float]]:
    """Embed several search queries in one request (``input_type="query"``), in order.

    The batched form of :func:`embed_query`, on the same short query-path
    budget per request (batching splits only past the per-request limits), for
    a caller with many queries at once — KB matching (#628) embeds every
    claim of a fact-check run in one call instead of one call per claim.
    """
    return _embed(
        list(texts),
        "query",
        feature,
        timeout=QUERY_TIMEOUT_SECONDS,
        max_attempts=QUERY_MAX_ATTEMPTS,
        model=model,
    )


def rerank(
    query: str,
    documents: list[str],
    *,
    top_k: int | None = None,
    feature: str = "unlabeled",
) -> list[RerankResult]:
    """Rerank ``documents`` against ``query``, most relevant first.

    Each result's ``index`` points into ``documents``. Voyage's default
    ``truncation=true`` handles over-long inputs. More than
    ``MAX_RERANK_DOCUMENTS`` documents is a caller error. Runs on the short
    query-path budget, like ``embed_query``.
    """
    api_key = _api_key()
    if not documents:
        return []
    if len(documents) > MAX_RERANK_DOCUMENTS:
        raise ValueError(
            f"rerank accepts at most {MAX_RERANK_DOCUMENTS} documents, got {len(documents)}"
        )
    model = get_settings().rerank_model
    payload: dict = {"query": query, "documents": list(documents), "model": model}
    if top_k is not None:
        payload["top_k"] = top_k
    body = _post(
        RERANK_URL,
        payload,
        api_key,
        timeout=QUERY_TIMEOUT_SECONDS,
        max_attempts=QUERY_MAX_ATTEMPTS,
    )
    try:
        results = [
            RerankResult(int(item["index"]), float(item["relevance_score"]))
            for item in body["data"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise EmbeddingsError(f"Malformed Voyage rerank response: {exc!r}") from None
    _log_usage(
        feature=feature,
        endpoint="rerank",
        model=model,
        inputs=len(documents),
        total_tokens=int((body.get("usage") or {}).get("total_tokens") or 0),
    )
    results.sort(key=lambda r: r.relevance_score, reverse=True)
    return results
