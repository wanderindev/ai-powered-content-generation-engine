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
# Haiku is pinned to a dated snapshot for reproducibility. Sonnet 5 has *no*
# dated snapshot published by Anthropic yet — the models endpoint exposes only
# the ``claude-sonnet-5`` alias (4.5 and 4 do have dated snapshots). An alias
# can drift as Anthropic re-points it, so pin Sonnet to a ``-YYYYMMDD`` snapshot
# the moment one is published; until then the alias is the only valid string.
#
# Sonnet 5 (unlike 4.6) runs adaptive thinking by default even when the
# ``thinking`` kwarg is omitted, and thinking tokens count toward max_tokens —
# call sites carry headroom for that. Manual ``budget_tokens`` thinking and
# non-default temperature/top_p/top_k are rejected (400) on this model.
SONNET_MODEL = "claude-sonnet-5"
HAIKU_MODEL = "claude-haiku-4-5-20251001"


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    """Return the process-wide Anthropic client (one shared connection pool)."""
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def first_text(message) -> str:
    """Return the first *text* block's text, skipping thinking blocks.

    Sonnet 5 runs adaptive thinking by default, so ``message.content[0]`` can
    be a thinking block with no ``.text`` attribute — never index content
    directly. Falls back to ``content[0]`` when no block advertises a type
    (keeps test mocks without a real ``type`` attribute working). Raises
    ValueError when the response has no content blocks.
    """
    content = getattr(message, "content", None) or []
    if not content:
        raise ValueError("LLM returned no content blocks")
    text_blocks = [b for b in content if getattr(b, "type", "text") == "text"]
    return (text_blocks[0] if text_blocks else content[0]).text


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
    - All other kwargs (``model``, ``max_tokens``, ``messages``, ``system``,
      ``thinking``, …) pass through.
    """
    global _session_usage

    settings = get_settings()
    cap = settings.anthropic_budget_input_tokens
    if cap and cap > 0 and _session_usage.input_tokens >= cap:
        raise BudgetExceededError(
            f"In-process input-token budget exhausted "
            f"({_session_usage.input_tokens} >= {cap}); refusing further LLM calls"
        )

    if stream is None:
        stream = kwargs.get("max_tokens", 0) >= settings.anthropic_stream_min_tokens

    client = get_client()
    if stream:
        with client.messages.stream(
            timeout=settings.anthropic_stream_timeout_seconds, **kwargs
        ) as streamed:
            message = streamed.get_final_message()
    else:
        message = client.messages.create(timeout=settings.anthropic_timeout_seconds, **kwargs)

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
        _session_usage.input_tokens,
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
