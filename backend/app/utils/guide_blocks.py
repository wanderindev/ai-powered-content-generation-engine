"""Prose traversal over a study guide's structured blocks (#461).

``EduMaterial.content_blocks`` (JSONB, #340) is the source of truth for a guide:
the branded PDF renders from it, and ``content`` / ``content_es`` are only
markdown projections produced by ``blocks_to_markdown``. So fact-checking a
guide must read from — and write corrections back into — the blocks themselves.

Two operations, both driven by one generic string-leaf walk rather than a
per-block-type switch. The block vocabulary (``GuideBlockType``) grows, and a
walker means a new block type needs no fact-check change to be covered:

- :func:`blocks_prose` — the guide's prose as plain text, for claim extraction
  and as the correction target. Deliberately **not** the markdown projection:
  a verbatim FIND snippet must match inside a real block string, and markdown
  decoration (``- ``, ``**``, ``> ``) exists only in the projection.
- :func:`apply_to_blocks` — replace a verbatim snippet in whichever string
  holds it, mutating a copy of the block tree.

Keys excluded from the walk are machine values, not prose: block/template
discriminators, generation hints the editor acts on, and stored URLs/ids. An
edit that "corrects" an ``image_query`` would silently change what photo an
editor is told to find.
"""

from __future__ import annotations

import copy
from typing import Any

# Machine-valued keys: never prose, never a correction target.
_NON_PROSE_KEYS = frozenset(
    {
        "type",
        "template",
        "image_query",
        "image_url",
        "media_id",
        "media_path",
        "aspect",  # photo frame shape (landscape/wide/portrait), not prose
        "url",
        "answer",  # mc_question: an index/letter, not a sentence
    }
)


def _walk(node: Any, visit) -> Any:
    """Depth-first walk applying *visit* to every prose string leaf.

    *visit* returns the (possibly replaced) string. Returns the rebuilt node so
    the same traversal serves both collection and replacement.
    """
    if isinstance(node, str):
        return visit(node)
    if isinstance(node, list):
        return [_walk(item, visit) for item in node]
    if isinstance(node, dict):
        return {
            key: value if key in _NON_PROSE_KEYS else _walk(value, visit)
            for key, value in node.items()
        }
    return node


def map_prose(blocks: list[dict] | None, fn) -> list[dict] | None:
    """Return a copy of *blocks* with *fn* applied to every prose string leaf.

    Machine keys (``_NON_PROSE_KEYS``) pass through untouched; the input is not
    mutated.
    """
    if blocks is None:
        return None
    return _walk(copy.deepcopy(blocks), fn)


def blocks_prose(blocks: list[dict] | None) -> str:
    """The guide's prose strings joined by newlines, in document order.

    Used both as the text claims are extracted from and as the text corrections
    are generated against, so a FIND snippet is always a verbatim substring of
    a real block string.
    """
    collected: list[str] = []

    def visit(text: str) -> str:
        if text.strip():
            collected.append(text)
        return text

    _walk(blocks or [], visit)
    return "\n".join(collected)


def count_in_blocks(blocks: list[dict] | None, snippet: str) -> int:
    """How many times *snippet* occurs across the block prose.

    Counted per string leaf and summed, matching how :func:`apply_to_blocks`
    replaces — so the "exactly once" invariant means the same thing to both.
    """
    if not snippet:
        return 0
    total = 0

    def visit(text: str) -> str:
        nonlocal total
        total += text.count(snippet)
        return text

    _walk(blocks or [], visit)
    return total


def apply_to_blocks(blocks: list[dict], find: str, replace: str) -> list[dict]:
    """Return a copy of *blocks* with *find* replaced by *replace* everywhere it
    occurs in prose.

    The caller is expected to have already asserted via :func:`count_in_blocks`
    that the snippet occurs exactly once; replacing everywhere keeps this
    function total rather than silently partial if that ever changes.
    """
    working = copy.deepcopy(blocks)
    return _walk(working, lambda text: text.replace(find, replace))
