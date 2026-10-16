"""Article indexer: chunk -> hash-compare -> embed only what changed (#624, epic #617).

:func:`index_article` keeps one article's ``ARTICLE`` rows in
``knowledge_chunks`` in step with its ``content`` / ``content_es``:

1. **Read** (own ``SessionLocal()``, closed before any I/O): the article's
   titles and bodies, plus the ``content_hash`` of every existing chunk row and
   whether it already has a vector.
2. **Plan** (pure): chunk both languages with
   :func:`app.services.knowledge.chunker.chunk_article`, then match the wanted
   chunks to existing rows **by content hash within a language**, not by
   position. A chunk whose hash already has an embedded row is *kept* (only its
   ``chunk_index`` / meta are rewritten, and only when one of them changed:
   an unchanged row is never written, #728), so editing one section re-embeds
   that section's chunks even when a split shifts every later index. Rows whose
   hash is no longer wanted are deleted.
3. **Embed** the new hashes through :func:`embeddings.embed_documents`, with
   **no session open** (the Connection Pool rule in CLAUDE.md).
4. **Write** in a fresh session, holding the article row ``FOR UPDATE`` so two
   reindexes of one article serialise. The bodies are re-read under the lock:
   if they changed while we were embedding, this run stops (``superseded``)
   because the write that changed them scheduled its own reindex. The vector
   column is not an ORM attribute, so it is written through Core.

When nothing changed there is no embedding call and no write, so re-running the
sweep (``python -m scripts.reindex_knowledge``) over an unchanged corpus is free.

**Situating sentences (#697).** Between the plan and the embed, every chunk that
needs one (a new chunk, or a kept chunk whose row has no ``meta["context"]``,
e.g. indexed before #697 or after a failed call) gets a one-line summary from
:func:`app.services.knowledge.context.situate`: one Haiku call per article
language, also with no session open. The chunk is embedded as ``title > heading
path`` + the sentence + the chunk (:attr:`Chunk.text_to_embed`), and the
sentence is stored in ``meta["context"]``. ``content_hash`` stays over the text
without the sentence, so matching rows does not depend on the model. A kept row
that gains a sentence is replaced (old row deleted, new one inserted) in the
same write transaction as the rest of the article, so readers never see the
article half-indexed. If the call fails, or there is no ``ANTHROPIC_API_KEY``,
the chunk is embedded without a sentence and a later run retries it; with no
key nothing is retried (no churn in dev).

**Retry cap (#712).** A chunk whose call returned no sentence records it in its
row's ``meta`` (``context_attempts``, ``context_failed_at``). After
:data:`~app.services.knowledge.context.MAX_ATTEMPTS` such calls the chunk is no
longer asked for, so an article whose chunk Haiku never situates goes back to
``unchanged`` instead of paying one more call on every save. The count belongs
to the content hash: an edited chunk is a new row and starts again at zero.
``retry_context=True`` (``reindex_knowledge --retry-context``) ignores the count
for one run and restarts it. A budget stop is not counted (it says nothing
about the chunk).

Sentences are matched **by content hash only**, not by (language, hash): the
sentences one run generates are a ``hash -> sentence`` map, so two identical
chunks (the same text in both languages, or repeated in one article) share one
sentence. That is harmless: identical text needs the same situating.

**Coverage:** every article with a body, whatever its status. Visibility is
decided at query time (retrieval joins ``articles``), so a draft or a scheduled
article is indexed now and simply filtered out of public queries until it is
live, and internal callers (pipeline memory) can see it.

**Never raises.** It runs as a ``BackgroundTask`` behind article writes
(:func:`schedule_reindex`), so a failure is logged and sent to Sentry and the
next sweep repairs it. An empty ``VOYAGE_API_KEY`` is not a failure: the run is
skipped with an info log, before any DB access.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import sentry_sdk
from fastapi import BackgroundTasks
from sqlalchemy import delete, insert, select, update

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.enums import KnowledgeSourceType, Language
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings
from app.services.knowledge import context as chunk_context
from app.services.knowledge.chunker import (
    CONTEXT_ATTEMPTS_KEY,
    CONTEXT_FAILED_AT_KEY,
    CONTEXT_KEY,
    Chunk,
    chunk_article,
    estimate_tokens,
)
from app.services.llm import LLMUsage

logger = logging.getLogger(__name__)

FEATURE = "knowledge_index"
SOURCE_TYPE = KnowledgeSourceType.ARTICLE.value

_TABLE = KnowledgeChunk.__table__
_C = _TABLE.c

# Stand-in sentence for a dry run's token estimate (the measured mean length of
# a generated sentence is about 200 characters, #697).
DRY_RUN_CONTEXT = "x" * 200


@dataclass
class IndexResult:
    """What one :func:`index_article` run did (or, for a dry run, would do)."""

    article_id: int | None  # None for the verified-claims KB (no article)
    # indexed | unchanged | dry_run | missing | disabled | superseded | failed
    status: str
    chunks: int = 0  # wanted chunks, both languages
    embedded: int = 0  # chunks (to be) embedded
    kept: int = 0  # chunks reused with their existing vector
    # Kept rows (to be) UPDATEd because a stored value changed (#728); the
    # other kept rows are not written at all.
    updated: int = 0
    deleted: int = 0  # stale rows (to be) removed
    embed_tokens: int = 0  # estimated tokens of the chunks to embed
    by_language: dict[str, int] = field(default_factory=dict)
    source_type: str = SOURCE_TYPE
    # Situating sentences (#697): chunks that needed one, how many got one (a
    # dry run: 0), the Haiku calls, tokens and cost (estimated for a dry run).
    context_wanted: int = 0
    context_written: int = 0
    context_calls: int = 0
    context_input_tokens: int = 0
    context_output_tokens: int = 0
    context_cost_usd: float = 0.0
    # Article chunks without a sentence (#712): for a dry run or an unchanged
    # article the rows that lack one now, otherwise after this run;
    # ``context_capped`` of them have used every attempt and are not retried.
    context_missing: int = 0
    context_capped: int = 0


@dataclass(frozen=True)
class _Existing:
    id: int
    language: str
    content_hash: str
    embedded: bool
    context: str | None = None
    attempts: int = 0  # situating calls that returned no sentence (#712)
    failed_at: str | None = None


@dataclass
class _Plan:
    wanted: list[Chunk]  # with ``meta["language"]`` set
    keep: dict[int, Chunk]  # existing row id -> the chunk it now holds
    new: list[Chunk]
    stale_ids: list[int]


def schedule_reindex(background_tasks: BackgroundTasks, article_id: int) -> None:
    """Queue :func:`index_article` to run after the response is sent.

    Call it from every endpoint that writes an article's title or body (see
    ``tests/test_knowledge_indexer.py::REINDEX_WRITE_SITES``). It never fails the
    write: the task itself never raises.
    """
    background_tasks.add_task(index_article, article_id)


def schedule_series_reindex(db, background_tasks: BackgroundTasks, parent_id: int) -> None:
    """Queue a reindex of every article in a series (the parent and its children).

    Series sections are injected into every member at once. The chunker strips
    them again, so this normally embeds nothing; it is here so a write path that
    also touches the body is still covered.
    """
    ids = db.execute(
        select(Article.id).where(
            (Article.id == parent_id) | (Article.series_parent_id == parent_id)
        )
    ).scalars()
    for article_id in ids:
        schedule_reindex(background_tasks, article_id)


def wanted_chunks(article: Article) -> list[Chunk]:
    """Both languages' chunks for an article (ES uses the EN title as a fallback)."""
    chunks = chunk_article(article.title, article.content, Language.EN.value)
    chunks += chunk_article(
        article.title_es or article.title, article.content_es, Language.ES.value
    )
    return chunks


def _article_rows(article_id: int) -> tuple:
    """The ``WHERE`` criteria selecting one article's ARTICLE chunk rows."""
    return (_C.source_type == SOURCE_TYPE, _C.source_id == article_id)


def _load_existing(db, *criteria) -> list[_Existing]:
    """Every chunk row matching *criteria* (the rows one indexing run owns)."""
    rows = db.execute(
        select(
            _C.id,
            _C.language,
            _C.content_hash,
            _C.embedding.is_not(None),
            _C.meta,
        ).where(*criteria)
    ).all()
    out = []
    for row_id, language, hash_, embedded, meta in rows:
        meta = meta if isinstance(meta, dict) else {}
        attempts = meta.get(CONTEXT_ATTEMPTS_KEY)
        if not isinstance(attempts, int) or isinstance(attempts, bool):
            attempts = 0
        out.append(
            _Existing(
                row_id,
                language,
                hash_,
                bool(embedded),
                meta.get(CONTEXT_KEY) or None,
                attempts,
                meta.get(CONTEXT_FAILED_AT_KEY) or None,
            )
        )
    return out


def _reset_attempts(existing: list[_Existing]) -> list[_Existing]:
    """``existing`` with every attempt count cleared (``retry_context``, #712)."""
    return [replace(row, attempts=0, failed_at=None) for row in existing]


def with_context(chunk: Chunk, context: str | None) -> Chunk:
    """``chunk`` carrying ``context`` in its meta (unchanged when ``context`` is empty)."""
    if not context:
        return chunk
    return replace(chunk, meta={**chunk.meta, CONTEXT_KEY: context})


def with_attempts(chunk: Chunk, attempts: int, failed_at: str | None) -> Chunk:
    """``chunk`` carrying its failed-attempt count (#712; unchanged when it is 0)."""
    if not attempts:
        return chunk
    meta = {**chunk.meta, CONTEXT_ATTEMPTS_KEY: attempts}
    if failed_at:
        meta[CONTEXT_FAILED_AT_KEY] = failed_at
    return replace(chunk, meta=meta)


def _plan(
    wanted: list[Chunk],
    existing: list[_Existing],
    *,
    contexts: dict[str, str] | None = None,
    failed: set[str] | None = None,
    failed_at: str | None = None,
) -> _Plan:
    """Match wanted chunks to embedded rows by (language, hash); the rest is new / stale.

    A kept chunk takes over its row's situating sentence or, without one, its
    failed-attempt count (#712). ``contexts`` (hash -> a sentence this run
    generated, #697) lets such a hash match only a row that already has a
    sentence, so a row without one is replaced; new chunks carry their
    generated sentence. A chunk still without a sentence whose hash is in
    ``failed`` (this run asked for one and got none) counts one more attempt.
    """
    contexts = contexts or {}
    failed = failed or set()

    def counted(chunk: Chunk, attempts: int, last: str | None) -> Chunk:
        if not chunk.context and chunk.content_hash in failed:
            attempts, last = attempts + 1, failed_at
        return with_attempts(chunk, attempts, last)

    pool: dict[tuple[str, str], list[_Existing]] = defaultdict(list)
    for row in sorted(existing, key=lambda r: r.id):
        if row.embedded:
            pool[(row.language, row.content_hash)].append(row)
    keep: dict[int, Chunk] = {}
    new: list[Chunk] = []
    for chunk in wanted:
        key = (chunk.meta["language"], chunk.content_hash)
        matches = pool.get(key) or []
        if chunk.content_hash in contexts:
            matches = [row for row in matches if row.context]
        if matches:
            row = matches[0]
            pool[key].remove(row)
            keep[row.id] = counted(with_context(chunk, row.context), row.attempts, row.failed_at)
        else:
            new.append(counted(with_context(chunk, contexts.get(chunk.content_hash)), 0, None))
    stale_ids = [row.id for row in existing if row.id not in keep]
    return _Plan(wanted, keep, new, stale_ids)


def _attempts(chunk: Chunk) -> int:
    return (chunk.meta or {}).get(CONTEXT_ATTEMPTS_KEY) or 0


def _missing_context(plan: _Plan) -> list[Chunk]:
    """Kept chunks whose row has no situating sentence and attempts left (#697, #712)."""
    return [
        chunk
        for chunk in plan.keep.values()
        if not chunk.context and _attempts(chunk) < chunk_context.MAX_ATTEMPTS
    ]


def _sentence_less(plan: _Plan) -> tuple[int, int]:
    """(chunks without a sentence, those of them out of attempts) in ``plan`` (#712)."""
    missing = [c for c in [*plan.keep.values(), *plan.new] if not c.context]
    capped = sum(1 for c in missing if _attempts(c) >= chunk_context.MAX_ATTEMPTS)
    return len(missing), capped


def _result(
    article_id: int | None,
    status: str,
    plan: _Plan,
    source_type: str = SOURCE_TYPE,
    *,
    context_wanted: int = 0,
    situated: _Situated | None = None,
    written: int = 0,
    sentence_less: tuple[int, int] = (0, 0),
    updated: int = 0,
) -> IndexResult:
    by_language: dict[str, int] = defaultdict(int)
    for chunk in plan.wanted:
        by_language[chunk.meta["language"]] += 1
    situated = situated or _Situated()
    return IndexResult(
        context_missing=sentence_less[0],
        context_capped=sentence_less[1],
        context_wanted=context_wanted,
        context_written=written,
        context_calls=situated.calls,
        context_input_tokens=situated.usage.input_tokens
        + situated.usage.cache_read_input_tokens
        + situated.usage.cache_creation_input_tokens,
        context_output_tokens=situated.usage.output_tokens,
        context_cost_usd=situated.cost_usd,
        article_id=article_id,
        status=status,
        chunks=len(plan.wanted),
        embedded=len(plan.new),
        kept=len(plan.keep),
        updated=updated,
        deleted=len(plan.stale_ids),
        embed_tokens=sum(estimate_tokens(c.text_to_embed) for c in plan.new),
        by_language=dict(by_language),
        source_type=source_type,
    )


def _article_row_values(article_id: int):
    """``chunk -> column values`` for one article's ARTICLE chunks."""
    return lambda chunk: _row_values(article_id, chunk)


def _row_values(article_id: int, chunk: Chunk) -> dict:
    return {
        "heading_path": chunk.heading_path,
        "content": chunk.content,
        "content_hash": chunk.content_hash,
        "token_count": chunk.token_count,
        "meta": chunk.meta,
        "source_type": SOURCE_TYPE,
        "source_id": article_id,
        "article_id": article_id,
        "language": chunk.meta["language"],
        "chunk_index": chunk.chunk_index,
    }


# The columns of uq_knowledge_chunks_source_chunk a kept row can move along
# (source_type and language are fixed by the plan's ownership and matching).
_UNIQUE_MOVING = ("source_id", "chunk_index")


def _changed_kept(db, plan: _Plan, row_values) -> dict[int, bool]:
    """Kept row id -> "its unique key moves", for rows whose values changed (#728).

    Every column the write would set is compared (``source_id`` too: a claim
    re-extracted with the same text keeps its vector but moves to the new
    ``fact_check_claims`` id, #625). A row missing from the result already
    holds exactly the values it would be given, so it is never written: every
    ``UPDATE`` leaves a dead tuple and a dead HNSW entry (#711).
    """
    if not plan.keep:
        return {}
    names = list(row_values(next(iter(plan.keep.values()))))
    rows = db.execute(
        select(_C.id, *(_C[name] for name in names)).where(_C.id.in_(list(plan.keep)))
    ).all()
    changed = {}
    for row_id, *current in rows:
        stored = dict(zip(names, current, strict=True))
        values = row_values(plan.keep[row_id])
        if any(stored[name] != values[name] for name in names):
            changed[row_id] = any(stored[name] != values[name] for name in _UNIQUE_MOVING)
    return changed


def _write(db, plan: _Plan, vectors: dict[str, list[float]], row_values) -> int:
    """Apply the plan in the caller's transaction (deletes, updates, inserts).

    ``row_values(chunk)`` gives a chunk's location and content columns. Only
    kept rows whose values changed are updated (#728): an unchanged row is
    never touched. Returns the number of kept rows updated.
    """
    if plan.stale_ids:
        db.execute(delete(_TABLE).where(_C.id.in_(plan.stale_ids)))
    changed = _changed_kept(db, plan, row_values)
    # Two passes so a row moving to a key another changed row still holds
    # never trips uq_knowledge_chunks_source_chunk (it is not deferrable).
    # Only moving rows step aside (to a negative index): an unchanged row keeps
    # its key, and no other wanted chunk can want it (wanted keys are unique).
    for row_id, moves in changed.items():
        if moves:
            db.execute(update(_TABLE).where(_C.id == row_id).values(chunk_index=-row_id))
    for row_id in changed:
        db.execute(update(_TABLE).where(_C.id == row_id).values(**row_values(plan.keep[row_id])))
    model = embeddings.document_model()
    for chunk in plan.new:
        db.execute(
            insert(_TABLE).values(
                **row_values(chunk),
                embedding=vectors[chunk.content_hash],
                embedding_model=model,
            )
        )
    return len(changed)


def _embed_new(plan: _Plan) -> dict[str, list[float]]:
    """Embed the plan's new chunks (call with NO session open); hash -> vector.

    The text is :attr:`Chunk.text_to_embed`: the hashed text plus the chunk's
    situating sentence when it has one (#697).
    """
    if not plan.new:
        return {}
    unique = list(dict.fromkeys(c.text_to_embed for c in plan.new))
    embedded = embeddings.embed_documents(unique, feature=FEATURE)
    by_text = dict(zip(unique, embedded, strict=True))
    return {c.content_hash: by_text[c.text_to_embed] for c in plan.new}


@dataclass
class _Situated:
    contexts: dict[str, str] = field(default_factory=dict)  # content hash -> sentence
    # Hashes a call was made for that got no sentence (#712; not a budget stop).
    failed: set[str] = field(default_factory=set)
    calls: int = 0
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: float = 0.0


def _titles(article: Article) -> dict[str, str]:
    return {
        Language.EN.value: (article.title or "").strip(),
        Language.ES.value: (article.title_es or article.title or "").strip(),
    }


def _context_targets(wanted: list[Chunk], need: list[Chunk]) -> dict[str, tuple[list, list]]:
    """language -> (every chunk of that language, the chunk indexes needing a sentence)."""
    targets: dict[str, tuple[list, list]] = {}
    for chunk in need:
        language = chunk.meta["language"]
        if language not in targets:
            targets[language] = ([c for c in wanted if c.meta["language"] == language], [])
        targets[language][1].append(chunk.chunk_index)
    return targets


def _situate(
    article_id: int, titles: dict[str, str], wanted: list[Chunk], need: list[Chunk]
) -> _Situated:
    """Generate the missing situating sentences (call with NO session open). Never raises."""
    out = _Situated()
    for language, (chunks, indexes) in _context_targets(wanted, need).items():
        result = chunk_context.situate(
            titles.get(language, ""), language, chunks, indexes, log_label=f"article {article_id}"
        )
        out.calls += 1
        if result.usage:
            out.usage = out.usage + result.usage
        out.cost_usd += result.cost_usd
        by_index = {c.chunk_index: c for c in chunks}
        for index, sentence in result.contexts.items():
            out.contexts[by_index[index].content_hash] = sentence
        if not result.status.startswith("budget:"):
            out.failed.update(by_index[i].content_hash for i in indexes if i not in result.contexts)
    # Sentences are keyed by hash only: an identical chunk that got one covers it.
    out.failed -= out.contexts.keys()
    return out


def _estimate_situate(titles: dict[str, str], wanted: list[Chunk], need: list[Chunk]) -> _Situated:
    """Dry-run estimate of :func:`_situate` (no call)."""
    out = _Situated()
    for language, (chunks, indexes) in _context_targets(wanted, need).items():
        usage = chunk_context.estimate(titles.get(language, ""), language, chunks, indexes)
        out.calls += 1
        out.usage = out.usage + usage
        out.cost_usd += chunk_context.estimate_cost(usage)
    return out


def _drop_unembedded(plan: _Plan, vectors: dict[str, list[float]], label: str) -> None:
    """Drop new chunks with no vector (they appeared after the embed); the sweep adds them."""
    missing = [c for c in plan.new if c.content_hash not in vectors]
    if missing:
        logger.warning(
            "knowledge index for %s: %d chunk(s) lack a vector; left for the sweep",
            label,
            len(missing),
        )
        plan.new = [c for c in plan.new if c.content_hash in vectors]


def index_article(
    article_id: int, *, dry_run: bool = False, retry_context: bool = False
) -> IndexResult:
    """Bring one article's ARTICLE chunks up to date. Never raises (see module doc).

    ``dry_run`` computes the plan (counts and estimated tokens) without calling
    Voyage or Haiku or writing, and works with no ``VOYAGE_API_KEY``.
    ``retry_context`` asks again for chunks that used up their situating
    attempts (#712) and restarts their count.
    """
    try:
        return _index_article(article_id, dry_run=dry_run, retry_context=retry_context)
    except Exception as exc:  # noqa: BLE001 — a background task must not raise
        logger.exception("knowledge index failed for article %d", article_id)
        sentry_sdk.capture_exception(exc)
        return IndexResult(article_id=article_id, status="failed")


def _index_article(article_id: int, *, dry_run: bool, retry_context: bool = False) -> IndexResult:
    if not dry_run and not embeddings.embeddings_enabled():
        logger.info("knowledge index skipped for article %d: embeddings disabled", article_id)
        return IndexResult(article_id=article_id, status="disabled")

    # 1. Read, then release the connection before any LLM / embedding I/O.
    with SessionLocal() as db:
        article = db.get(Article, article_id)
        if article is None:
            # A deleted article's chunks cascade with it; nothing to do.
            return IndexResult(article_id=article_id, status="missing")
        wanted = wanted_chunks(article)
        titles = _titles(article)
        existing = _load_existing(db, *_article_rows(article_id))
        sentence_less = _sentence_less(_plan(wanted, existing))
        # Only with a key: without one nothing is asked, so nothing is reset.
        reset = retry_context and chunk_context.context_enabled()
        if reset:
            existing = _reset_attempts(existing)
        plan = _plan(wanted, existing)
        changed = _changed_kept(db, plan, _article_row_values(article_id))
        reorder = bool(plan.stale_ids) or bool(changed)

    # Chunks that need a situating sentence (#697): every new one, plus kept
    # rows indexed without one that have attempts left (#712). None when there
    # is no Anthropic key.
    need = (plan.new + _missing_context(plan)) if chunk_context.context_enabled() else []
    if not plan.new and not reorder and not need:
        return _result(article_id, "unchanged", plan, sentence_less=sentence_less)
    if dry_run:
        # Assume every sentence is written: kept rows without one are replaced.
        placeholder = {c.content_hash: DRY_RUN_CONTEXT for c in need}
        dry_plan = _plan(wanted, existing, contexts=placeholder)
        return _result(
            article_id,
            "dry_run",
            dry_plan,
            context_wanted=len(need),
            situated=_estimate_situate(titles, wanted, need),
            sentence_less=sentence_less,
            updated=sum(1 for row_id in changed if row_id in dry_plan.keep),
        )

    # 2. Situate and embed, with no session open. A kept row that got a
    # sentence is re-planned as new (its old row becomes stale).
    situated = _situate(article_id, titles, wanted, need) if need else _Situated()
    failed_at = datetime.now(UTC).isoformat(timespec="seconds")
    if situated.contexts:
        plan = _plan(wanted, existing, contexts=situated.contexts)
    vectors = _embed_new(plan)

    # 3. Write under the article row lock, against a fresh read of the bodies.
    with SessionLocal() as db:
        article = db.execute(
            select(Article).where(Article.id == article_id).with_for_update(of=Article)
        ).scalar_one_or_none()
        if article is None:
            return IndexResult(article_id=article_id, status="missing")
        fresh = wanted_chunks(article)
        if [c.content_hash for c in fresh] != [c.content_hash for c in wanted]:
            logger.info(
                "knowledge index for article %d superseded: content changed mid-run",
                article_id,
            )
            return _result(
                article_id, "superseded", plan, context_wanted=len(need), situated=situated
            )
        # Existing rows may have moved since the read (a concurrent run that
        # committed first); re-plan against them and embed nothing new here.
        current = _load_existing(db, *_article_rows(article_id))
        plan = _plan(
            fresh,
            _reset_attempts(current) if reset else current,
            contexts=situated.contexts,
            failed=situated.failed,
            failed_at=failed_at,
        )
        _drop_unembedded(plan, vectors, f"article {article_id}")
        updated = _write(db, plan, vectors, _article_row_values(article_id))
        db.commit()

    written = sum(1 for c in plan.new if c.context)
    result = _result(
        article_id,
        "indexed",
        plan,
        context_wanted=len(need),
        situated=situated,
        written=written,
        sentence_less=_sentence_less(plan),
        updated=updated,
    )
    logger.info(
        "knowledge index: article=%d chunks=%d embedded=%d kept=%d updated=%d deleted=%d "
        "context=%d/%d context_missing=%d context_capped=%d context_cost_usd=%.5f",
        article_id,
        result.chunks,
        result.embedded,
        result.kept,
        result.updated,
        result.deleted,
        written,
        len(need),
        result.context_missing,
        result.context_capped,
        situated.cost_usd,
    )
    return result
