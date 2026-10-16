"""Revert recorded grammar fixes (``grammar_check_fixes`` rows) in article text.

A Grammarly / LLM fix row records the plain-text ``find_text`` the tool saw and
the ``replace_text`` it wrote. Undoing one means putting ``find_text`` back
where ``replace_text`` now sits — under two constraints this module exists for:

* **The stored body is markdown; the snippets are plain text.** Grammarly
  returns the sentence as rendered, so ``*situado*`` in the article shows up
  as ``situado`` in the row. A byte-for-byte search therefore misses any
  snippet that spans emphasis. Matching is done token by token, allowing
  optional ``*`` / ``_`` emphasis markers around each token, and the tokens
  the revert leaves untouched are copied back from the article verbatim so
  their emphasis survives.
* **Exactly once.** A revert applies only when ``replace_text`` occurs exactly
  once in the content (the #409 / #387 discipline). Fixes that stacked on the
  same sentence must be reverted newest-first so each row's ``replace_text``
  is what the content currently says.

Used by ``scripts/one_off/revert_grammarly_fixes.py`` (#519) and reusable by
the meaning-check gate (#518), whose ``REVERT`` verdicts are the same shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

__all__ = ["RevertResult", "count_snippet", "revert_snippet"]

# Optional markdown emphasis hugging a token: ``*word*``, ``**word**``, ``_word_``.
_EMPHASIS = r"[*_]{0,2}"


@dataclass(frozen=True)
class RevertResult:
    content: str | None
    occurrences: int
    reason: str | None = None

    @property
    def applied(self) -> bool:
        return self.content is not None


def _snippet_pattern(snippet: str) -> tuple[re.Pattern[str], list[str]]:
    """Compile ``snippet`` into a whitespace- and emphasis-tolerant regex.

    One capturing group per token, so a match reports where each token sits in
    the content. Leading/trailing partial words (Grammarly cuts its context
    window mid-word) are matched as-is — no word boundaries are imposed.
    """
    tokens = snippet.split()
    if not tokens:
        raise ValueError("snippet is empty")
    return re.compile(r"\s+".join(f"({_token_regex(tok)})" for tok in tokens)), tokens


def _token_regex(token: str) -> str:
    """A token with optional emphasis hugging its *word core*.

    Emphasis closes before trailing punctuation (``*tierra*,``) and opens after
    leading punctuation (``"*quoted*``), so the markers are allowed between the
    punctuation and the core, not only at the token's ends.
    """
    match = re.fullmatch(r"(\W*?)(.*?)(\W*)", token, flags=re.DOTALL)
    lead, core, trail = match.groups() if match else ("", token, "")
    if not core:  # pure punctuation token, e.g. an em dash
        return re.escape(token)
    return f"{re.escape(lead)}{_EMPHASIS}{re.escape(core)}{_EMPHASIS}{re.escape(trail)}"


def count_snippet(content: str, snippet: str) -> int:
    """How many times ``snippet`` occurs in ``content`` (emphasis-tolerant)."""
    pattern, _ = _snippet_pattern(snippet)
    return sum(1 for _ in pattern.finditer(content))


def revert_snippet(content: str, replace_text: str, find_text: str) -> RevertResult:
    """Put ``find_text`` back where ``replace_text`` occurs exactly once.

    Tokens common to both snippets are copied from ``content`` (keeping their
    markdown emphasis and original inter-token whitespace); only the tokens the
    fix changed are swapped for the ``find_text`` wording. Returns a result with
    ``content=None`` and a ``reason`` when the snippet is absent or ambiguous.
    """
    pattern, replace_tokens = _snippet_pattern(replace_text)
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        reason = (
            "snippet not found in content"
            if not matches
            else f"snippet occurs {len(matches)} times"
        )
        return RevertResult(content=None, occurrences=len(matches), reason=reason)

    match = matches[0]
    find_tokens = find_text.split()
    pieces: list[str] = []
    matcher = SequenceMatcher(None, replace_tokens, find_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Groups are 1-based; copy the original span so emphasis and
            # whitespace inside the run are preserved byte for byte.
            pieces.append(content[match.start(i1 + 1) : match.end(i2)])
        else:
            segment = " ".join(find_tokens[j1:j2])
            if segment:
                pieces.append(segment)

    reverted = content[: match.start()] + " ".join(pieces) + content[match.end() :]
    return RevertResult(content=reverted, occurrences=1)
