"""Section-aware bilingual chunker for article markdown (#623, epic #617).

Pure functions: no DB, no network. The indexer (#624) feeds it one language's
``title`` + ``content`` (``content`` / ``content_es``) and writes one
``knowledge_chunks`` row per returned :class:`Chunk`.

How an article becomes chunks:

1. **Series boilerplate is stripped.** The "About this Article" / "Acerca de
   este artículo" header and the "Continue Reading" / "Continuar leyendo" CTA
   that ``series_sections.py`` injects are cut with its own
   :func:`split_series_sections`, so both stay defined in one place. They are
   navigation, not content, and would otherwise make every member of a series
   look alike to retrieval.
2. **Split by section.** ``#`` / ``##`` / ``###`` headings open a section and
   the heading path ("Heading > Subheading") is kept; ``####`` and deeper stay
   in the text. Text before the first heading has no path. An ``#`` heading
   that just repeats the title is dropped.
3. **Pack paragraphs** (blank-line separated blocks) into chunks of roughly
   ``MIN_TOKENS``–``MAX_TOKENS``. A section that fits is one chunk; a longer
   one is split into evenly sized chunks at paragraph boundaries, each later
   chunk opening with up to ``OVERLAP_TOKENS`` of trailing sentences from the
   previous one. A paragraph is split (by sentence, then by word) only when it
   alone exceeds ``MAX_TOKENS``.
4. **Embedding text** is ``"title > heading path"`` + a blank line + the chunk
   content; ``content_hash`` and ``token_count`` are taken over it, so a
   retitled article or renamed heading re-embeds. Since #697 the indexer adds a
   generated situating sentence (``meta["context"]``) on its own line under the
   ``title > heading path`` line (:func:`contextual_text`); the hash stays over
   the deterministic text above, so it remains the re-embed key and the
   sentence never has to be generated just to compare.

Token counts are an estimate (no tokenizer dependency): ``ceil(chars / 4)``,
the usual rule of thumb for English BPE vocabularies. Spanish runs a little
higher per char, which only makes the 300–600 band approximate — fine, since
voyage-4 takes 32k tokens per input and the band is a retrieval-granularity
choice, not a model limit.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from app.services.series_sections import split_series_sections

MIN_TOKENS = 300
MAX_TOKENS = 600
OVERLAP_TOKENS = 60
CHARS_PER_TOKEN = 4

HEADING_SEPARATOR = " > "
# ``meta`` key of the generated situating sentence (#697).
CONTEXT_KEY = "context"
# ``meta`` keys of a chunk that has no sentence yet (#712): how many situating
# calls returned none for it, and when the last one did (ISO 8601, UTC).
CONTEXT_ATTEMPTS_KEY = "context_attempts"
CONTEXT_FAILED_AT_KEY = "context_failed_at"
# ``meta`` key pairing a chunk with its other-language twin (#713): the body's
# shape, the section index and the paragraph span (:func:`twin_key`). Equal
# keys within one article mean the chunks pack the same source paragraphs.
TWIN_KEY = "twin_key"

_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n+")
# Whitespace after sentence-ending punctuation, optionally followed by a
# closing quote / bracket (two fixed-width lookbehinds; the mark is kept).
_SENTENCE_SPLIT_RE = re.compile(r"(?:(?<=[.!?…])|(?<=[.!?…][\"'”»)]))\s+")


def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ``ceil(len(text) / 4)``; 0 for blank text."""
    text = text.strip()
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Section:
    """A run of paragraphs under one heading path (empty path = no heading)."""

    headings: tuple[str, ...]
    paragraphs: tuple[str, ...]

    @property
    def heading_path(self) -> str | None:
        return HEADING_SEPARATOR.join(self.headings) or None


@dataclass(frozen=True)
class Chunk:
    """One embeddable chunk of one article in one language."""

    chunk_index: int
    heading_path: str | None
    content: str
    embedding_text: str
    content_hash: str
    token_count: int
    meta: dict = field(default_factory=dict)
    # ``"title > heading path"`` (what :func:`build_embedding_text` put first).
    prefix: str = ""

    @property
    def context(self) -> str | None:
        """The generated situating sentence (#697), if this chunk has one."""
        return (self.meta or {}).get(CONTEXT_KEY) or None

    @property
    def text_to_embed(self) -> str:
        """What is sent to the embedding model: :attr:`embedding_text` plus the context."""
        context = self.context
        if not context:
            return self.embedding_text
        return contextual_text(self.prefix, context, self.content)


def strip_series_boilerplate(content: str | None) -> str:
    """Return the article body without the injected series About / Continue blocks."""
    _about, body, _continue = split_series_sections(content or "")
    return body


def split_sections(markdown: str, title: str | None = None) -> list[Section]:
    """Split markdown into sections by ``#``–``###`` headings, keeping the path.

    Sections with no paragraphs (a heading directly followed by a subheading)
    are omitted; their heading still appears in the children's paths.
    """
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(lines).strip()
        lines.clear()
        if not text:
            return
        paragraphs = tuple(p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip())
        if paragraphs:
            sections.append(Section(tuple(h for _, h in stack), paragraphs))

    normalized_title = _normalize(title) if title else None
    for line in markdown.replace("\r\n", "\n").split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if not match:
            lines.append(line)
            continue
        level = len(match.group(1))
        heading = _clean_heading(match.group(2))
        if level == 1 and normalized_title and _normalize(heading) == normalized_title:
            continue  # the title is already the prefix of every embedding text
        flush()
        while stack and stack[-1][0] >= level:
            stack.pop()
        if heading:
            stack.append((level, heading))
    flush()
    return sections


def chunk_article(title: str, content: str | None, language: str) -> list[Chunk]:
    """Chunk one language of an article (``content`` or ``content_es``).

    ``language`` is recorded in each chunk's ``meta``; the splitting itself is
    language-agnostic. Returns ``[]`` for empty content.
    """
    body = strip_series_boilerplate(content)
    title = (title or "").strip()
    chunks: list[Chunk] = []
    sections = split_sections(body, title)
    shape = section_shape(sections)
    for section_index, section in enumerate(sections):
        pieces = pack_paragraph_spans(list(section.paragraphs))
        for part, (piece, span) in enumerate(pieces):
            embedding_text = build_embedding_text(title, section.heading_path, piece)
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    heading_path=section.heading_path,
                    content=piece,
                    embedding_text=embedding_text,
                    content_hash=content_hash(embedding_text),
                    token_count=estimate_tokens(embedding_text),
                    meta={
                        "language": language,
                        "headings": list(section.headings),
                        "section_index": section_index,
                        "section_part": part,
                        "section_parts": len(pieces),
                        TWIN_KEY: twin_key(shape, section_index, span),
                    },
                    prefix=embedding_prefix(title, section.heading_path),
                )
            )
    return chunks


def embedding_prefix(title: str | None, heading_path: str | None) -> str:
    """``"title > heading path"``, just the title, or ``""``."""
    return HEADING_SEPARATOR.join(p for p in (title, heading_path) if p)


def build_embedding_text(title: str, heading_path: str | None, content: str) -> str:
    """``"title > heading path"`` (or just the title) + blank line + content."""
    prefix = embedding_prefix(title, heading_path)
    return f"{prefix}\n\n{content}" if prefix else content


def contextual_text(prefix: str, context: str | None, content: str) -> str:
    """The prefix line, the situating sentence on the next line, blank line, content.

    The text the indexer embeds and retrieval reranks for an article chunk
    (#697). With no ``context`` it equals :func:`build_embedding_text`.
    """
    head = "\n".join(p for p in (prefix, context) if p)
    return f"{head}\n\n{content}" if head else content


def content_hash(text: str) -> str:
    """sha256 hex of the embedding text — the indexer's re-embed key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack_paragraphs(paragraphs: list[str]) -> list[str]:
    """Pack one section's paragraphs into chunk texts of ~MIN–MAX tokens."""
    return [text for text, _span in pack_paragraph_spans(paragraphs)]


def pack_paragraph_spans(paragraphs: list[str]) -> list[tuple[str, str]]:
    """:func:`pack_paragraphs` plus each chunk's source span (#713).

    The span names the first and last paragraph the chunk packs (0-based within
    the section) as ``"<first>-<last>"``; a piece of a paragraph split for
    being over ``MAX_TOKENS`` is ``"<paragraph>.<piece>/<pieces>"``. The
    overlap a later chunk opens with is not part of its span.
    """
    units: list[tuple[str, str]] = []
    for p_index, paragraph in enumerate(paragraphs):
        if estimate_tokens(paragraph) > MAX_TOKENS:
            pieces = _split_oversized(paragraph)
            units.extend((piece, f"{p_index}.{k}/{len(pieces)}") for k, piece in enumerate(pieces))
        else:
            units.append((paragraph, str(p_index)))
    if not units:
        return []

    total = sum(estimate_tokens(u) for u, _ in units)
    if total <= MAX_TOKENS:
        return [("\n\n".join(u for u, _ in units), _span(units))]

    # Evenly sized chunks: aim each group at remaining / n, where n is the
    # fewest chunks that can each stay under MAX (recomputed after every
    # flush), and close a group when the next unit would overshoot that aim by
    # more than half of itself — so a section never ends in a stub.
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    size = 0
    remaining = total
    target = remaining / math.ceil(remaining / MAX_TOKENS)
    for unit in units:
        tokens = estimate_tokens(unit[0])
        if current and (size + tokens > MAX_TOKENS or size + tokens / 2 > target):
            groups.append(current)
            remaining -= size
            target = remaining / math.ceil(remaining / MAX_TOKENS)
            current, size = [], 0
        current.append(unit)
        size += tokens
    if current:
        groups.append(current)
    # A short tail still left over is folded back when the result fits.
    if len(groups) > 1:
        tail = sum(estimate_tokens(u) for u, _ in groups[-1])
        before = sum(estimate_tokens(u) for u, _ in groups[-2])
        if tail < MIN_TOKENS and tail + before <= MAX_TOKENS:
            groups[-2:] = [groups[-2] + groups[-1]]

    out = [("\n\n".join(u for u, _ in groups[0]), _span(groups[0]))]
    for previous, group in zip(groups, groups[1:], strict=False):
        overlap = _trailing_overlap(previous[-1][0])
        texts = [u for u, _ in group]
        out.append(("\n\n".join(([overlap] if overlap else []) + texts), _span(group)))
    return out


def _span(units: list[tuple[str, str]]) -> str:
    return f"{units[0][1]}-{units[-1][1]}"


def section_shape(sections: list[Section]) -> str:
    """A short fingerprint of an article body's structure (#713).

    The number of sections and each one's paragraph count, hashed: a faithful
    translation has the same shape, so a chunk's twin key only matches across
    languages when both bodies split the same way.
    """
    counts = ",".join(str(len(s.paragraphs)) for s in sections)
    return hashlib.sha256(counts.encode("ascii")).hexdigest()[:10]


def twin_key(shape: str, section_index: int, span: str) -> str:
    """The language-neutral key pairing an EN chunk with its ES twin (#713)."""
    return f"{shape}:{section_index}:{span}"


def _trailing_overlap(paragraph: str) -> str:
    """Up to OVERLAP_TOKENS of the paragraph's last whole sentences ('' if none fit)."""
    picked: list[str] = []
    size = 0
    for sentence in reversed(_sentences(paragraph)):
        tokens = estimate_tokens(sentence)
        if size + tokens > OVERLAP_TOKENS:
            break
        picked.insert(0, sentence)
        size += tokens
    return " ".join(picked)


def _split_oversized(paragraph: str) -> list[str]:
    """Split a paragraph over MAX_TOKENS by sentence, then by word if needed."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in _sentences(paragraph):
        for part in (
            _split_words(sentence) if estimate_tokens(sentence) > MAX_TOKENS else [sentence]
        ):
            tokens = estimate_tokens(part)
            if current and size + tokens > MAX_TOKENS:
                pieces.append(" ".join(current))
                current, size = [], 0
            current.append(part)
            size += tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _split_words(text: str) -> list[str]:
    """Hard split a run-on sentence into word groups of at most MAX_TOKENS."""
    limit = MAX_TOKENS * CHARS_PER_TOKEN
    groups: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if current and len(candidate) > limit:
            groups.append(current)
            current = word
        else:
            current = candidate
    if current:
        groups.append(current)
    return groups


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _clean_heading(text: str) -> str:
    """Drop emphasis markers around a heading's text."""
    return re.sub(r"^[*_]+|[*_]+$", "", text.strip()).strip()


def _normalize(text: str) -> str:
    return " ".join(_clean_heading(text).casefold().split())
