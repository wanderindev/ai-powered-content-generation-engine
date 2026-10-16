"""Situating summaries for article chunks (#697, epic #698).

A chunk such as "they arrived at the gulf..." carries no signal that it is
about Balboa's arrival, so neither the embedding nor the reranker can match it
to a reader's question. :func:`situate` asks Haiku 5.5 (effort ``low``,
``feature="knowledge_context"``) for one sentence per chunk that names who,
what, where and when, with the whole article in view. The indexer stores the
sentence in the chunk's ``meta["context"]`` and embeds
``title > heading path`` + the sentence + the chunk; retrieval reranks on the
same text. Readers and the answer model still see only the title and the chunk
(``content`` is unchanged).

One call per article language, covering only the chunks that need a sentence
(new or edited chunks, or chunks indexed before #697), so an edit to one
section costs one small call.

**Never raises.** Any failure (no ``ANTHROPIC_API_KEY``, the budget backstop,
a refusal, truncation, a transport error, a malformed reply) returns no
sentence for the affected chunks, and the indexer embeds them with
``title > heading path`` only. The next article write or the
``scripts.reindex_knowledge`` sweep retries them, up to
:data:`MAX_ATTEMPTS` calls per chunk content hash (#712, counted in the row's
``meta``); after that the chunk stays without a sentence and is ``unchanged``
until it is edited (a new hash) or ``reindex_knowledge --retry-context`` runs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.prompts.knowledge_context import CONTEXT_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from app.services.knowledge.chunker import Chunk, estimate_tokens
from app.services.llm import (
    HAIKU_MODEL,
    BudgetExceededError,
    LLMUsage,
    create_message,
    estimate_cost_usd,
    parse_llm_json,
    usage_from_message,
)

logger = logging.getLogger(__name__)

FEATURE = "knowledge_context"
MODEL = HAIKU_MODEL
EFFORT = "low"
# Thinking counts toward max_tokens; a sentence is ~40-70 tokens (more in ES).
BASE_MAX_TOKENS = 2000
MAX_TOKENS_PER_CHUNK = 150
MAX_TOKENS_CAP = 16000
# Situating calls per chunk content hash that may return no sentence before
# the indexer stops asking (#712); a budget stop does not count.
MAX_ATTEMPTS = 3
# A stored sentence is cut to this many characters.
MAX_CONTEXT_CHARS = 400
# Dry-run estimates: output tokens per sentence (thinking included) and the
# Haiku tokenizer's ratio to the chunker's chars/4 estimate (~1.3, llm.md).
EST_OUTPUT_TOKENS_PER_CHUNK = 90
TOKENIZER_RATIO = 1.3


@dataclass
class Situated:
    """The sentences one :func:`situate` call produced (by chunk index)."""

    contexts: dict[int, str]
    status: str  # ok | partial | disabled | skipped | failed:<Exception> | malformed
    usage: LLMUsage | None = None
    cost_usd: float = 0.0
    ms: int = 0


def context_enabled() -> bool:
    """Situating sentences need an Anthropic key; without one chunks are embedded without."""
    return bool(get_settings().anthropic_api_key)


def max_tokens_for(n: int) -> int:
    return min(MAX_TOKENS_CAP, BASE_MAX_TOKENS + MAX_TOKENS_PER_CHUNK * n)


def _prompt(title: str, language: str, chunks: list[Chunk], wanted: list[int]) -> str:
    return build_user_prompt(
        title,
        language,
        [(c.chunk_index, c.heading_path, c.content) for c in chunks],
        wanted,
    )


def estimate(title: str, language: str, chunks: list[Chunk], wanted: list[int]) -> LLMUsage:
    """Estimated tokens of one :func:`situate` call (for ``--dry-run``)."""
    if not wanted:
        return LLMUsage()
    text = SYSTEM_PROMPT + _prompt(title, language, chunks, wanted)
    return LLMUsage(
        input_tokens=round(estimate_tokens(text) * TOKENIZER_RATIO),
        output_tokens=EST_OUTPUT_TOKENS_PER_CHUNK * len(wanted),
    )


def estimate_cost(usage: LLMUsage) -> float:
    return estimate_cost_usd(MODEL, usage)


def _clean(value) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def parse_contexts(data, wanted: list[int]) -> dict[int, str]:
    """``{chunk_index: sentence}`` for the wanted indexes found in a reply."""
    if not isinstance(data, dict) or not isinstance(data.get("contexts"), list):
        return {}
    allowed = set(wanted)
    out: dict[int, str] = {}
    for item in data["contexts"]:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index not in allowed:
            continue
        text = _clean(item.get("context"))
        if text and index not in out:
            out[index] = text
    return out


def situate(
    title: str,
    language: str,
    chunks: list[Chunk],
    wanted: list[int],
    *,
    log_label: str = "-",
) -> Situated:
    """One sentence per wanted chunk index, from one Haiku call. Never raises.

    ``chunks`` is every chunk of the article in ``language`` (the document the
    model reads); ``wanted`` the chunk indexes that need a sentence.
    """
    wanted = sorted(set(wanted))
    if not wanted:
        return Situated({}, "skipped")
    if not context_enabled():
        return Situated({}, "disabled")
    t = time.perf_counter()
    try:
        message = create_message(
            feature=FEATURE,
            model=MODEL,
            max_tokens=max_tokens_for(len(wanted)),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt(title, language, chunks, wanted)}],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": CONTEXT_SCHEMA},
            },
        )
    except Exception as exc:  # noqa: BLE001 — optional step: embed without a sentence
        logger.warning(
            "knowledge context for %s (%s) failed: %s; indexing without it",
            log_label,
            language,
            type(exc).__name__,
        )
        status = "budget" if isinstance(exc, BudgetExceededError) else "failed"
        return Situated({}, f"{status}:{type(exc).__name__}", ms=_ms(t))
    usage = usage_from_message(message)
    try:
        data = parse_llm_json(message)
    except ValueError:
        data = None
    contexts = parse_contexts(data, wanted)
    if not contexts:
        status = "malformed"
    elif len(contexts) < len(wanted):
        status = "partial"
    else:
        status = "ok"
    if status != "ok":
        logger.warning(
            "knowledge context for %s (%s): %s, %d/%d sentence(s)",
            log_label,
            language,
            status,
            len(contexts),
            len(wanted),
        )
    return Situated(contexts, status, usage, estimate_cost_usd(MODEL, usage), _ms(t))


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


__all__ = [
    "FEATURE",
    "MAX_ATTEMPTS",
    "Situated",
    "context_enabled",
    "estimate",
    "estimate_cost",
    "parse_contexts",
    "situate",
]
