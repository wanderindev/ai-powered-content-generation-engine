"""Size bounds for MCP tool results (#640).

Every read tool returns at most :data:`MAX_RESULT_CHARS` characters of JSON.
Tools trim their own long text fields first (with a field-level flag such as
``content_truncated``); :func:`bound_result` is the generic backstop that
guarantees the cap whatever the data: it repeatedly halves the longest string
(above :data:`MIN_STRING`) and, when no string is left to shorten, drops the
last item of the longest list, then sets ``truncated: true`` on the result.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

MAX_RESULT_CHARS = 20_000
# Strings at or below this length are never shortened by the backstop.
MIN_STRING = 160
ELLIPSIS = " …[truncated]"


def _default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def result_size(result: Any) -> int:
    """Length of the result as compact JSON (what the cap is measured on)."""
    return len(json.dumps(result, ensure_ascii=False, default=_default, separators=(",", ":")))


def clip(text: str | None, limit: int) -> str | None:
    """``text`` cut to at most ``limit`` characters, marked when cut."""
    if text is None or len(text) <= limit:
        return text
    return text[: max(0, limit - len(ELLIPSIS))] + ELLIPSIS


def _walk(node: Any, path: tuple, strings: list, lists: list) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, (*path, key), strings, lists)
    elif isinstance(node, list):
        if node:
            lists.append((path, node))
        for i, value in enumerate(node):
            _walk(value, (*path, i), strings, lists)
    elif isinstance(node, str) and len(node) > MIN_STRING:
        strings.append((path, node))


def _set(root: Any, path: tuple, value: Any) -> None:
    for key in path[:-1]:
        root = root[key]
    root[path[-1]] = value


def bound_result(result: dict[str, Any], max_chars: int = MAX_RESULT_CHARS) -> dict[str, Any]:
    """Return ``result`` (mutated in place) shrunk to at most ``max_chars`` of JSON.

    Always sets ``truncated`` (``False`` when nothing had to go).
    """
    result.setdefault("truncated", False)
    while result_size(result) > max_chars:
        result["truncated"] = True
        strings: list = []
        lists: list = []
        _walk(result, (), strings, lists)
        if strings:
            path, text = max(strings, key=lambda item: len(item[1]))
            _set(result, path, clip(text, max(MIN_STRING, len(text) // 2)))
            continue
        if lists:
            _path, items = max(lists, key=lambda item: len(item[1]))
            items.pop()
            continue
        break  # nothing left to shrink (cannot happen for a dict of scalars this small)
    return result
