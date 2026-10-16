"""Article indexer + reindex sweep (#624, epic #617)."""

import importlib
import inspect
import pkgutil
import re

import pytest
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings
from app.services.knowledge import claims, indexer
from app.services.knowledge.chunker import chunk_article
from fastapi.routing import APIRoute
from scripts import reindex_knowledge
from sqlalchemy import literal_column, select
from sqlalchemy.orm import sessionmaker

DIMS = 1024


def _para(word: str, n: int = 120) -> str:
    return " ".join(f"{word}{i}." for i in range(n))


def _body(*sections: tuple[str, str]) -> str:
    return "\n\n".join(f"## {heading}\n\n{text}" for heading, text in sections)


SECTIONS = [
    ("Origins", _para("origin")),
    ("Construction", _para("build")),
    ("Legacy", _para("legacy")),
]


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.error: Exception | None = None

    def __call__(self, texts, *, feature="unlabeled"):
        assert feature == indexer.FEATURE
        if self.error:
            raise self.error
        self.calls.append(list(texts))
        return [[0.01 * (i % 7 + 1)] * DIMS for i in range(len(texts))]

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]


@pytest.fixture
def indexer_env(test_engine, monkeypatch):
    """Point the indexer (and the sweep script) at the test DB with a fake Voyage."""
    from app.core.config import get_settings

    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(indexer, "SessionLocal", factory)
    monkeypatch.setattr(claims, "SessionLocal", factory)  # the sweep also indexes claims
    monkeypatch.setattr(reindex_knowledge, "SessionLocal", factory)
    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
    # No situating sentences here (#697 has its own tests in test_knowledge_context.py).
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "embed_documents", fake)
    return fake


@pytest.fixture
def article(test_db, article_factory):
    a = article_factory(title="The Canal", title_es="El Canal", content=_body(*SECTIONS))
    a.content_es = _body(("Orígenes", _para("origen")), ("Legado", _para("legado")))
    test_db.commit()
    return a


def _rows(test_db, article_id):
    c = KnowledgeChunk.__table__.c
    test_db.expire_all()
    return test_db.execute(
        select(c.language, c.chunk_index, c.content_hash, c.embedding.is_not(None))
        .where(c.source_type == "ARTICLE", c.source_id == article_id)
        .order_by(c.language, c.chunk_index)
    ).all()


def _expected(a):
    return chunk_article(a.title, a.content, "en") + chunk_article(a.title_es, a.content_es, "es")


def test_first_index_embeds_every_chunk(indexer_env, test_db, article):
    result = indexer.index_article(article.id)

    expected = _expected(article)
    assert result.status == "indexed"
    assert result.embedded == result.chunks == len(expected)
    assert sorted(indexer_env.texts) == sorted(c.embedding_text for c in expected)
    rows = _rows(test_db, article.id)
    assert len(rows) == len(expected)
    assert all(embedded for *_, embedded in rows)
    assert {r.language for r in rows} == {"en", "es"}
    model = test_db.execute(select(KnowledgeChunk.embedding_model).limit(1)).scalar_one()
    assert model == embeddings.document_model()


def test_rerun_on_unchanged_article_makes_no_embedding_call(indexer_env, test_db, article):
    indexer.index_article(article.id)
    before = _rows(test_db, article.id)
    indexer_env.calls.clear()

    result = indexer.index_article(article.id)

    assert result.status == "unchanged"
    assert indexer_env.calls == []
    assert _rows(test_db, article.id) == before


def test_editing_one_section_reembeds_only_that_section(indexer_env, test_db, article):
    indexer.index_article(article.id)
    indexer_env.calls.clear()

    edited = [SECTIONS[0], ("Construction", _para("dig")), SECTIONS[2]]
    article.content = _body(*edited)
    test_db.commit()
    result = indexer.index_article(article.id)

    construction = [
        c
        for c in chunk_article(article.title, article.content, "en")
        if c.heading_path == "Construction"
    ]
    assert result.status == "indexed"
    assert len(indexer_env.calls) == 1
    assert sorted(indexer_env.texts) == sorted(c.embedding_text for c in construction)
    assert result.deleted == len(construction)  # the old Construction chunks
    rows = _rows(test_db, article.id)
    expected = sorted(_expected(article), key=lambda c: (c.meta["language"], c.chunk_index))
    assert [(r.language, r.chunk_index, r.content_hash) for r in rows] == [
        (c.meta["language"], c.chunk_index, c.content_hash) for c in expected
    ]
    assert all(embedded for *_, embedded in rows)


def _versions(test_db, article_id) -> dict[int, str]:
    """Chunk row id -> its xmin (a new one on every UPDATE / INSERT of the row)."""
    c = KnowledgeChunk.__table__.c
    test_db.expire_all()
    test_db.commit()
    return dict(
        test_db.execute(
            select(c.id, literal_column("xmin::text")).where(
                c.source_type == "ARTICLE", c.source_id == article_id
            )
        ).all()
    )


def test_editing_one_section_leaves_the_other_rows_unwritten(indexer_env, test_db, article):
    """#728: kept rows whose values did not change are never UPDATEd."""
    indexer.index_article(article.id)
    before = _versions(test_db, article.id)

    article.content = _body(SECTIONS[0], ("Construction", _para("dig")), SECTIONS[2])
    test_db.commit()
    result = indexer.index_article(article.id)

    after = _versions(test_db, article.id)
    kept = set(before) & set(after)
    assert result.kept == len(kept) > 0
    assert result.updated == 0
    assert all(after[row_id] == before[row_id] for row_id in kept)


def test_removing_a_section_deletes_and_renumbers_without_embedding(indexer_env, test_db, article):
    indexer.index_article(article.id)
    indexer_env.calls.clear()

    article.content = _body(SECTIONS[1], SECTIONS[2])  # first section removed
    test_db.commit()
    result = indexer.index_article(article.id)

    assert indexer_env.calls == []
    assert result.status == "indexed" and result.deleted > 0
    en = [r for r in _rows(test_db, article.id) if r.language == "en"]
    wanted = chunk_article(article.title, article.content, "en")
    assert [(r.chunk_index, r.content_hash) for r in en] == [
        (c.chunk_index, c.content_hash) for c in wanted
    ]


def test_clearing_the_spanish_body_drops_its_chunks(indexer_env, test_db, article):
    indexer.index_article(article.id)
    article.content_es = None
    test_db.commit()

    indexer.index_article(article.id)

    assert {r.language for r in _rows(test_db, article.id)} == {"en"}


def test_empty_voyage_key_skips_quietly(test_db, article, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "voyage_api_key", "")
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "embed_documents", fake)

    def no_session():
        raise AssertionError("a disabled run must not touch the database")

    monkeypatch.setattr(indexer, "SessionLocal", no_session)
    with pytest.MonkeyPatch.context() as mp:
        captured = []
        mp.setattr(indexer.sentry_sdk, "capture_exception", captured.append)
        result = indexer.index_article(article.id)

    assert result.status == "disabled"
    assert fake.calls == [] and captured == []


def test_embedding_failure_is_captured_and_writes_nothing(
    indexer_env, test_db, article, monkeypatch
):
    captured = []
    monkeypatch.setattr(indexer.sentry_sdk, "capture_exception", captured.append)
    indexer_env.error = embeddings.EmbeddingsError("Voyage returned HTTP 500")

    result = indexer.index_article(article.id)  # must not raise

    assert result.status == "failed"
    assert len(captured) == 1
    assert _rows(test_db, article.id) == []


def test_missing_article_is_a_noop(indexer_env):
    assert indexer.index_article(999_999).status == "missing"
    assert indexer_env.calls == []


def test_dry_run_reports_without_embedding_or_writing(indexer_env, test_db, article):
    result = indexer.index_article(article.id, dry_run=True)

    expected = _expected(article)
    assert result.status == "dry_run"
    assert result.embedded == len(expected)
    assert result.embed_tokens == sum(c.token_count for c in expected)
    assert result.by_language == {
        "en": sum(1 for c in expected if c.meta["language"] == "en"),
        "es": sum(1 for c in expected if c.meta["language"] == "es"),
    }
    assert indexer_env.calls == []
    assert _rows(test_db, article.id) == []


# --- The sweep script ---------------------------------------------------------


def test_sweep_is_idempotent(indexer_env, test_db, article, article_factory):
    other = article_factory(title="Other", content=_body(("Only", _para("only"))))

    assert reindex_knowledge.main(["--all"]) == 0
    first = len(indexer_env.texts)
    assert first > 0
    indexer_env.calls.clear()

    assert reindex_knowledge.main(["--all"]) == 0
    assert indexer_env.calls == []
    assert _rows(test_db, other.id)


def test_sweep_dry_run_totals(indexer_env, test_db, article):
    results = reindex_knowledge.sweep(reindex_knowledge.article_ids(), dry_run=True)
    totals = reindex_knowledge.summarize(results, dry_run=True)

    expected = _expected(article)
    assert totals["chunks"] == totals["embedded"] == len(expected)
    assert totals["embed_tokens"] == sum(c.token_count for c in expected)
    assert totals["est_cost_usd"] == embeddings.estimate_cost(
        embeddings.document_model(), totals["embed_tokens"]
    )
    assert indexer_env.calls == []


def test_sweep_refuses_a_real_run_without_a_key(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "voyage_api_key", "")
    assert reindex_knowledge.main(["--all"]) == 1


# --- Write sites --------------------------------------------------------------

# Every endpoint that writes an article's title or body. Each must schedule a
# reindex; the guard below fails when a new route writes article content
# without appearing here. (Excerpt-only writes are not listed: the excerpt is
# not part of any chunk's embedding text.)
REINDEX_WRITE_SITES = {
    ("POST", "/api/v1/admin/dashboard/articles/{article_id}/write"),
    ("POST", "/api/v1/admin/dashboard/articles/{article_id}/translate"),
    ("POST", "/api/v1/admin/dashboard/articles/{article_id}/grammar-apply"),
    ("PUT", "/api/v1/admin/dashboard/articles/{article_id}"),
    ("POST", "/api/v1/admin/dashboard/articles/{article_id}/series-sections"),
    ("POST", "/api/v1/admin/dashboard/fact-check/{subject_type}/{subject_id}/corrections/apply"),
    ("POST", "/api/v1/admin/translate/article/{article_id}/title"),
    ("POST", "/api/v1/admin/translate/article/{article_id}/content"),
    ("POST", "/api/v1/admin/articles/series/{series_parent_id}/generate-sections"),
    ("POST", "/api/v1/admin/articles/{article_id}/grammar-check"),
    ("POST", "/api/v1/admin/articles/{article_id}/es-accuracy-check"),
}

# What "writes article content" looks like in an endpoint's source: a direct
# assignment, or a call into a service that rewrites the body.
_ARTICLE_WRITE = re.compile(
    r"\barticle\.(?:title|title_es|content|content_es)\s*=(?!=)"
    r"|\brun_grammar_check\(|\brun_es_accuracy_check\(|\bapply_corrections\("
    r"|\bgenerate_sections_for_series\(|\bgenerate_article_content\("
)
_SCHEDULES = re.compile(r"\bschedule_(?:series_)?reindex\(")


def _routes():
    """Every APIRoute of every ``app.api`` router, at its mounted path.

    Walks the routers rather than ``app.routes``: newer FastAPI keeps an included
    router as one lazy ``_IncludedRouter`` entry there. Every router is mounted
    under ``/api/v1`` (``app/main.py``).
    """
    import app.api as api_pkg

    for info in pkgutil.iter_modules(api_pkg.__path__):
        module = importlib.import_module(f"app.api.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    yield method, f"/api/v1{route.path}", inspect.getsource(route.endpoint)


def test_route_walk_sees_the_admin_routers():
    paths = {p for _, p, _ in _routes()}
    assert "/api/v1/admin/dashboard/articles" in paths
    assert "/api/v1/admin/translate/taxonomies/all" in paths


def test_every_listed_write_site_exists_and_schedules_a_reindex():
    found = {(m, p): src for m, p, src in _routes()}
    for site in REINDEX_WRITE_SITES:
        assert site in found, f"{site} is listed but no such route exists"
        assert _SCHEDULES.search(found[site]), f"{site} does not schedule a reindex"
        # Keeps the unlisted-route guard below honest: it would see this site.
        assert _ARTICLE_WRITE.search(found[site]), f"{site} is not detected as a write"


def test_no_unlisted_route_writes_article_content():
    unlisted = sorted(
        (m, p)
        for m, p, src in _routes()
        if _ARTICLE_WRITE.search(src) and (m, p) not in REINDEX_WRITE_SITES
    )
    assert unlisted == [], (
        "These routes write article content but are not in REINDEX_WRITE_SITES: "
        f"{unlisted}. Call schedule_reindex(background_tasks, article_id) after the "
        "commit and add the route to the list."
    )


def test_put_article_indexes_after_the_response(
    indexer_env, test_client, admin_token, test_db, article
):
    response = test_client.put(
        f"/api/v1/admin/dashboard/articles/{article.id}",
        json={
            "title": "The Canal",
            "content_html": "<h2>Origins</h2><p>" + _para("new", 30) + "</p>",
            "title_es": "El Canal",
            "content_html_es": "<h2>Orígenes</h2><p>" + _para("nuevo", 30) + "</p>",
        },
    )

    assert response.status_code == 200
    rows = _rows(test_db, article.id)
    assert {r.language for r in rows} == {"en", "es"}
    assert all(embedded for *_, embedded in rows)
    assert indexer_env.calls
