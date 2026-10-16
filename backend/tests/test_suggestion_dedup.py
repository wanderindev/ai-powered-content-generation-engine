"""Semantic duplicate-topic detection for article suggestions (#630).

Fake embeddings throughout (deterministic axis vectors); no Voyage traffic.
Covers: the flag-off identity (prompt pinned to the pre-#630 text, no Voyage
call, no flag columns written), the steer, a duplicate flagged against an
article and against a suggestion (with the lazy vector backfill), a novel topic
not flagged, the degrade paths, and the admin API fields.
"""

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.core.config import get_settings
from app.models.article_suggestion import DEDUP_COLUMNS, ArticleSuggestion
from app.models.category import Category, Taxonomy
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings, suggestion_dedup
from app.services import suggestion_generation as sg
from pydantic import BaseModel, ConfigDict
from sqlalchemy import event, insert, inspect, select, text, update
from sqlalchemy.exc import DataError

DIMS = 1024

# sha256 of the prompt origin/main (pre-#630) builds for the fixture inputs
# below; the flag-off path must still produce exactly this text.
PRE_630_PROMPT_SHA256 = "dbd0429e09d3c6bcd2def9355835dec52cf66d7418c1afc5a86bfcee6f65b46c"


def _vec(*weights: tuple[int, float]) -> list[float]:
    v = [0.0] * DIMS
    for i, w in weights:
        v[i] = w
    return v


def _unit(sim: float, axis: int) -> list[float]:
    """A unit vector whose cosine similarity with axis 0 is ``sim``."""
    return _vec((0, sim), (axis, math.sqrt(max(0.0, 1 - sim * sim))))


AXIS0 = _vec((0, 1.0))
AXIS1 = _vec((1, 1.0))
AXIS2 = _vec((2, 1.0))

_counter = {"n": 0}


def _chunk(db, article_id, embedding, language="en"):
    _counter["n"] += 1
    content = f"chunk {_counter['n']}"
    db.execute(
        insert(KnowledgeChunk.__table__).values(
            source_type="ARTICLE",
            source_id=article_id,
            article_id=article_id,
            language=language,
            chunk_index=_counter["n"],
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            token_count=3,
            embedding_model="voyage-4",
            embedding=embedding,
        )
    )
    db.commit()


@pytest.fixture
def category(test_db):
    tax = Taxonomy(name="Historical Panama", description="Panama history")
    test_db.add(tax)
    test_db.commit()
    cat = Category(name="Colonial Era", taxonomy_id=tax.id, description="Spanish colonial period")
    test_db.add(cat)
    test_db.commit()
    test_db.add(ArticleSuggestion(category_id=cat.id, title="Old suggestion", point_of_view="POV"))
    test_db.commit()
    test_db.refresh(cat)
    return cat


class FakeLLM:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps({"suggestions": self.items})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _item(title, main_topic):
    return {"title": title, "main_topic": main_topic, "sub_topics": [], "point_of_view": "POV"}


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM([_item("New A", "Topic A"), _item("New B", "Topic B")])
    monkeypatch.setattr(sg, "create_message", fake)
    return fake


@pytest.fixture
def no_voyage(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("flag off must not call Voyage")

    for name in ("_embed", "_post", "embed_query", "embed_queries", "embed_documents", "rerank"):
        monkeypatch.setattr(embeddings, name, boom)


@pytest.fixture
def dedup_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "suggestion_dedup_enabled", True)
    monkeypatch.setattr(settings, "suggestion_dedup_min_similarity", 0.85)
    monkeypatch.setattr(settings, "suggestion_dedup_steer_titles", 10)


def _fake_voyage(monkeypatch, *, query=AXIS0, documents=None):
    """Fake embed_query / embed_documents; ``documents`` maps a title -> vector.

    Any other text gets its own axis (from 10 up), so it matches nothing.
    """
    seen = {"query": [], "documents": []}
    axes: dict[str, int] = {}

    def default(text):
        return _vec((axes.setdefault(text, 10 + len(axes)), 1.0))

    def embed_query(text, *, feature="unlabeled", model=None):
        seen["query"].append(text)
        return query

    def embed_documents(texts, *, feature="unlabeled", model=None):
        seen["documents"].append(list(texts))
        return [(documents or {}).get(t.split("\n\n", 1)[0]) or default(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed_query", embed_query)
    monkeypatch.setattr(embeddings, "embed_documents", embed_documents)
    return seen


def _generate(test_db, category):
    return sg.generate_historical_suggestions(
        category=category, taxonomy=category.taxonomy, num_suggestions=2, db=test_db
    )


def _flag(test_db, suggestion_id):
    """The #630 flag columns, read with Core (they are not ORM attributes)."""
    c = ArticleSuggestion.__table__.c
    return test_db.execute(
        select(c.duplicate_article_id, c.duplicate_suggestion_id, c.duplicate_similarity).where(
            c.id == suggestion_id
        )
    ).one()


def _set_flag(test_db, suggestion_id, **values):
    table = ArticleSuggestion.__table__
    test_db.execute(update(table).where(table.c.id == suggestion_id).values(**values))
    test_db.commit()


def _stored_vector(test_db, suggestion_id):
    table = ArticleSuggestion.__table__
    return test_db.execute(
        select(table.c.embedding).where(table.c.id == suggestion_id)
    ).scalar_one()


# --- Flag off: byte-for-byte unchanged -----------------------------------------


def test_flag_off_prompt_calls_and_rows_unchanged(test_db, category, llm, no_voyage):
    assert get_settings().suggestion_dedup_enabled is False
    rows = _generate(test_db, category)

    assert len(llm.calls) == 1
    prompt = llm.calls[0]["messages"][0]["content"]
    assert hashlib.sha256(prompt.encode()).hexdigest() == PRE_630_PROMPT_SHA256
    assert "Already Covered" not in prompt
    assert llm.calls[0]["feature"] == "suggestion_generation"
    for row in rows:
        assert _flag(test_db, row.id).duplicate_article_id is None
        assert _flag(test_db, row.id).duplicate_suggestion_id is None
        assert _flag(test_db, row.id).duplicate_similarity is None
        assert _stored_vector(test_db, row.id) is None


# --- Flag on --------------------------------------------------------------------


def test_steer_titles_in_prompt(test_db, category, llm, dedup_on, monkeypatch, article_factory):
    near = article_factory(title="Portobelo Fairs", category=category)
    far = article_factory(title="Canal Treaty", category=category)
    _chunk(test_db, near.id, AXIS0)
    _chunk(test_db, far.id, AXIS1)
    seen = _fake_voyage(monkeypatch, query=_unit(0.9, 1))

    _generate(test_db, category)

    prompt = llm.calls[0]["messages"][0]["content"]
    assert seen["query"] == ["Colonial Era: Spanish colonial period"]
    assert "Already Covered on the Site" in prompt
    covered = prompt.split("Already Covered on the Site", 1)[1].split("SPECIAL INSTRUCTION", 1)[0]
    # Nearest first; both are within the cap.
    assert covered.index("Portobelo Fairs") < covered.index("Canal Treaty")


def test_steer_is_capped(test_db, category, llm, dedup_on, monkeypatch, article_factory):
    monkeypatch.setattr(get_settings(), "suggestion_dedup_steer_titles", 1)
    for i, title in enumerate(["First", "Second"]):
        a = article_factory(title=title, category=category)
        _chunk(test_db, a.id, _unit(0.9 - i * 0.3, 1))
    _fake_voyage(monkeypatch)

    _generate(test_db, category)

    prompt = llm.calls[0]["messages"][0]["content"]
    assert "- First" in prompt and "- Second" not in prompt


def test_duplicate_of_article_is_flagged(
    test_db, category, llm, dedup_on, monkeypatch, article_factory
):
    article = article_factory(title="Portobelo Fairs", category=category)
    _chunk(test_db, article.id, AXIS0)
    _chunk(test_db, article.id, AXIS0, language="es")
    _fake_voyage(monkeypatch, documents={"New A": _unit(0.95, 1), "New B": AXIS2})

    rows = {r.title: r for r in _generate(test_db, category)}

    a, b = rows["New A"], rows["New B"]
    assert _flag(test_db, a.id).duplicate_article_id == article.id
    assert _flag(test_db, a.id).duplicate_suggestion_id is None
    assert _flag(test_db, a.id).duplicate_similarity == pytest.approx(0.95, abs=1e-4)
    # A novel topic is not flagged, and nothing was blocked or deleted.
    assert (
        _flag(test_db, b.id).duplicate_article_id is None
        and _flag(test_db, b.id).duplicate_suggestion_id is None
    )
    assert _flag(test_db, b.id).duplicate_similarity is None
    assert (
        test_db.query(ArticleSuggestion).filter(ArticleSuggestion.title.like("New %")).count() == 2
    )


def test_duplicate_of_suggestion_is_flagged_and_old_rows_backfilled(
    test_db, category, llm, dedup_on, monkeypatch
):
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    seen = _fake_voyage(monkeypatch, documents={"Old suggestion": AXIS1, "New A": AXIS1})

    rows = {r.title: r for r in _generate(test_db, category)}

    assert _flag(test_db, rows["New A"].id).duplicate_suggestion_id == old.id
    assert _flag(test_db, rows["New A"].id).duplicate_article_id is None
    assert _flag(test_db, rows["New A"].id).duplicate_similarity == pytest.approx(1.0, abs=1e-4)
    assert _flag(test_db, rows["New B"].id).duplicate_suggestion_id is None
    # One Voyage request: the two new suggestions plus the unembedded old one.
    assert seen["documents"] == [["New A\n\nTopic A", "New B\n\nTopic B", "Old suggestion"]]
    for sid in (old.id, rows["New A"].id, rows["New B"].id):
        assert _stored_vector(test_db, sid) is not None


def test_below_floor_is_not_flagged(test_db, category, llm, dedup_on, monkeypatch, article_factory):
    article = article_factory(title="Portobelo Fairs", category=category)
    _chunk(test_db, article.id, AXIS0)
    _fake_voyage(monkeypatch, documents={"New A": _unit(0.8, 1)})

    rows = _generate(test_db, category)

    assert all(_flag(test_db, r.id).duplicate_article_id is None for r in rows)
    assert all(_flag(test_db, r.id).duplicate_similarity is None for r in rows)


def test_embeddings_disabled_degrades_to_unchanged_prompt(test_db, category, llm, dedup_on):
    # conftest forces VOYAGE_API_KEY="" -> EmbeddingsDisabledError on every call.
    rows = _generate(test_db, category)

    prompt = llm.calls[0]["messages"][0]["content"]
    assert hashlib.sha256(prompt.encode()).hexdigest() == PRE_630_PROMPT_SHA256
    assert len(rows) == 2
    assert all(
        _flag(test_db, r.id).duplicate_article_id is None
        and _flag(test_db, r.id).duplicate_suggestion_id is None
        for r in rows
    )


def test_voyage_failure_degrades(test_db, category, llm, dedup_on, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise embeddings.EmbeddingsError("boom")

    monkeypatch.setattr(embeddings, "embed_query", fail)
    monkeypatch.setattr(embeddings, "embed_documents", fail)

    rows = _generate(test_db, category)

    assert len(rows) == 2
    assert "Already Covered" not in llm.calls[0]["messages"][0]["content"]
    assert all(_flag(test_db, r.id).duplicate_similarity is None for r in rows)
    warnings = [r for r in caplog.records if r.name == suggestion_dedup.__name__]
    assert [r.getMessage() for r in warnings] == [
        "suggestion dedup: steer embedding failed; no steer",
        "suggestion dedup: embedding failed; no flag",
    ]


def test_matching_failure_degrades(test_db, category, llm, dedup_on, monkeypatch):
    _fake_voyage(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("sql broke")

    monkeypatch.setattr(suggestion_dedup.related, "nearest_articles", fail)
    monkeypatch.setattr(suggestion_dedup.retrieval, "search", fail)

    rows = _generate(test_db, category)

    assert len(rows) == 2
    assert test_db.query(ArticleSuggestion).count() == 3
    assert all(_flag(test_db, r.id).duplicate_similarity is None for r in rows)


# --- Admin API --------------------------------------------------------------------


def test_api_exposes_duplicate_fields(test_client, admin_token, test_db, category, article_factory):
    article = article_factory(title="Portobelo Fairs", category=category)
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    by_article = ArticleSuggestion(
        category_id=category.id, title="Dup of article", point_of_view="POV"
    )
    by_suggestion = ArticleSuggestion(
        category_id=category.id, title="Dup of suggestion", point_of_view="POV"
    )
    test_db.add_all([by_article, by_suggestion])
    test_db.commit()
    _set_flag(test_db, by_article.id, duplicate_article_id=article.id, duplicate_similarity=0.91)
    _set_flag(test_db, by_suggestion.id, duplicate_suggestion_id=old.id, duplicate_similarity=0.88)

    resp = test_client.get("/api/v1/admin/dashboard/suggestions")
    assert resp.status_code == 200
    rows = {r["title"]: r for r in resp.json()}
    assert rows["Dup of article"]["duplicate_article_id"] == article.id
    assert rows["Dup of article"]["duplicate_article_title"] == "Portobelo Fairs"
    assert rows["Dup of article"]["duplicate_article_slug"] == article.slug
    assert rows["Dup of article"]["duplicate_similarity"] == pytest.approx(0.91)
    assert rows["Dup of suggestion"]["duplicate_suggestion_id"] == old.id
    assert rows["Dup of suggestion"]["duplicate_suggestion_title"] == "Old suggestion"
    assert rows["Old suggestion"]["duplicate_article_id"] is None
    assert rows["Old suggestion"]["duplicate_similarity"] is None

    detail = test_client.get(f"/api/v1/admin/dashboard/suggestions/{by_article.id}").json()
    assert detail["duplicate_article_title"] == "Portobelo Fairs"
    assert detail["duplicate_similarity"] == pytest.approx(0.91)


def test_editing_title_clears_the_stored_vector(test_client, admin_token, test_db, category):
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    table = ArticleSuggestion.__table__
    test_db.execute(table.update().where(table.c.id == old.id).values(embedding=AXIS1))
    test_db.commit()

    resp = test_client.put(
        f"/api/v1/admin/dashboard/suggestions/{old.id}",
        json={"title": "Renamed", "point_of_view": "POV", "sub_topics": []},
    )
    assert resp.status_code == 200
    test_db.expire_all()
    assert _stored_vector(test_db, old.id) is None


def test_clear_embedding_surfaces_a_pending_flush_error(test_db, category, caplog):
    """An unrelated pending change that fails to flush raises its real cause (#659)."""
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    old.title = "x" * 300  # String(255): StringDataRightTruncation on flush
    with caplog.at_level("WARNING"), pytest.raises(DataError):
        suggestion_dedup.clear_embedding(test_db, old.id)
    test_db.rollback()
    assert "could not clear the stored vector" not in caplog.text


def test_editing_title_clears_the_duplicate_flag(test_client, admin_token, test_db, category):
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    other = ArticleSuggestion(category_id=category.id, title="Other", point_of_view="POV")
    test_db.add(other)
    test_db.commit()
    _set_flag(
        test_db,
        old.id,
        embedding=AXIS1,
        duplicate_suggestion_id=other.id,
        duplicate_similarity=0.9,
    )
    url = f"/api/v1/admin/dashboard/suggestions/{old.id}"

    # A point-of-view edit is not embedded: the flag stays.
    resp = test_client.put(
        url, json={"title": "Old suggestion", "point_of_view": "New POV", "sub_topics": []}
    )
    assert resp.status_code == 200
    test_db.expire_all()
    assert _flag(test_db, old.id).duplicate_suggestion_id == other.id
    assert _flag(test_db, old.id).duplicate_similarity == pytest.approx(0.9)
    assert _stored_vector(test_db, old.id) is not None

    # A title edit makes both the vector and the flag stale: both are cleared.
    resp = test_client.put(
        url, json={"title": "Renamed", "point_of_view": "New POV", "sub_topics": []}
    )
    assert resp.status_code == 200
    test_db.expire_all()
    flag = _flag(test_db, old.id)
    assert flag.duplicate_article_id is None
    assert flag.duplicate_suggestion_id is None
    assert flag.duplicate_similarity is None
    assert _stored_vector(test_db, old.id) is None


def test_backfill_does_not_bump_updated_at(test_db, category, llm, dedup_on, monkeypatch):
    _fake_voyage(monkeypatch)
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    old_id = old.id
    fixed = datetime(2020, 1, 1, tzinfo=UTC)
    table = ArticleSuggestion.__table__
    test_db.execute(update(table).where(table.c.id == old_id).values(updated_at=fixed))
    test_db.commit()

    _generate(test_db, category)

    assert _stored_vector(test_db, old_id) is not None
    stamp = test_db.execute(select(table.c.updated_at).where(table.c.id == old_id)).scalar_one()
    assert stamp == fixed


def test_dedup_columns_are_not_class_attributes():
    for name in DEDUP_COLUMNS:
        assert name in ArticleSuggestion.__table__.c
        assert not hasattr(ArticleSuggestion, name)
        assert not hasattr(ArticleSuggestion(), name)

    class Probe(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        id: int | None = None
        duplicate_article_id: int | None = None

    probe = Probe.model_validate(ArticleSuggestion(title="t", point_of_view="p"))
    assert probe.duplicate_article_id is None


def test_single_duplicate_check(test_db, category, article_factory):
    article = article_factory(category=category)
    old = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one()
    with pytest.raises(Exception, match="ck_article_suggestions_single_duplicate"):
        _set_flag(test_db, old.id, duplicate_article_id=article.id, duplicate_suggestion_id=old.id)
    test_db.rollback()


# --- Deploy window: no ORM statement names the 0045 columns -----------------------


def test_orm_statements_never_name_dedup_columns(test_db, category):
    """Entity queries must work against a pre-0045 database (deploy window)."""
    statements: list[str] = []

    def _capture(conn, cursor, statement, *args):
        statements.append(statement)

    bind = test_db.get_bind()
    event.listen(bind, "before_cursor_execute", _capture)
    try:
        row = ArticleSuggestion(category_id=category.id, title="Guard", point_of_view="POV")
        test_db.add(row)
        test_db.commit()
        test_db.refresh(row)
        row.title = "Guard 2"
        test_db.commit()
        test_db.execute(select(ArticleSuggestion)).all()
        test_db.query(ArticleSuggestion).filter_by(id=row.id).one()
    finally:
        event.remove(bind, "before_cursor_execute", _capture)
    assert statements
    pattern = r"\bembedding\b|duplicate_article_id|duplicate_suggestion_id|duplicate_similarity"
    assert not any(re.search(pattern, s) for s in statements)
    for name in DEDUP_COLUMNS:
        assert name in ArticleSuggestion.__table__.c
        assert name not in ArticleSuggestion.__mapper__.attrs


def test_new_code_works_against_a_pre_0045_schema(test_db, category):
    """Drop the four columns (transactional DDL, rolled back) and use the model."""
    old_id = test_db.query(ArticleSuggestion).filter_by(title="Old suggestion").one().id
    test_db.expire_all()
    for name in DEDUP_COLUMNS:
        test_db.execute(text(f"ALTER TABLE article_suggestions DROP COLUMN {name} CASCADE"))
    try:
        # ORM entity reads / writes, as the public article list and the driver do.
        assert test_db.query(ArticleSuggestion).get(old_id).title == "Old suggestion"
        test_db.execute(select(ArticleSuggestion)).all()
        test_db.add(ArticleSuggestion(category_id=category.id, title="New", point_of_view="P"))
        test_db.flush()
        # The admin fields and the vector clear degrade, in a SAVEPOINT...
        assert suggestion_dedup.duplicate_fields(test_db, [old_id]) == {}
        suggestion_dedup.clear_embedding(test_db, old_id)
        # ...so the caller's transaction is still usable.
        assert test_db.query(ArticleSuggestion).count() == 2
    finally:
        test_db.rollback()
    assert "duplicate_similarity" in {
        c["name"] for c in inspect(test_db.get_bind()).get_columns("article_suggestions")
    }
