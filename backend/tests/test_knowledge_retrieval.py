"""Knowledge retrieval: visibility, facts filter, FTS, RRF, rerank, endpoint (#626)."""

import ast
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.models.category import EXCLUDED_TAXONOMY_ID, Category, Taxonomy
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings
from app.services.embeddings import EmbeddingsDisabledError, EmbeddingsError, RerankResult
from app.services.knowledge import retrieval
from app.services.knowledge.retrieval import rrf_fuse, search
from sqlalchemy import insert, text

DIMS = 1024
APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _vec(*weights: tuple[int, float]) -> list[float]:
    v = [0.0] * DIMS
    for i, w in weights:
        v[i] = w
    return v


CANAL = _vec((0, 1.0))
RAILROAD = _vec((1, 1.0))
OTHER = _vec((2, 1.0))


def fake_embed_query(text, *, feature="unlabeled"):
    """Deterministic 'multilingual' embedding: a concept keyword picks the axis."""
    assert feature == retrieval.FEATURE
    t = text.lower()
    if "canal" in t:
        return CANAL
    if "ferrocarril" in t or "railroad" in t:
        return RAILROAD
    return OTHER


@pytest.fixture
def voyage(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
    monkeypatch.setattr(embeddings, "embed_query", fake_embed_query)
    calls = []

    def fake_rerank(query, documents, *, top_k=None, feature="unlabeled"):
        calls.append(list(documents))
        # Reverse the input order, to prove the rerank order wins.
        n = len(documents)
        return [RerankResult(n - 1 - i, 1.0 - i / 10) for i in range(n)]

    monkeypatch.setattr(embeddings, "rerank", fake_rerank)
    return calls


_counter = {"n": 0}


def _chunk(
    db,
    *,
    content,
    article_id=None,
    source_type="ARTICLE",
    source_id=None,
    language="en",
    embedding=None,
    heading_path=None,
    meta=None,
    chunk_index=None,
):
    _counter["n"] += 1
    row = db.execute(
        insert(KnowledgeChunk.__table__)
        .values(
            source_type=source_type,
            source_id=source_id if source_id is not None else (article_id or _counter["n"]),
            article_id=article_id,
            language=language,
            chunk_index=chunk_index if chunk_index is not None else _counter["n"],
            heading_path=heading_path,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            token_count=len(content) // 4 + 1,
            embedding_model="voyage-4" if embedding is not None else None,
            embedding=embedding,
            meta=meta,
        )
        .returning(KnowledgeChunk.__table__.c.id)
    ).scalar_one()
    db.commit()
    return row


def _bio_category(db):
    tax = Taxonomy(id=EXCLUDED_TAXONOMY_ID, name="Notable Figures", description="Bios")
    db.add(tax)
    db.commit()
    cat = Category(name="Biographies", taxonomy_id=tax.id, description="Bios")
    db.add(cat)
    db.commit()
    return cat


def _article_ids(result):
    return [hit.article_id for hit in result.results]


@pytest.fixture
def corpus(test_db, article_factory):
    """A published article, a scheduled one, a bio (taxonomy 5) and a draft."""
    published = article_factory(title="The Canal")
    scheduled = article_factory(
        title="Scheduled Canal", published_at=datetime.now(UTC) + timedelta(days=3)
    )
    bio = article_factory(title="Canal Hero Bio", category=_bio_category(test_db))
    draft = article_factory(title="Draft Canal", status="PENDING", published_at=None)
    for a in (published, scheduled, bio, draft):
        _chunk(test_db, article_id=a.id, content=f"{a.title}: the canal opened", embedding=CANAL)
    return {"published": published, "scheduled": scheduled, "bio": bio, "draft": draft}


# --- Visibility -------------------------------------------------------------------


def test_public_excludes_scheduled_bio_and_unapproved(test_db, voyage, corpus):
    for mode in ("vector", "fts", "hybrid"):
        result = search(test_db, "canal", mode=mode, visibility="public")
        assert _article_ids(result) == [corpus["published"].id], mode


def test_internal_includes_scheduled_but_not_unapproved(test_db, voyage, corpus):
    result = search(test_db, "canal", mode="vector", visibility="internal")
    ids = set(_article_ids(result))
    assert corpus["published"].id in ids
    assert corpus["scheduled"].id in ids
    assert corpus["draft"].id not in ids


def test_kb_chunks_are_internal_only(test_db, voyage, article_factory):
    article = article_factory(title="The Canal")
    _chunk(test_db, article_id=article.id, content="the canal opened in 1914", embedding=CANAL)
    kb = _chunk(
        test_db,
        source_type="VERIFIED_CLAIM",
        source_id=7,
        content="The canal opened on 15 August 1914.",
        embedding=CANAL,
        meta={"verdict": "VERIFIED"},
    )
    public = search(test_db, "canal", mode="hybrid", visibility="public")
    assert all(c.source_type != "VERIFIED_CLAIM" for c in public.chunks)
    assert _article_ids(public) == [article.id]

    internal = search(test_db, "canal", mode="hybrid", visibility="internal")
    kb_hits = [h for h in internal.results if h.article_id is None]
    assert [h.best.chunk_id for h in kb_hits] == [kb]
    assert kb_hits[0].title is None


def test_facts_only_keeps_only_verified_kb_rows(test_db, voyage):
    rows = {}
    for verdict in ("VERIFIED", "INACCURATE", "DISPUTED"):
        rows[verdict] = _chunk(
            test_db,
            source_type="VERIFIED_CLAIM",
            source_id=len(rows) + 1,
            content=f"The canal claim ({verdict}).",
            embedding=CANAL,
            meta={"verdict": verdict},
        )
    everything = search(test_db, "canal", mode="vector", visibility="internal")
    assert {c.chunk_id for c in everything.chunks} == set(rows.values())

    facts = search(test_db, "canal", mode="vector", visibility="internal", facts_only=True)
    assert [c.chunk_id for c in facts.chunks] == [rows["VERIFIED"]]


def test_exclude_article_ids_keeps_kb_chunks(test_db, voyage, article_factory):
    a, b = article_factory(title="A"), article_factory(title="B")
    _chunk(test_db, article_id=a.id, content="canal a", embedding=CANAL)
    _chunk(test_db, article_id=b.id, content="canal b", embedding=CANAL)
    _chunk(
        test_db,
        source_type="VERIFIED_CLAIM",
        source_id=1,
        content="canal kb",
        embedding=CANAL,
        meta={"verdict": "VERIFIED"},
    )
    result = search(
        test_db, "canal", mode="vector", visibility="internal", exclude_article_ids=[a.id]
    )
    assert set(_article_ids(result)) == {b.id, None}


def test_language_and_source_type_filters(test_db, voyage, article_factory):
    a = article_factory(title="A")
    en = _chunk(test_db, article_id=a.id, content="canal en", embedding=CANAL)
    es = _chunk(test_db, article_id=a.id, content="canal es", language="es", embedding=CANAL)
    claim = _chunk(
        test_db,
        article_id=a.id,
        source_type="ARTICLE_CLAIM",
        source_id=99,
        content="canal claim",
        embedding=CANAL,
        meta={"verdict": "VERIFIED"},
    )
    only_es = search(test_db, "canal", mode="vector", languages=["es"])
    assert [c.chunk_id for c in only_es.chunks] == [es]
    only_claims = search(test_db, "canal", mode="vector", source_types=["ARTICLE_CLAIM"])
    assert [c.chunk_id for c in only_claims.chunks] == [claim]
    both = search(test_db, "canal", mode="vector", source_types=["ARTICLE"])
    assert {c.chunk_id for c in both.chunks} == {en, es}


# --- Cross-lingual, FTS, grouping ---------------------------------------------------


def test_spanish_query_returns_english_only_matches(test_db, voyage, article_factory):
    canal = article_factory(title="The Canal")
    rail = article_factory(title="The Railroad")
    _chunk(test_db, article_id=canal.id, content="Locks were built at Gatun.", embedding=CANAL)
    _chunk(test_db, article_id=rail.id, content="The line crossed the isthmus.", embedding=RAILROAD)

    result = search(test_db, "¿Quién construyó el ferrocarril?", mode="vector")
    assert _article_ids(result)[0] == rail.id
    assert result.results[0].best.language == "en"

    hybrid = search(test_db, "¿Quién construyó el ferrocarril?", mode="hybrid")
    assert _article_ids(hybrid)[0] == rail.id
    assert hybrid.results[0].best.fts_rank is None  # no lexical overlap: vector found it


def test_fts_matches_under_both_configs_and_unaccents(test_db, article_factory):
    es_article = article_factory(title="Colón")
    en_article = article_factory(title="Railroad")
    es = _chunk(
        test_db,
        article_id=es_article.id,
        language="es",
        content="Los canales y la ciudad de Colón crecieron con el ferrocarril.",
    )
    en = _chunk(
        test_db,
        article_id=en_article.id,
        content="Workers were building the railroads across the isthmus.",
    )
    # Spanish stemming (canal ~ canales) and unaccent (colon ~ Colón).
    spanish = search(test_db, "canal colon", mode="fts")
    assert [c.chunk_id for c in spanish.chunks] == [es]
    # English stemming (railroad ~ railroads, worker ~ workers).
    english = search(test_db, "worker railroad", mode="fts")
    assert [c.chunk_id for c in english.chunks] == [en]
    assert english.chunks[0].fts_rank == 1 and english.chunks[0].fts_score > 0


def test_results_group_chunks_by_article(test_db, voyage, article_factory):
    a = article_factory(title="A")
    b = article_factory(title="B")
    a1 = _chunk(test_db, article_id=a.id, content="canal one", embedding=CANAL)
    _chunk(test_db, article_id=b.id, content="canal b", embedding=_vec((0, 0.9), (2, 0.4)))
    a2 = _chunk(test_db, article_id=a.id, content="canal two", embedding=_vec((0, 0.8), (2, 0.6)))
    result = search(test_db, "canal", mode="vector", k=10)
    assert _article_ids(result) == [a.id, b.id]
    top = result.results[0]
    assert top.title == "A"
    assert top.best.chunk_id == a1
    assert [c.chunk_id for c in top.chunks] == [a1, a2]
    assert [c.rank for c in result.chunks] == [1, 2, 3]
    assert search(test_db, "canal", mode="vector", k=1).results[0].article_id == a.id
    assert len(search(test_db, "canal", mode="vector", k=1).results) == 1


# --- Fusion and rerank --------------------------------------------------------------


def test_rrf_fuse_rewards_agreement():
    # 1/(60+2) * 2 beats 1/(60+1): consistently second beats one first place.
    fused = rrf_fuse([1, 2, 3], [4, 2, 5])
    assert fused[0][0] == 2
    assert fused[0][1] == pytest.approx(2 / 62)
    assert [i for i, _ in fused[1:3]] == [1, 4]  # tie at 1/61, first-seen order
    assert dict(fused)[3] == pytest.approx(1 / 63)
    assert rrf_fuse([7, 8], k=0) == [(7, 1.0), (8, 0.5)]


def test_hybrid_orders_by_rrf_and_records_stage_ranks(test_db, voyage, article_factory):
    a = article_factory(title="A")
    b = article_factory(title="B")
    c = article_factory(title="C")
    # Vector order: a, b, c. FTS (matches "canal locks"): only b and c, b first.
    _chunk(test_db, article_id=a.id, content="Nothing lexical here.", embedding=CANAL)
    _chunk(
        test_db,
        article_id=b.id,
        content="The canal locks at Gatun; canal locks again.",
        embedding=_vec((0, 0.9), (2, 0.43)),
    )
    _chunk(
        test_db,
        article_id=c.id,
        content="Canal locks.",
        embedding=_vec((0, 0.5), (2, 0.86)),
    )
    result = search(test_db, "canal locks", mode="hybrid")
    best = {h.article_id: h.best for h in result.results}
    assert _article_ids(result)[0] == b.id  # 2nd + 1st beats 1st + absent
    assert best[a.id].vector_rank == 1 and best[a.id].fts_rank is None
    assert best[b.id].vector_rank == 2 and best[b.id].fts_rank in (1, 2)
    assert best[b.id].rrf_score == pytest.approx(1 / 62 + 1 / (60 + best[b.id].fts_rank))
    assert best[a.id].rrf_score == pytest.approx(1 / 61)
    assert result.reranked is False and result.degraded == []
    assert {"embed", "vector", "fts", "fuse", "total"} <= set(result.timings_ms)


def test_rerank_only_when_asked_and_its_order_wins(test_db, voyage, article_factory):
    a = article_factory(title="A")
    b = article_factory(title="B")
    _chunk(test_db, article_id=a.id, content="canal a", embedding=CANAL)
    _chunk(test_db, article_id=b.id, content="canal b", embedding=_vec((0, 0.7), (2, 0.7)))

    plain = search(test_db, "canal", mode="vector")
    assert voyage == [] and _article_ids(plain) == [a.id, b.id]

    reranked = search(test_db, "canal", mode="vector", rerank=True)
    # Article chunks are reranked as their embedding text: title (> heading) + chunk (#697).
    assert voyage == [["A\n\ncanal a", "B\n\ncanal b"]]
    assert reranked.reranked is True
    assert _article_ids(reranked) == [b.id, a.id]
    assert reranked.results[0].best.rerank_rank == 1
    assert reranked.results[0].best.vector_rank == 2
    assert "rerank" in reranked.timings_ms


def test_failed_rerank_keeps_fused_order(test_db, voyage, article_factory, monkeypatch):
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="canal a", embedding=CANAL)

    def boom(*args, **kwargs):
        raise EmbeddingsError("down")

    monkeypatch.setattr(embeddings, "rerank", boom)
    result = search(test_db, "canal", mode="hybrid", rerank=True)
    assert _article_ids(result) == [a.id]
    assert result.reranked is False
    assert result.degraded == [retrieval.DEGRADED_RERANK_UNAVAILABLE]


# --- Multi-query search (#696) ------------------------------------------------------


def _multi_corpus(test_db, article_factory):
    canal = article_factory(title="Canal")
    railroad = article_factory(title="Railroad")
    canal_chunk = _chunk(test_db, article_id=canal.id, content="the canal opened", embedding=CANAL)
    # No embedding and no "canal": only the second query's FTS can find it.
    rail_chunk = _chunk(test_db, article_id=railroad.id, content="trains stopped at Gorgona")
    return canal, railroad, canal_chunk, rail_chunk


def test_search_multi_fuses_every_query_and_reranks_against_one(
    test_db, voyage, article_factory, monkeypatch
):
    canal, railroad, canal_chunk, rail_chunk = _multi_corpus(test_db, article_factory)
    embed_calls, rerank_queries = [], []

    def fake_embed_queries(texts, *, feature="unlabeled"):
        embed_calls.append(list(texts))
        return [fake_embed_query(t, feature=feature) for t in texts]

    def no_single_embed(*a, **kw):
        raise AssertionError("one batched embedding call, not one per query")

    def fake_rerank(query, documents, *, top_k=None, feature="unlabeled"):
        rerank_queries.append(query)
        return [RerankResult(i, 0.9 - i / 10) for i in range(len(documents))]

    monkeypatch.setattr(embeddings, "embed_queries", fake_embed_queries)
    monkeypatch.setattr(embeddings, "embed_query", no_single_embed)
    monkeypatch.setattr(embeddings, "rerank", fake_rerank)

    result = retrieval.search_multi(
        test_db, ["canal", "Gorgona"], rerank_query="when did the canal open", rerank=True
    )
    assert embed_calls == [["canal", "Gorgona"]]
    assert rerank_queries == ["when did the canal open"]
    assert result.query == "when did the canal open" and result.reranked is True
    hits = {c.chunk_id: c for c in result.chunks}
    assert set(hits) == {canal_chunk, rail_chunk}
    assert set(_article_ids(result)) == {canal.id, railroad.id}
    # The primary query's chunk keeps its stage scores; the rewrite-only one has none.
    assert hits[canal_chunk].vector_distance is not None
    assert hits[rail_chunk].vector_distance is None and hits[rail_chunk].fts_rank is None
    assert all(c.rrf_score is not None and c.rerank_score is not None for c in hits.values())
    assert {"embed", "search", "fuse", "rerank", "total"} <= set(result.timings_ms)


def test_search_multi_rrf_order_without_rerank(test_db, voyage, article_factory, monkeypatch):
    canal, railroad, canal_chunk, rail_chunk = _multi_corpus(test_db, article_factory)
    monkeypatch.setattr(
        embeddings,
        "embed_queries",
        lambda texts, *, feature="unlabeled": [fake_embed_query(t, feature=feature) for t in texts],
    )
    # The canal chunk is found by both queries (vector), the railroad one by one.
    result = retrieval.search_multi(test_db, ["canal", "Gorgona"], rerank=False)
    assert [c.chunk_id for c in result.chunks] == [canal_chunk, rail_chunk]
    assert voyage == []  # no rerank call


def test_search_multi_degrades_to_fts_without_embeddings(
    test_db, voyage, article_factory, monkeypatch
):
    canal, railroad, canal_chunk, rail_chunk = _multi_corpus(test_db, article_factory)

    def disabled(*a, **kw):
        raise EmbeddingsDisabledError("no key")

    monkeypatch.setattr(embeddings, "embed_queries", disabled)
    monkeypatch.setattr(embeddings, "embed_query", disabled)
    result = retrieval.search_multi(test_db, ["canal", "Gorgona"], rerank=False)
    assert retrieval.DEGRADED_EMBEDDINGS_DISABLED in result.degraded
    assert {c.chunk_id for c in result.chunks} == {canal_chunk, rail_chunk}


def test_search_multi_needs_a_query(test_db):
    with pytest.raises(ValueError):
        retrieval.search_multi(test_db, ["", "  "])


# --- Degradation ------------------------------------------------------------------


def test_hybrid_degrades_to_fts_without_a_key(test_db, article_factory):
    # conftest forces VOYAGE_API_KEY="": the real seam raises EmbeddingsDisabledError.
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="The canal opened.", embedding=CANAL)
    result = search(test_db, "canal", mode="hybrid")
    assert _article_ids(result) == [a.id]
    assert result.degraded == [retrieval.DEGRADED_EMBEDDINGS_DISABLED]
    assert result.chunks[0].vector_rank is None and result.chunks[0].fts_rank == 1
    with pytest.raises(EmbeddingsDisabledError):
        search(test_db, "canal", mode="vector")


def test_hybrid_degrades_when_the_query_embedding_fails(test_db, article_factory, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")

    def boom(*args, **kwargs):
        raise EmbeddingsError("timeout")

    monkeypatch.setattr(embeddings, "embed_query", boom)
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="The canal opened.", embedding=CANAL)
    result = search(test_db, "canal", mode="hybrid")
    assert result.degraded == [retrieval.DEGRADED_EMBEDDING_FAILED]
    assert _article_ids(result) == [a.id]
    with pytest.raises(EmbeddingsError):
        search(test_db, "canal", mode="vector")


def test_unembedded_chunks_are_skipped_by_vector_stage(test_db, voyage, article_factory):
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="canal pending embedding")
    assert search(test_db, "canal", mode="vector").results == []
    assert _article_ids(search(test_db, "canal", mode="fts")) == [a.id]


# --- Iterative HNSW scan (#722) -----------------------------------------------------


def _guc(db, name):
    return db.execute(text(f"SHOW {name}")).scalar()


def test_vector_stage_sets_iterative_scan_for_the_transaction_only(
    test_db, voyage, article_factory, monkeypatch
):
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", None)
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="The canal opened.", embedding=CANAL)
    test_db.commit()

    assert _article_ids(search(test_db, "canal", mode="vector")) == [a.id]
    assert retrieval._iterative_scan_supported is True  # test DB runs pgvector >= 0.8
    assert _guc(test_db, "hnsw.iterative_scan") == retrieval.ITERATIVE_SCAN
    assert _guc(test_db, "hnsw.ef_search") == str(retrieval.DEFAULT_CANDIDATES)
    assert _guc(test_db, "hnsw.max_scan_tuples") == str(retrieval.MAX_SCAN_TUPLES)

    test_db.commit()  # SET LOCAL: gone with the transaction
    assert _guc(test_db, "hnsw.iterative_scan") == "off"
    assert _guc(test_db, "hnsw.ef_search") == "40"
    assert _guc(test_db, "hnsw.max_scan_tuples") == "20000"  # pgvector's default


def test_old_pgvector_skips_iterative_scan_and_warns_once(
    test_db, voyage, article_factory, monkeypatch, caplog
):
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", None)
    checks = []

    def old_version(db):
        checks.append(1)
        return "0.7.4"

    monkeypatch.setattr(retrieval, "_pgvector_version", old_version)
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="The canal opened.", embedding=CANAL)
    test_db.commit()

    with caplog.at_level(logging.WARNING, logger="app.services.knowledge.retrieval"):
        assert _article_ids(search(test_db, "canal", mode="vector")) == [a.id]
        assert _article_ids(search(test_db, "canal", mode="hybrid")) == [a.id]
    assert _guc(test_db, "hnsw.iterative_scan") == "off"
    assert _guc(test_db, "hnsw.ef_search") == str(retrieval.DEFAULT_CANDIDATES)
    assert _guc(test_db, "hnsw.max_scan_tuples") == "20000"  # only set with the scan
    assert len(checks) == 1  # cached per process
    warnings = [r for r in caplog.records if "iterative_scan" in r.getMessage()]
    assert len(warnings) == 1 and "0.7.4" in warnings[0].getMessage()


def test_missing_extension_counts_as_unsupported(monkeypatch):
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", None)
    monkeypatch.setattr(retrieval, "_pgvector_version", lambda db: None)
    assert retrieval.iterative_scan_supported(object()) is False


@pytest.mark.parametrize(
    ("version", "supported"),
    [("0.8.0", True), ("0.8.6", True), ("0.10.1", True), ("1.0", True), ("0.7.4", False)],
)
def test_iterative_scan_version_gate(version, supported):
    assert (retrieval._parse_version(version) >= retrieval.ITERATIVE_SCAN_MIN_VERSION) is supported


def test_vector_stage_restores_distance_order(monkeypatch):
    """relaxed_order can return rows slightly out of order; callers get them sorted."""
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", True)

    class Row:
        def __init__(self, id_, distance):
            self.id, self.distance = id_, distance

    class FakeSession:
        def __init__(self):
            self.statements = []

        def execute(self, stmt):
            self.statements.append(stmt)
            if len(self.statements) == 1:
                return None  # the set_config statement
            return [Row(3, 0.30), Row(1, 0.10), Row(4, 0.20), Row(2, 0.20)]

    db = FakeSession()
    cols = KnowledgeChunk.__table__.c
    hits = retrieval._vector_stage(db, cols, CANAL, KnowledgeChunk.__table__, [], 4)
    assert hits == [(1, 0.10), (2, 0.20), (4, 0.20), (3, 0.30)]
    settings_sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "hnsw.iterative_scan" in settings_sql and "relaxed_order" in settings_sql
    assert "hnsw.ef_search" in settings_sql
    assert "hnsw.max_scan_tuples" in settings_sql and str(retrieval.MAX_SCAN_TUPLES) in settings_sql
    assert len(db.statements) == 2  # a full pool runs no underfill count


# --- Scan limit and the underfill warning (#729) -------------------------------------


def test_max_scan_tuples_has_headroom_over_the_default():
    # pgvector's default is 20,000; the table held 14,101 rows on 2026-10-13.
    assert retrieval.MAX_SCAN_TUPLES >= 3 * 14_101 > 20_000


def _underfill_warnings(caplog):
    return [r for r in caplog.records if "underfilled" in r.getMessage()]


def test_short_pool_without_more_matches_does_not_warn(
    test_db, voyage, article_factory, monkeypatch, caplog
):
    """Fewer rows than the pool is normal when the filter has no more matches."""
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", None)
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="The canal opened.", embedding=CANAL)
    _chunk(test_db, article_id=a.id, content="The railroad ran.", embedding=RAILROAD)
    with caplog.at_level(logging.WARNING, logger="app.services.knowledge.retrieval"):
        result = search(test_db, "canal", mode="vector")
    assert _article_ids(result) == [a.id]
    assert len(result.chunks) == 2  # < DEFAULT_CANDIDATES, and that is all there is
    assert _underfill_warnings(caplog) == []


def test_scan_stopped_by_max_scan_tuples_warns(test_db, voyage, monkeypatch, caplog):
    """The HNSW scan hits its tuple cap while matching rows remain: one WARNING."""
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", None)
    monkeypatch.setattr(retrieval, "MAX_SCAN_TUPLES", 1)
    # More filtered-out rows (English) nearest the query than ef_search, so the
    # first pass returns none of the Spanish ones and the iterative pass stops
    # at once. KB claims, so ``internal`` needs no article join.
    for i in range(retrieval.DEFAULT_CANDIDATES + 20):
        _chunk(
            test_db,
            source_type="VERIFIED_CLAIM",
            content=f"claim {i}",
            embedding=_vec((0, 1.0), (3, 0.001 * i)),
        )
    for i in range(3):
        _chunk(
            test_db,
            source_type="VERIFIED_CLAIM",
            language="es",
            content=f"canal {i}",
            embedding=_vec((0, 1.0), (4, 1.0 + i)),
        )
    # A tiny table: make the planner take the HNSW index, the only ordered path.
    test_db.execute(text("SET LOCAL enable_seqscan = off"))
    test_db.execute(text("SET LOCAL enable_sort = off"))
    with caplog.at_level(logging.WARNING, logger="app.services.knowledge.retrieval"):
        result = search(test_db, "canal", mode="vector", visibility="internal", languages=["es"])
    warnings = _underfill_warnings(caplog)
    assert len(result.chunks) < 3
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert f"returned={len(result.chunks)} of limit={retrieval.DEFAULT_CANDIDATES}" in message
    assert "matching rows>=3" in message and "canal" not in message  # no query text


def test_underfill_check_counts_matches_up_to_the_limit(monkeypatch, caplog):
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", True)

    class Scalar:
        def __init__(self, value):
            self.value = value

        def scalar(self):
            return self.value

    class FakeSession:
        def __init__(self, rows, matching):
            self.rows, self.matching, self.statements = rows, matching, []

        def execute(self, stmt):
            self.statements.append(stmt)
            if len(self.statements) == 1:
                return None  # set_config
            if len(self.statements) == 2:
                return [type("Row", (), {"id": i, "distance": 0.1 * i})() for i in self.rows]
            return Scalar(self.matching)

    cols = KnowledgeChunk.__table__.c
    with caplog.at_level(logging.WARNING, logger="app.services.knowledge.retrieval"):
        retrieval._vector_stage(
            FakeSession([1, 2], 2), cols, CANAL, KnowledgeChunk.__table__, [], 5
        )
        assert _underfill_warnings(caplog) == []
        db = FakeSession([1, 2], 5)
        retrieval._vector_stage(db, cols, CANAL, KnowledgeChunk.__table__, [], 5)
    assert len(_underfill_warnings(caplog)) == 1
    count_sql = str(db.statements[2].compile(compile_kwargs={"literal_binds": True}))
    assert "count(*)" in count_sql and "LIMIT 5" in count_sql and "<=>" not in count_sql


def test_unsupported_scan_never_runs_the_underfill_check(monkeypatch):
    monkeypatch.setattr(retrieval, "_iterative_scan_supported", False)
    statements = []

    class FakeSession:
        def execute(self, stmt):
            statements.append(stmt)
            return None if len(statements) == 1 else []

    cols = KnowledgeChunk.__table__.c
    assert (
        retrieval._vector_stage(FakeSession(), cols, CANAL, KnowledgeChunk.__table__, [], 5) == []
    )
    assert len(statements) == 2
    settings_sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "max_scan_tuples" not in settings_sql and "iterative_scan" not in settings_sql


def test_bad_mode_and_visibility_raise(test_db):
    with pytest.raises(ValueError):
        search(test_db, "x", mode="semantic")
    with pytest.raises(ValueError):
        search(test_db, "x", visibility="everyone")


def test_timings_logged_under_the_app_namespace(test_db, voyage, article_factory, caplog):
    a = article_factory(title="A")
    _chunk(test_db, article_id=a.id, content="canal", embedding=CANAL)
    with caplog.at_level(logging.INFO, logger="app.services.knowledge.retrieval"):
        search(test_db, "secret canal question", mode="hybrid")
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    line = next(m for m in lines if m.startswith("knowledge search:"))
    for stage in ("embed_ms=", "vector_ms=", "fts_ms=", "fuse_ms=", "rerank_ms=", "total_ms="):
        assert stage in line
    assert "secret" not in line  # the query text is never logged


# --- Admin endpoint ---------------------------------------------------------------

URL = "/api/v1/admin/dashboard/knowledge/search"


def test_endpoint_returns_raw_ranked_results(test_client, admin_token, voyage, corpus):
    resp = test_client.get(URL, params={"q": "canal", "mode": "hybrid"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "hybrid" and body["visibility"] == "public"
    assert [r["article_id"] for r in body["results"]] == [corpus["published"].id]
    chunk = body["results"][0]["best"]
    assert chunk["vector_rank"] == 1 and chunk["fts_rank"] == 1 and chunk["rrf_score"] > 0
    assert "total" in body["timings_ms"]

    internal = test_client.get(URL, params={"q": "canal", "visibility": "internal"}).json()
    assert corpus["scheduled"].id in [r["article_id"] for r in internal["results"]]


def test_endpoint_requires_admin(test_client):
    test_client.headers.pop("Authorization", None)
    assert test_client.get(URL, params={"q": "canal"}).status_code == 401


def test_endpoint_vector_mode_without_key_is_503(test_client, admin_token):
    resp = test_client.get(URL, params={"q": "canal", "mode": "vector"})
    assert resp.status_code == 503
    degraded = test_client.get(URL, params={"q": "canal", "mode": "hybrid"})
    assert degraded.status_code == 200
    assert degraded.json()["degraded"] == [retrieval.DEGRADED_EMBEDDINGS_DISABLED]


def test_endpoint_validates_params(test_client, admin_token):
    assert test_client.get(URL, params={"q": "canal", "mode": "semantic"}).status_code == 422
    assert test_client.get(URL, params={"q": "canal", "language": "fr"}).status_code == 422
    assert test_client.get(URL).status_code == 422


# --- Guards -----------------------------------------------------------------------


def _names_knowledge_chunks(path: Path) -> list[int]:
    """Line numbers where code (not a comment or docstring) names the table/model."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "KnowledgeChunk":
            hits.append(node.lineno)
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in (
            "KnowledgeChunk",
            "knowledge_chunk",
        ):
            hits.append(getattr(node, "lineno", 0))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "knowledge_chunks" in node.value
            and id(node) not in docstrings
        ):
            hits.append(node.lineno)
    return hits


def test_only_the_knowledge_package_and_models_touch_knowledge_chunks():
    """A new reader must go through ``retrieval.search`` (#626 acceptance)."""
    allowed = (APP_DIR / "services" / "knowledge", APP_DIR / "models")
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if any(path.is_relative_to(root) for root in allowed):
            continue
        offenders += [f"{path.relative_to(APP_DIR)}:{ln}" for ln in _names_knowledge_chunks(path)]
    assert offenders == []


def test_guard_sees_a_direct_reader(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""Mentions knowledge_chunks in a docstring only."""\n'
        "from app.models.knowledge_chunk import KnowledgeChunk\n"
        "# knowledge_chunks in a comment\n"
        "q = text('SELECT * FROM knowledge_chunks')\n"
    )
    assert sorted(_names_knowledge_chunks(probe)) == [2, 4]


def test_excluded_taxonomy_id_is_defined_once():
    definitions = []
    for path in APP_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "EXCLUDED_TAXONOMY_ID" for t in node.targets
            ):
                definitions.append(str(path.relative_to(APP_DIR)))
    assert definitions == ["models/category.py"]
    assert EXCLUDED_TAXONOMY_ID == 5
