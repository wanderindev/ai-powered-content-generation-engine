"""Knowledge retrieval: vector, bilingual full-text, hybrid RRF, optional rerank (#626).

:func:`search` is the **one** reader of ``knowledge_chunks``. Ask PIC (#619),
pipeline memory (#618) and the MCP read tools all go through it, and
``tests/test_knowledge_retrieval.py`` fails on any other module under ``app/``
that names the table or the model (the indexers in this package write it).

Stages, each over the same visibility / language / source-type filters:

* **vector** — cosine distance ``embedding <=> :q`` on the HNSW
  ``vector_cosine_ops`` index, against ``embeddings.embed_query``. The scan is
  iterative (``hnsw.iterative_scan = relaxed_order``, pgvector >= 0.8, #722)
  so the filters below cannot starve the pool, and the rows are re-sorted by
  distance because relaxed order is only approximately ordered.
* **fts** — the stored generated ``search_vector`` (``english`` or ``spanish``
  by row language, over ``pic_unaccent``; ``app/models/search_sql.py``) matched
  against ``websearch_to_tsquery('english', pic_unaccent(q)) ||
  websearch_to_tsquery('spanish', pic_unaccent(q))``: a query matches under
  either config, so a Spanish question still stems as Spanish. Ranked by
  ``ts_rank``.
* **hybrid** — reciprocal rank fusion of the two lists, ``1 / (RRF_K + rank)``
  summed per chunk (``RRF_K = 60``).
* **rerank** (opt-in) — Voyage rerank over the top ``RERANK_TOP_N`` fused
  chunks; its order replaces the fused one. An article chunk is reranked as
  the text it was embedded from (:func:`rerank_text`, #697): ``title > heading
  path``, its situating sentence when it has one, then the chunk. Claim chunks
  are reranked as their bare text.

Results are grouped by article (claim-KB chunks, which have no article, are a
group each), best chunk first, and every chunk carries its per-stage ranks and
scores so the eval harness (#627) can compare modes from one call.

**Visibility** is decided here, at query time, never stored:

* ``public`` — INNER JOIN ``articles``: APPROVED, ``published_at <= now()``,
  taxonomy ≠ ``EXCLUDED_TAXONOMY_ID``. ``VERIFIED_CLAIM`` chunks have no
  article, so the inner join drops them: Ask PIC cites articles only.
* ``internal`` — LEFT JOIN: article chunks of APPROVED articles **including
  scheduled ones** (and, as the ticket defines it, any taxonomy), plus every
  ``VERIFIED_CLAIM`` chunk (pipeline memory, MCP).

**Facts.** ``VERIFIED_CLAIM`` chunks include INACCURATE and DISPUTED KB rows
(an INACCURATE chunk's first line is the *falsehood*). ``facts_only=True``
keeps only ``meta.verdict = 'VERIFIED'`` among them; any use of a KB chunk as
supporting evidence must pass it. ``ARTICLE_CLAIM`` chunks are indexed only for
VERIFIED (claim text) and CORRECTED (correction text) claims, so all of them
are facts already.

**Degradation.** With no ``VOYAGE_API_KEY``, or a failed query embedding,
``mode="hybrid"`` runs FTS alone and says so in ``SearchResult.degraded``; it
never raises. ``mode="vector"`` raises the seam's ``EmbeddingsDisabledError`` /
``EmbeddingsError``. A failed rerank keeps the fused order and adds
``rerank_unavailable``. Query embedding and rerank use the seam's short
query-path budget.

Per-stage timings are logged at INFO (``knowledge search: ...``) under the
``app.`` namespace and returned in ``timings_ms``; the query text is not logged.

**Eval-scoped tables (#627).** ``table=`` points the same SQL at a copy of
``knowledge_chunks`` with the same columns (:func:`chunk_table_like`), which
the RAG eval (``scripts/eval_rag``) creates at runtime and fills with another
model's vectors; ``embedding_model=`` embeds the query with that model. Both
default to the live table and ``settings.embedding_model``, and no production
caller passes them.
"""

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Column, Float, MetaData, Table, func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.category import EXCLUDED_TAXONOMY_ID, Category
from app.models.enums import ContentStatus, KnowledgeSourceType, Language
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings
from app.services.knowledge.chunker import contextual_text, embedding_prefix

logger = logging.getLogger(__name__)

Mode = Literal["vector", "fts", "hybrid"]
Visibility = Literal["public", "internal"]
MODES: tuple[str, ...] = ("vector", "fts", "hybrid")
VISIBILITIES: tuple[str, ...] = ("public", "internal")

FEATURE = "knowledge_search"
RRF_K = 60
# Chunks each stage contributes before fusion / grouping.
DEFAULT_CANDIDATES = 50
# Fused chunks sent to the reranker.
RERANK_TOP_N = 50
DEFAULT_K = 10
# Only this KB verdict is a fact (see module doc).
FACT_VERDICT = "VERIFIED"

DEGRADED_EMBEDDINGS_DISABLED = "embeddings_disabled"
DEGRADED_EMBEDDING_FAILED = "embedding_failed"
DEGRADED_RERANK_UNAVAILABLE = "rerank_unavailable"

# pgvector's iterative index scan (#722): with ``relaxed_order`` the HNSW scan
# keeps going past ``ef_search`` until the filtered query has ``limit`` rows (or
# ``hnsw.max_scan_tuples``, default 20,000, are visited), so the visibility /
# source-type filter can no longer starve the pool. Needs pgvector >= 0.8.
ITERATIVE_SCAN = "relaxed_order"
# The iterative scan's stop (#729). pgvector's default, 20,000 index tuples
# (dead entries included), is only ~1.4x the table on 2026-10-13 (14,101 rows,
# 46% VERIFIED_CLAIM, which the public filter drops), so growth alone would
# start underfilling filtered queries again. The scan stops as soon as the
# query has ``limit`` rows, so the cap only matters for a filter that matches
# fewer rows than that (or very sparsely): there it bounds the worst case. A
# full walk of the 14.1k-row prod index took ~120-145 ms p50 server-side, so
# 50,000 (3.5x today's table) bounds it near 0.5 s. A fixed value, not a
# per-search COUNT: see docs/claude/knowledge.md. The underfill WARNING in
# :func:`_vector_stage` says when it needs raising.
MAX_SCAN_TUPLES = 50_000
ITERATIVE_SCAN_MIN_VERSION = (0, 8)
# Per process: None = not checked yet. One process talks to one database.
_iterative_scan_supported: bool | None = None

_TABLE = KnowledgeChunk.__table__
_EN = literal_column("'english'::regconfig")
_ES = literal_column("'spanish'::regconfig")


@dataclass
class ChunkHit:
    """One retrieved chunk with every stage's rank (1-based) and score."""

    chunk_id: int
    source_type: str
    source_id: int
    article_id: int | None
    language: str
    chunk_index: int
    heading_path: str | None
    content: str
    meta: dict | None
    vector_rank: int | None = None
    vector_distance: float | None = None
    fts_rank: int | None = None
    fts_score: float | None = None
    rrf_score: float | None = None
    rerank_rank: int | None = None
    rerank_score: float | None = None
    # Position in the final chunk order (after fusion / rerank).
    rank: int = 0
    # The article's title in the chunk's language (EN as the ES fallback).
    article_title: str | None = None


@dataclass
class ArticleHit:
    """Chunks grouped by article (or one KB claim): best chunk + all matches."""

    rank: int
    article_id: int | None
    title: str | None
    title_es: str | None
    slug: str | None
    slug_es: str | None
    best: ChunkHit
    chunks: list[ChunkHit]


@dataclass
class SearchResult:
    query: str
    mode: str
    visibility: str
    reranked: bool
    degraded: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    results: list[ArticleHit] = field(default_factory=list)
    # The full ranked chunk list (before grouping and the ``k`` cut), for the eval.
    chunks: list[ChunkHit] = field(default_factory=list)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def chunk_table_like(name: str) -> Table:
    """A Core ``Table`` named ``name`` with ``knowledge_chunks``' columns.

    For the eval-scoped copies (see module doc): types only, no constraints or
    foreign keys, in its own ``MetaData`` so it never joins ``Base.metadata``
    (``create_all`` and the test TRUNCATE never see it). The physical table is
    the caller's to create.
    """
    return Table(
        name,
        MetaData(),
        *(Column(col.name, col.type, primary_key=col.primary_key) for col in _TABLE.columns),
    )


def public_article_criteria() -> list:
    """The public-article rule over ``Article`` / ``Category`` columns.

    APPROVED, ``published_at <= now()``, taxonomy ≠ ``EXCLUDED_TAXONOMY_ID``;
    the caller joins ``categories``. Shared with ``related.py`` (#629) so the
    rule has one definition in this package.
    """
    return [
        Article.status == ContentStatus.APPROVED.value,
        Article.published_at.is_not(None),
        Article.published_at <= func.now(),
        Category.taxonomy_id != EXCLUDED_TAXONOMY_ID,
    ]


def _filters(
    cols,
    *,
    visibility: str,
    languages: Sequence[str] | None,
    source_types: Sequence[str] | None,
    exclude_article_ids: Iterable[int],
    facts_only: bool,
) -> list:
    """WHERE criteria shared by every stage; the FROM is :func:`_base_from`."""
    criteria = []
    if visibility == "public":
        criteria += public_article_criteria()
    else:
        # LEFT JOIN: KB chunks (no article) pass; article chunks need APPROVED,
        # scheduled included.
        criteria.append(
            or_(cols.article_id.is_(None), Article.status == ContentStatus.APPROVED.value)
        )
    if languages:
        criteria.append(cols.language.in_(list(languages)))
    if source_types:
        criteria.append(cols.source_type.in_(list(source_types)))
    excluded = list(exclude_article_ids)
    if excluded:
        criteria.append(or_(cols.article_id.is_(None), cols.article_id.not_in(excluded)))
    if facts_only:
        criteria.append(
            or_(
                cols.source_type != KnowledgeSourceType.VERIFIED_CLAIM.value,
                cols.meta["verdict"].astext == FACT_VERDICT,
            )
        )
    return criteria


def _base_from(table: Table, visibility: str):
    cols = table.c
    articles = Article.__table__
    categories = Category.__table__
    if visibility == "public":
        # Inner joins: never ``article_id IS NULL OR <public>`` — KB chunks stay out.
        return table.join(articles, articles.c.id == cols.article_id).join(
            categories, categories.c.id == articles.c.category_id
        )
    return table.outerjoin(articles, articles.c.id == cols.article_id)


def _parse_version(version: str) -> tuple[int, ...]:
    """``"0.8.6"`` -> ``(0, 8, 6)``; stops at the first non-numeric part."""
    parts = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _pgvector_version(db: Session) -> str | None:
    return db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()


def iterative_scan_supported(db: Session) -> bool:
    """Whether the database's pgvector has ``hnsw.iterative_scan`` (>= 0.8).

    Read once per process from ``pg_extension`` (a plain SELECT, so it cannot
    abort the caller's transaction) rather than by trying the ``SET``: on an
    older pgvector, setting an unknown ``hnsw.*`` parameter errors once the
    library is loaded and would poison the transaction, and before the library
    is loaded it silently succeeds, so a trial ``SET`` proves nothing.
    Unsupported logs one warning and the vector stage runs as before.
    """
    global _iterative_scan_supported
    if _iterative_scan_supported is None:
        version = _pgvector_version(db)
        supported = bool(version) and _parse_version(version) >= ITERATIVE_SCAN_MIN_VERSION
        if not supported:
            logger.warning(
                "knowledge search: pgvector %s lacks hnsw.iterative_scan (needs >= 0.8); "
                "the WHERE filter can starve the vector pool",
                version or "missing",
            )
        _iterative_scan_supported = supported
    return _iterative_scan_supported


def _vector_stage(db: Session, cols, vector: list[float], from_, criteria: list, limit: int):
    distance = cols.embedding.cosine_distance(vector)
    # HNSW yields at most ef_search rows before the WHERE filter applies; widen
    # it to the candidate pool, and (pgvector >= 0.8) let the scan iterate until
    # the filtered query fills ``limit`` (#722). Both for this transaction only
    # (is_local = true), in one statement.
    # ``hnsw.max_scan_tuples`` raises pgvector's stop for that scan (#729).
    gucs = [func.set_config("hnsw.ef_search", str(max(40, limit)), True)]
    iterative = iterative_scan_supported(db)
    if iterative:
        gucs.append(func.set_config("hnsw.iterative_scan", ITERATIVE_SCAN, True))
        gucs.append(func.set_config("hnsw.max_scan_tuples", str(MAX_SCAN_TUPLES), True))
    db.execute(select(*gucs))
    where = [cols.embedding.is_not(None), *criteria]
    stmt = (
        select(cols.id, distance.label("distance"))
        .select_from(from_)
        .where(*where)
        .order_by(distance)
        .limit(limit)
    )
    rows = [(row.id, float(row.distance)) for row in db.execute(stmt)]
    if iterative and len(rows) < limit:
        _warn_if_underfilled(db, from_, where, limit, len(rows))
    # relaxed_order may return rows slightly out of distance order; every
    # caller reads this list as a ranking (RRF rank, vector-mode order, the
    # floors in KB matching and the consistency check), so restore it.
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


def _warn_if_underfilled(db: Session, from_, where: list, limit: int, returned: int) -> None:
    """WARN when the iterative scan stopped before the filter ran out of rows (#729).

    Fewer rows than ``limit`` is normal when the filter matches fewer rows, so
    this counts the matching rows (capped at ``limit``; no ORDER BY, so no
    HNSW scan) and warns only when more exist than the scan returned: it hit
    ``hnsw.max_scan_tuples`` (or ``hnsw.scan_mem_multiplier``'s memory cap).
    Runs only on a short result, so a filled pool costs nothing extra.
    """
    matching = db.execute(
        select(func.count()).select_from(
            select(literal_column("1")).select_from(from_).where(*where).limit(limit).subquery()
        )
    ).scalar()
    if matching > returned:
        logger.warning(
            "knowledge search: vector stage underfilled: returned=%d of limit=%d, "
            "matching rows>=%d; the HNSW scan stopped early, raise MAX_SCAN_TUPLES "
            "(now %d) or vacuum knowledge_chunks",
            returned,
            limit,
            matching,
            MAX_SCAN_TUPLES,
        )


def _fts_stage(db: Session, cols, query: str, from_, criteria: list, limit: int):
    unaccented = func.pic_unaccent(query)
    tsq = func.websearch_to_tsquery(_EN, unaccented, type_=TSQUERY).op("||", return_type=TSQUERY)(
        func.websearch_to_tsquery(_ES, unaccented, type_=TSQUERY)
    )
    score = func.ts_rank(cols.search_vector, tsq, type_=Float)
    stmt = (
        select(cols.id, score.label("score"))
        .select_from(from_)
        .where(cols.search_vector.op("@@")(tsq), *criteria)
        .order_by(score.desc(), cols.id)
        .limit(limit)
    )
    return [(row.id, float(row.score)) for row in db.execute(stmt)]


def rrf_fuse(*ranked_lists: Sequence[int], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: ``sum(1 / (k + rank))`` per id, highest first.

    Each input is a list of ids, best first (rank 1). Ties keep first-seen order.
    """
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _load_chunks(db: Session, cols, ids: list[int]) -> dict[int, ChunkHit]:
    if not ids:
        return {}
    articles = Article.__table__
    stmt = (
        select(
            cols.id,
            cols.source_type,
            cols.source_id,
            cols.article_id,
            cols.language,
            cols.chunk_index,
            cols.heading_path,
            cols.content,
            cols.meta,
            articles.c.title,
            articles.c.title_es,
        )
        .select_from(cols.id.table.outerjoin(articles, articles.c.id == cols.article_id))
        .where(cols.id.in_(ids))
    )
    return {
        row.id: ChunkHit(
            chunk_id=row.id,
            source_type=row.source_type,
            source_id=row.source_id,
            article_id=row.article_id,
            language=row.language,
            chunk_index=row.chunk_index,
            heading_path=row.heading_path,
            content=row.content,
            meta=row.meta,
            article_title=(
                (row.title_es or row.title) if row.language == Language.ES.value else row.title
            ),
        )
        for row in db.execute(stmt)
    }


def rerank_text(hit: ChunkHit) -> str:
    """The document the reranker scores for a chunk (#697).

    An ``ARTICLE`` chunk: ``title > heading path``, its situating sentence
    (``meta["context"]``) when it has one, then the chunk; the same text the
    indexer embedded. Any other chunk (a claim): its bare text, as embedded.
    """
    if hit.source_type != KnowledgeSourceType.ARTICLE.value:
        return hit.content
    context = (hit.meta or {}).get("context")
    prefix = embedding_prefix((hit.article_title or "").strip(), hit.heading_path)
    return contextual_text(prefix, context, hit.content)


def _group(db: Session, chunks: list[ChunkHit], k: int) -> list[ArticleHit]:
    groups: dict[tuple, list[ChunkHit]] = {}
    for chunk in chunks:
        key = (
            ("article", chunk.article_id)
            if chunk.article_id is not None
            else (chunk.source_type, chunk.source_id)
        )
        groups.setdefault(key, []).append(chunk)
    top = list(groups.values())[:k]
    article_ids = [g[0].article_id for g in top if g[0].article_id is not None]
    articles = {}
    if article_ids:
        rows = db.execute(
            select(
                Article.id, Article.title, Article.title_es, Article.slug, Article.slug_es
            ).where(Article.id.in_(article_ids))
        )
        articles = {row.id: row for row in rows}
    hits = []
    for rank, members in enumerate(top, start=1):
        art = articles.get(members[0].article_id)
        hits.append(
            ArticleHit(
                rank=rank,
                article_id=members[0].article_id,
                title=art.title if art else None,
                title_es=art.title_es if art else None,
                slug=art.slug if art else None,
                slug_es=art.slug_es if art else None,
                best=members[0],
                chunks=members,
            )
        )
    return hits


def _rerank(result: SearchResult, query: str, chunks: list[ChunkHit], top_n: int) -> list[ChunkHit]:
    """Rerank the first ``top_n`` chunks against ``query``; the rest keep their order.

    A failed rerank keeps the given order and adds ``rerank_unavailable``.
    """
    t = time.perf_counter()
    head, tail = chunks[:top_n], chunks[top_n:]
    try:
        ranked = embeddings.rerank(query, [rerank_text(c) for c in head], feature=FEATURE)
    except (embeddings.EmbeddingsDisabledError, embeddings.EmbeddingsError):
        result.degraded.append(DEGRADED_RERANK_UNAVAILABLE)
    else:
        reordered = []
        for rank, item in enumerate(ranked, start=1):
            hit = head[item.index]
            hit.rerank_rank, hit.rerank_score = rank, item.relevance_score
            reordered.append(hit)
        seen = {id(c) for c in reordered}
        chunks = reordered + [c for c in head if id(c) not in seen] + tail
        result.reranked = True
    result.timings_ms["rerank"] = _ms(t)
    return chunks


def search(
    db: Session,
    query: str,
    *,
    mode: Mode = "hybrid",
    rerank: bool = False,
    k: int = DEFAULT_K,
    languages: Sequence[str] | None = None,
    source_types: Sequence[str] | None = None,
    visibility: Visibility = "public",
    exclude_article_ids: Iterable[int] = (),
    facts_only: bool = False,
    candidates: int = DEFAULT_CANDIDATES,
    table: Table | None = None,
    embedding_model: str | None = None,
    query_vector: list[float] | None = None,
) -> SearchResult:
    """Ranked knowledge search; see the module docstring for every rule.

    ``k`` is the number of **groups** (articles / KB claims) returned;
    ``candidates`` is how many chunks each stage contributes before fusion.
    The query embedding is computed before the first statement this function
    issues, so the Voyage call adds no DB time of its own. ``table`` /
    ``embedding_model`` are for the eval only (module doc).

    ``query_vector`` is a query embedding the caller already has (KB matching,
    #628, embeds a whole batch of claims in one ``embed_queries`` call before
    opening any statement): when given, no embedding call is made here, so the
    vector stage can neither be disabled nor fail.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    t_total = time.perf_counter()
    result = SearchResult(query=query, mode=mode, visibility=visibility, reranked=False)
    timings = result.timings_ms

    vector: list[float] | None = query_vector if mode in ("vector", "hybrid") else None
    if vector is None and mode in ("vector", "hybrid"):
        t = time.perf_counter()
        try:
            if embedding_model:
                vector = embeddings.embed_query(query, feature=FEATURE, model=embedding_model)
            else:
                vector = embeddings.embed_query(query, feature=FEATURE)
        except embeddings.EmbeddingsDisabledError:
            if mode == "vector":
                raise
            result.degraded.append(DEGRADED_EMBEDDINGS_DISABLED)
        except embeddings.EmbeddingsError:
            if mode == "vector":
                raise
            logger.warning("knowledge search: query embedding failed; hybrid degraded to fts")
            result.degraded.append(DEGRADED_EMBEDDING_FAILED)
        timings["embed"] = _ms(t)

    chunk_table = _TABLE if table is None else table
    cols = chunk_table.c
    from_ = _base_from(chunk_table, visibility)
    criteria = _filters(
        cols,
        visibility=visibility,
        languages=languages,
        source_types=source_types,
        exclude_article_ids=exclude_article_ids,
        facts_only=facts_only,
    )

    vector_hits: list[tuple[int, float]] = []
    fts_hits: list[tuple[int, float]] = []
    if vector is not None:
        t = time.perf_counter()
        vector_hits = _vector_stage(db, cols, vector, from_, criteria, candidates)
        timings["vector"] = _ms(t)
    if mode in ("fts", "hybrid"):
        t = time.perf_counter()
        fts_hits = _fts_stage(db, cols, query, from_, criteria, candidates)
        timings["fts"] = _ms(t)

    t = time.perf_counter()
    if mode == "vector":
        order = [cid for cid, _ in vector_hits]
        rrf: dict[int, float] = {}
    elif mode == "fts" or vector is None:
        order = [cid for cid, _ in fts_hits]
        rrf = {}
    else:
        fused = rrf_fuse([c for c, _ in vector_hits], [c for c, _ in fts_hits])
        order = [cid for cid, _ in fused]
        rrf = dict(fused)
    loaded = _load_chunks(db, cols, order)
    for rank, (cid, distance) in enumerate(vector_hits, start=1):
        if cid in loaded:
            loaded[cid].vector_rank, loaded[cid].vector_distance = rank, distance
    for rank, (cid, score) in enumerate(fts_hits, start=1):
        if cid in loaded:
            loaded[cid].fts_rank, loaded[cid].fts_score = rank, score
    for cid, score in rrf.items():
        if cid in loaded:
            loaded[cid].rrf_score = score
    chunks = [loaded[cid] for cid in order if cid in loaded]
    timings["fuse"] = _ms(t)

    if rerank and chunks:
        chunks = _rerank(result, query, chunks, RERANK_TOP_N)

    for rank, chunk in enumerate(chunks, start=1):
        chunk.rank = rank
    result.chunks = chunks
    result.results = _group(db, chunks, k)
    timings["total"] = _ms(t_total)

    logger.info(
        "knowledge search: mode=%s visibility=%s rerank=%s k=%d query_chars=%d "
        "chunks=%d groups=%d degraded=%s embed_ms=%s vector_ms=%s fts_ms=%s "
        "fuse_ms=%s rerank_ms=%s total_ms=%s",
        mode,
        visibility,
        result.reranked,
        k,
        len(query),
        len(chunks),
        len(result.results),
        ",".join(result.degraded) or "-",
        timings.get("embed", "-"),
        timings.get("vector", "-"),
        timings.get("fts", "-"),
        timings.get("fuse", "-"),
        timings.get("rerank", "-"),
        timings["total"],
    )
    return result


def search_multi(
    db: Session,
    queries: Sequence[str],
    *,
    rerank_query: str | None = None,
    rerank: bool = True,
    k: int = DEFAULT_K,
    languages: Sequence[str] | None = None,
    source_types: Sequence[str] | None = None,
    visibility: Visibility = "public",
    exclude_article_ids: Iterable[int] = (),
    facts_only: bool = False,
    candidates: int = DEFAULT_CANDIDATES,
    rerank_top_n: int = RERANK_TOP_N,
) -> SearchResult:
    """Multi-query hybrid search (Ask PIC query rewriting, #696).

    Each of ``queries`` gets its own hybrid ranking (:func:`search`, no
    rerank), the rankings are fused by reciprocal rank fusion exactly as
    hybrid fuses its two stages, and the first ``rerank_top_n`` fused chunks
    are reranked against ``rerank_query`` (default ``queries[0]``), so the
    final order and the rerank scores are relative to that one query.

    - All query embeddings come from **one** ``embed_queries`` call made
      before any statement; with no key, or a failed embedding, every query
      runs FTS alone and ``degraded`` says so (never raises, like hybrid).
    - ``queries[0]`` is the primary query: a chunk it found keeps its
      per-stage ranks and scores (``vector_distance`` feeds Ask PIC's
      fallback floor, which is calibrated on the reader's own question). A
      chunk only another query found has those fields cleared, since they
      were measured against different text. ``rrf_score`` is the multi-query
      fused score for every chunk.
    - Filters are those of :func:`search`; ``mode`` is always hybrid and the
      eval-only ``table`` / ``embedding_model`` are not supported.
    """
    queries = [q for q in queries if q and q.strip()]
    if not queries:
        raise ValueError("search_multi needs at least one query")
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    rerank_query = rerank_query or queries[0]
    t_total = time.perf_counter()
    result = SearchResult(query=rerank_query, mode="hybrid", visibility=visibility, reranked=False)
    timings = result.timings_ms

    t = time.perf_counter()
    vectors: list | None = None
    try:
        vectors = embeddings.embed_queries(queries, feature=FEATURE)
    except embeddings.EmbeddingsDisabledError:
        result.degraded.append(DEGRADED_EMBEDDINGS_DISABLED)
    except embeddings.EmbeddingsError:
        logger.warning("knowledge search: query embeddings failed; multi-query degraded to fts")
        result.degraded.append(DEGRADED_EMBEDDING_FAILED)
    timings["embed"] = _ms(t)

    t = time.perf_counter()
    rankings: list[list[ChunkHit]] = []
    for i, query in enumerate(queries):
        one = search(
            db,
            query,
            mode="hybrid" if vectors is not None else "fts",
            rerank=False,
            k=0,
            languages=languages,
            source_types=source_types,
            visibility=visibility,
            exclude_article_ids=exclude_article_ids,
            facts_only=facts_only,
            candidates=candidates,
            query_vector=vectors[i] if vectors is not None else None,
        )
        result.degraded += [d for d in one.degraded if d not in result.degraded]
        rankings.append(one.chunks)
    timings["search"] = _ms(t)

    t = time.perf_counter()
    hits: dict[int, ChunkHit] = {}
    for i, ranking in enumerate(rankings):
        for hit in ranking:
            if hit.chunk_id in hits:
                continue
            if i > 0:
                hit.vector_rank = hit.vector_distance = hit.fts_rank = hit.fts_score = None
            hits[hit.chunk_id] = hit
    fused = rrf_fuse(*([h.chunk_id for h in ranking] for ranking in rankings))
    chunks = []
    for cid, score in fused:
        hits[cid].rrf_score = score
        chunks.append(hits[cid])
    timings["fuse"] = _ms(t)

    if rerank and chunks:
        chunks = _rerank(result, rerank_query, chunks, rerank_top_n)

    for rank, chunk in enumerate(chunks, start=1):
        chunk.rank = rank
    result.chunks = chunks
    result.results = _group(db, chunks, k)
    timings["total"] = _ms(t_total)
    logger.info(
        "knowledge search: multi-query queries=%d visibility=%s rerank=%s k=%d chunks=%d "
        "groups=%d degraded=%s embed_ms=%s search_ms=%s rerank_ms=%s total_ms=%s",
        len(queries),
        visibility,
        result.reranked,
        k,
        len(chunks),
        len(result.results),
        ",".join(result.degraded) or "-",
        timings.get("embed", "-"),
        timings.get("search", "-"),
        timings.get("rerank", "-"),
        timings["total"],
    )
    return result


__all__ = [
    "ArticleHit",
    "ChunkHit",
    "SearchResult",
    "chunk_table_like",
    "public_article_criteria",
    "rrf_fuse",
    "search",
    "search_multi",
]
