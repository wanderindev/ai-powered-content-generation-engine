"""Unified Anthropic access layer for all LLM-using services.

Single source of truth for:

- **Model identifiers** — bump a model in one place, not across 7 modules.
- **The Anthropic client** — one cached instance (one connection pool) instead
  of a fresh ``anthropic.Anthropic()`` per call site.
- **Defensive JSON parsing** of LLM responses (``parse_llm_json``).

This is the insertion point the rest of the vendor-call-hardening epic (#265)
builds on: per-call timeouts / streaming (#68) and token-usage tracking (#78)
grow inside this module rather than re-touching every call site.

All Anthropic calls go through ``create_message`` — the single seam that logs
per-call token usage (#78) and enforces a generous in-process runaway backstop.
"""

import json
import logging
import re
from functools import lru_cache
from typing import NamedTuple

import anthropic

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# --- Model identifiers — bump here, nowhere else (issue #76) ---
#
# None of the three has a dated snapshot — Anthropic publishes only the bare
# ids, which are complete as written (never append a date suffix). If a
# ``-YYYYMMDD`` id is ever published, pin it; until then the bare id is the
# only valid string.
#
# All three run adaptive thinking whether or not the ``thinking`` kwarg is
# passed, and thinking tokens count toward max_tokens — call sites carry
# headroom for that. On all three, manual ``budget_tokens``
# (``{"type": "enabled"}``), non-default temperature/top_p/top_k and an
# assistant prefill are rejected (400); effort is the only thinking control.
# On Sonnet 5.5 and Opus 5.5, ``thinking={"type": "disabled"}`` is also a 400
# at every effort level, as is forced ``tool_choice`` (``any`` / ``tool``).
# (Sonnet 5.5's thinking-off mode is ``{"type": "between_tools"}``; nothing here
# needs it.) Effort defaults: Sonnet 5.5 ``high``; **Opus 5.5 and Haiku 5.5
# ``medium``** — every Opus and Haiku call site sets ``effort`` explicitly.
#
# Haiku 5.5 (#610 — verified 2026-10-08 with live requests from pic-backend
# and Anthropic's Haiku 5.5 overview + migration guide): adaptive thinking is
# on by default — at medium effort block 0 is a ``thinking`` block, so
# ``first_text`` / ``parse_llm_json`` are mandatory; ``budget_tokens`` 400s
# ("Use thinking.type.adaptive and output_config.effort"); unlike the other
# two it *accepts* ``{"type": "disabled"}`` and forced ``tool_choice``, neither
# used here. Every Haiku call site passes ``output_config={"effort":
# "medium"}`` (the operator's "medium thinking budget") with a cap raised to
# cover the thinking — a ~40-word excerpt measured 389 output tokens, past the
# old 256 cap. It uses the Opus 4.7+ tokenizer (~30% more tokens than Haiku 4.5
# for the same text) and has **no server-side refusal fallback** (see
# ``_FALLBACK_MODELS``).
#
# Opus 5.5 runs the **blog article pipeline** — condense, outline, and content
# (#415 plus the 2026-07-26 content A/B) — and nothing else. Everything outside
# that pipeline (edu materials, fact-check, instagram, research, suggestions,
# image prompts) stays on Sonnet 5.5 deliberately; those were audited and found
# not to be intelligence-bound. Adding a fourth Opus call site is a decision to
# make on measured evidence, not a default.
SONNET_MODEL = "claude-sonnet-5-5"
OPUS_MODEL = "claude-opus-5-5"
HAIKU_MODEL = "claude-haiku-5-5"

# List prices, USD per 1M tokens: (input, output, cache read, cache write).
# Cache write is the 5-minute TTL rate (1.25x input). Bump these with the
# model ids above. First read by Ask PIC's daily USD cap (#634), where an
# unpriced model fails closed.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[float, float, float, float]] = {
    HAIKU_MODEL: (0.10, 0.50, 0.01, 0.125),
    SONNET_MODEL: (2.00, 10.00, 0.20, 2.50),
    OPUS_MODEL: (4.00, 20.00, 0.20, 5.00),
}
# Haiku 5.5's list price above is for prompts up to 100K tokens; a longer
# prompt bills the whole call at this tier (prompt = input + cache tokens).
LONG_PROMPT_PRICE_PER_MILLION_TOKENS: dict[str, tuple[int, tuple[float, float, float, float]]] = {
    HAIKU_MODEL: (100_000, (0.50, 2.50, 0.05, 0.625)),
}


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    """Return the process-wide Anthropic client (one shared connection pool)."""
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def first_text(message) -> str:
    """Return the first *text* block's text, skipping thinking blocks.

    Sonnet 5.5, Opus 5.5 and Haiku 5.5 all run adaptive thinking, so
    ``message.content[0]`` can be a thinking block (or, after a refusal
    fallback, a ``fallback`` marker) with no ``.text`` attribute — never index
    content directly. Falls back to ``content[0]`` when no block advertises a
    string type (keeps test mocks without a real ``type`` attribute working).
    Raises ValueError when the response has no content blocks, or only typed
    non-text blocks — e.g. a truncation-tolerant call that hit ``max_tokens``
    while still thinking.
    """
    content = getattr(message, "content", None) or []
    if not content:
        raise ValueError("LLM returned no content blocks")
    text_blocks = [b for b in content if getattr(b, "type", "text") == "text"]
    if text_blocks:
        return text_blocks[0].text
    if isinstance(getattr(content[0], "type", None), str):
        raise ValueError("LLM returned no text block")
    return content[0].text


def parse_llm_json(message, *, required_key: str | None = None) -> dict | list:
    """Defensively parse JSON from an Anthropic message.

    Extracts the first text block, strips markdown fences, and parses JSON.
    Raises ValueError (not IndexError/JSONDecodeError/KeyError/StopIteration)
    on any failure so callers get one predictable, retry-friendly exception.
    """
    raw = first_text(message).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned malformed JSON: {e}") from e
    if required_key is not None and (not isinstance(result, dict) or required_key not in result):
        raise ValueError(f"LLM response missing required key {required_key!r}")
    return result


# --- Token-usage tracking + runaway backstop (issue #78) ---


class LLMUsage(NamedTuple):
    """Token counts from one (or accumulated) Anthropic response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


class BudgetExceededError(RuntimeError):
    """Raised when cumulative in-process input-token spend exceeds the cap."""


class TruncationError(ValueError):
    """Raised when a response was cut off by ``max_tokens`` (``stop_reason``).

    A truncated response is known-incomplete: JSON parses to garbage, an outline
    loses its trailing sections, an article stops mid-sentence. The defensive
    parsers downstream cannot tell "short" from "truncated", so the cut-off is
    surfaced here — at the single seam every call passes through — instead of
    being silently consumed (issues #93, #101).
    """


class RefusalError(ValueError):
    """Raised when the response ended in ``stop_reason == "refusal"``.

    A safety-classifier or model decline arrives as a normal HTTP 200 whose
    ``content`` is empty (declined before output) or a partial that must be
    discarded (declined mid-stream). Callers already treat ``ValueError`` as
    "the LLM step failed", so this surfaces there instead of as a confusing
    parse error downstream. Raised only after any server-side fallback has
    also declined (see ``_FALLBACK_MODELS``).
    """


# Refusal fallback (server-side, Claude API only): on a policy decline the API
# re-runs the same request on the model Anthropic recommends for that refusal
# category and returns its answer, repriced automatically. ``"default"`` routes
# by category, so no fallback model is pinned here to go stale. Only Sonnet 5.5
# and Opus 5.5 take it. Haiku 5.5 calls are sent without it: Anthropic's Haiku
# 5.5 migration guide says it "has no server-side fallback" (a probe carrying
# the beta header + ``fallbacks`` was not rejected, but there is nothing for it
# to do), so a Haiku decline surfaces directly as ``RefusalError``. The regular ``messages``
# namespace is used (not ``client.beta``) so the SDK's typed Message still
# parses the ``fallback`` content block and ``refusal`` stop details.
_FALLBACK_MODELS = frozenset({SONNET_MODEL, OPUS_MODEL})
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _with_refusal_fallback(kwargs: dict) -> dict:
    """Add the ``fallbacks: "default"`` opt-in for models that accept it."""
    if kwargs.get("model") not in _FALLBACK_MODELS:
        return kwargs
    headers = dict(kwargs.get("extra_headers") or {})
    existing = headers.get("anthropic-beta")
    headers["anthropic-beta"] = f"{existing},{_FALLBACK_BETA}" if existing else _FALLBACK_BETA
    body = {"fallbacks": "default", **(kwargs.get("extra_body") or {})}
    return {**kwargs, "extra_headers": headers, "extra_body": body}


def _fallback_ran(message) -> bool:
    """True when a fallback model served (or attempted) this response."""
    iterations = getattr(getattr(message, "usage", None), "iterations", None)
    if not isinstance(iterations, list):
        return False
    return any(getattr(entry, "type", None) == "fallback_message" for entry in iterations)


# --- Prompt caching (#716) ---
#
# Caching is a byte-exact *prefix* match: render order is tools -> system ->
# messages, and a breakpoint caches everything before it. The minimum
# cacheable prefix on all three 5.5 models is 512 tokens; a shorter prefix
# silently does not cache (no error, ``cache_creation_input_tokens == 0``).
# Writes bill at 1.25x input (5-minute TTL) or 2x (1-hour TTL); reads at the
# cache-read price in ``PRICE_PER_MILLION_TOKENS`` (0.05x on Opus 5.5, 0.1x on
# Sonnet / Haiku 5.5). A read refreshes the TTL, so what matters is the
# start-to-start gap between calls sharing the prefix. Max 4 breakpoints.
_CACHE_TTLS = frozenset({"5m", "1h"})


def cache_control(ttl: str = "5m") -> dict:
    """A ``cache_control`` marker.

    Two uses, both passed straight through ``create_message``:

    - on a content block (a system or message ``text`` block) — an explicit
      breakpoint at the end of the shared prefix; see ``cached_text``;
    - as the top-level ``cache_control=`` kwarg — automatic caching, which
      places the breakpoint on the last cacheable block. On a server-tool
      request it also lets each internal sampling iteration read the previous
      iteration's prefix instead of re-billing it.

    ``ttl`` is ``"5m"`` (default, 1.25x write) or ``"1h"`` (2x write — only
    pays off when calls sharing the prefix start 5-60 minutes apart).
    """
    if ttl not in _CACHE_TTLS:
        raise ValueError(f"cache ttl must be one of {sorted(_CACHE_TTLS)}, got {ttl!r}")
    marker: dict = {"type": "ephemeral"}
    if ttl != "5m":
        marker["ttl"] = ttl
    return marker


def cached_text(text: str, ttl: str = "5m") -> dict:
    """A ``text`` content block carrying a cache breakpoint.

    Make it the *last* block of the stable prefix — ``system=[cached_text(PROMPT)]``,
    or the shared head of a user message followed by an unmarked block holding
    the per-call part: ``content=[cached_text(shared), {"type": "text", "text":
    varying}]``. Everything after the marker bills as ordinary input; a marker
    placed after the varying part pays the write premium on every call.
    """
    return {"type": "text", "text": text, "cache_control": cache_control(ttl)}


def backstop_tokens(usage: LLMUsage) -> int:
    """Input tokens the runaway backstop counts: uncached input + cache writes.

    Cache *writes* are full-price-plus input (1.25x / 2x), so leaving them out
    would let a loop that writes a fresh entry on every call (a marker after
    the varying part) run past the cap unseen. Cache *reads* stay out on
    purpose: they bill at 5-10% of input, and counting them would trip the
    20M cap on legitimate cached batch work — a grounding run re-reads the
    same ~15k-token research doc once per claim batch — at a small fraction of
    the spend the cap was sized for (#716).
    """
    return usage.input_tokens + usage.cache_creation_input_tokens


# Process-lifetime accumulator. A crude runaway backstop, NOT billing-accurate:
# it never auto-resets and is per-worker-process. Real per-feature cost lives in
# the structured logs emitted by create_message (and the Anthropic console).
_session_usage = LLMUsage()


def get_session_usage() -> LLMUsage:
    """Cumulative token usage recorded by this process since start / last reset."""
    return _session_usage


def reset_session_usage() -> None:
    """Reset the in-process accumulator (for tests and long-lived workers)."""
    global _session_usage
    _session_usage = LLMUsage()


def _coerce_int(value) -> int:
    """Return value iff it is a real int, else 0 — keeps usage extraction
    mock-safe (test MagicMock messages have no real ``usage`` integers)."""
    return value if isinstance(value, int) else 0


def _extract_usage(message) -> LLMUsage:
    usage = getattr(message, "usage", None)
    return LLMUsage(
        input_tokens=_coerce_int(getattr(usage, "input_tokens", 0)),
        output_tokens=_coerce_int(getattr(usage, "output_tokens", 0)),
        cache_read_input_tokens=_coerce_int(getattr(usage, "cache_read_input_tokens", 0)),
        cache_creation_input_tokens=_coerce_int(getattr(usage, "cache_creation_input_tokens", 0)),
    )


def usage_from_message(message) -> LLMUsage:
    """Token usage of one response (zeros for a mock without real ints)."""
    return _extract_usage(message)


def _prices(model: str, prompt_tokens: int) -> tuple[float, float, float, float]:
    long_tier = LONG_PROMPT_PRICE_PER_MILLION_TOKENS.get(model)
    if long_tier is not None and prompt_tokens > long_tier[0]:
        return long_tier[1]
    try:
        return PRICE_PER_MILLION_TOKENS[model]
    except KeyError:
        raise ValueError(f"no price recorded for model {model!r}") from None


def estimate_cost_usd(model: str, usage: LLMUsage) -> float:
    """List-price USD cost of ``usage`` on ``model``; ``ValueError`` if unpriced."""
    prompt = usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
    p_in, p_out, p_read, p_write = _prices(model, prompt)
    return (
        usage.input_tokens * p_in
        + usage.output_tokens * p_out
        + usage.cache_read_input_tokens * p_read
        + usage.cache_creation_input_tokens * p_write
    ) / 1_000_000


def worst_case_cost_usd(model: str, *, input_tokens: int, max_tokens: int) -> float:
    """Upper bound on one call's cost before it is made.

    Every input token is priced at the dearer of the plain and cache-write
    rates, and the output at the full ``max_tokens`` (thinking counts toward
    it). ``ValueError`` if the model is unpriced.
    """
    p_in, p_out, _p_read, p_write = _prices(model, input_tokens)
    return (input_tokens * max(p_in, p_write) + max_tokens * p_out) / 1_000_000


def create_message(
    *,
    feature: str,
    stream: bool | None = None,
    reject_on_truncation: bool = True,
    **kwargs,
):
    """Single entry point for ``messages.create`` across all services.

    Enforces the in-process runaway backstop *before* spending, streams long
    generations, then records per-call token usage after.

    - ``feature`` is a short label (e.g. ``"article_content"``) for per-feature
      spend breakdowns in the logs; it is **not** forwarded to the API.
    - ``stream`` forces/forbids streaming; when ``None`` (the default) the call
      streams iff ``max_tokens >= settings.anthropic_stream_min_tokens``.
      Streaming keeps the connection alive for long Sonnet generations instead
      of blocking a worker on one long read (issue #68); the final accumulated
      ``Message`` (including ``.usage``) is returned, identical in shape to a
      non-streamed response.
    - ``reject_on_truncation`` (default ``True``) raises ``TruncationError``
      when the response was cut off by ``max_tokens`` — a known-incomplete
      result no downstream parser can recover (issues #93, #101). The cut-off
      is always logged; set this ``False`` only for calls where a hard stop at
      the cap is an acceptable outcome rather than a defect.
    - A ``stop_reason == "refusal"`` response raises ``RefusalError`` (after
      the server-side fallback, which Sonnet/Opus calls opt into
      automatically, has also declined; Haiku has no fallback).
    - All other kwargs (``model``, ``max_tokens``, ``messages``, ``system``,
      ``thinking``, ``cache_control``, …) pass through. Prompt caching is
      opt-in per call site: mark the end of the stable prefix with
      ``cached_text`` or pass ``cache_control=cache_control()`` for automatic
      placement (#716, docs/claude/llm.md).
    - The backstop counts uncached input + cache writes (``backstop_tokens``),
      never cache reads.
    """
    global _session_usage

    settings = get_settings()
    cap = settings.anthropic_budget_input_tokens
    spent = backstop_tokens(_session_usage)
    if cap and cap > 0 and spent >= cap:
        raise BudgetExceededError(
            f"In-process input-token budget exhausted "
            f"({spent} >= {cap}, uncached input + cache writes); refusing further LLM calls"
        )

    if stream is None:
        stream = kwargs.get("max_tokens", 0) >= settings.anthropic_stream_min_tokens

    request = _with_refusal_fallback(kwargs)
    client = get_client()
    if stream:
        with client.messages.stream(
            timeout=settings.anthropic_stream_timeout_seconds, **request
        ) as streamed:
            message = streamed.get_final_message()
    else:
        message = client.messages.create(timeout=settings.anthropic_timeout_seconds, **request)

    usage = _extract_usage(message)
    _session_usage = _session_usage + usage
    logger.info(
        "anthropic call: feature=%s model=%s streamed=%s input_tokens=%d output_tokens=%d "
        "cache_read=%d cache_write=%d process_total_input=%d",
        feature,
        kwargs.get("model", "?"),
        stream,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens,
        backstop_tokens(_session_usage),
    )

    if _fallback_ran(message):
        logger.warning(
            "anthropic call fell back after a refusal: feature=%s requested=%s served_by=%s",
            feature,
            kwargs.get("model", "?"),
            getattr(message, "model", "?"),
        )

    if getattr(message, "stop_reason", None) == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        logger.warning(
            "anthropic call refused: feature=%s model=%s category=%s — output discarded",
            feature,
            kwargs.get("model", "?"),
            category,
        )
        raise RefusalError(
            f"LLM declined the request for feature={feature!r} (refusal category={category!r})"
        )

    if getattr(message, "stop_reason", None) == "max_tokens":
        logger.warning(
            "anthropic call truncated at max_tokens: feature=%s model=%s max_tokens=%s "
            "output_tokens=%d — response is incomplete",
            feature,
            kwargs.get("model", "?"),
            kwargs.get("max_tokens", "?"),
            usage.output_tokens,
        )
        if reject_on_truncation:
            raise TruncationError(
                f"LLM response for feature={feature!r} was truncated at "
                f"max_tokens={kwargs.get('max_tokens', '?')}; refusing incomplete output"
            )

    return message
