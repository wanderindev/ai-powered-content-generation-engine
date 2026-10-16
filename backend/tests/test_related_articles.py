"""Related articles: find_related + GET /articles/{id}/related (#629)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from app.api import articles as articles_api
from app.models.category import EXCLUDED_TAXONOMY_ID, Category, Taxonomy
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.media import Media
from app.services import embeddings
from app.services.knowledge.related import find_related
from sqlalchemy import delete, insert

DIMS = 1024


def _vec(*weights: tuple[int, float]) -> list[float]:
    v = [0.0] * DIMS
    for i, w in weights:
        v[i] = w
    return v


SOURCE = _vec((0, 1.0))
NEAR = _vec((0, 1.0), (1, 0.2))
MID = _vec((0, 1.0), (1, 1.0))
FAR = _vec((1, 1.0))

_counter = {"n": 0}


def _chunk(db, article_id, embedding, *, language="en", source_type="ARTICLE"):
    _counter["n"] += 1
    content = f"chunk {_counter['n']}"
    db.execute(
        insert(KnowledgeChunk.__table__).values(
            source_type=source_type,
            source_id=article_id if source_type == "ARTICLE" else _counter["n"],
            article_id=article_id,
            language=language,
            chunk_index=_counter["n"],
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            token_count=3,
            embedding_model="voyage-4" if embedding is not None else None,
            embedding=embedding,
        )
    )
    db.commit()


def _bio_category(db):
    tax = Taxonomy(id=EXCLUDED_TAXONOMY_ID, name="Notable Figures", description="Bios")
    db.add(tax)
    db.commit()
    cat = Category(name="Biographies", taxonomy_id=tax.id, description="Bios")
    db.add(cat)
    db.commit()
    return cat


@pytest.fixture(autouse=True)
def _fresh_cache():
    articles_api.clear_related_cache()
    yield
    articles_api.clear_related_cache()


@pytest.fixture(autouse=True)
def no_voyage(monkeypatch):
    """Any Voyage call fails the test: related articles use stored vectors only."""

    def boom(*args, **kwargs):
        raise AssertionError("related articles must not call Voyage")

    for name in ("_embed", "_post", "embed_query", "embed_queries", "embed_documents", "rerank"):
        monkeypatch.setattr(embeddings, name, boom)


@pytest.fixture
def corpus(test_db, article_factory, category_factory):
    """A series source with its family, ranked candidates, and hidden articles."""
    # One shared category: every factory call would otherwise add a taxonomy and
    # run the sequence into the explicit id 5.
    cat = category_factory()
    bio_category = _bio_category(test_db)
    source = article_factory(title="Source", title_es="Fuente", category=cat)
    child = article_factory(
        title="Source part 2", series_parent=source, series_order=1, category=cat
    )
    child2 = article_factory(
        title="Source part 3", series_parent=source, series_order=2, category=cat
    )
    same_research = article_factory(
        title="Same research", research_id=source.research_id, category=cat
    )
    near = article_factory(title="Near", title_es="Cerca", category=cat)
    near.excerpt_es = "Extracto cercano"
    mid = article_factory(title="Mid", category=cat)
    far = article_factory(title="Far", category=cat)
    scheduled = article_factory(
        title="Scheduled", published_at=datetime.now(UTC) + timedelta(days=3), category=cat
    )
    bio = article_factory(title="Bio", category=bio_category)
    draft = article_factory(title="Draft", status="PENDING", published_at=None, category=cat)
    unpublished = article_factory(title="Unpublished", published_at=None, category=cat)
    test_db.commit()

    for a in (source, child, child2, same_research, scheduled, bio, draft, unpublished):
        _chunk(test_db, a.id, SOURCE)
    _chunk(test_db, near.id, NEAR)
    _chunk(test_db, mid.id, MID)
    _chunk(test_db, far.id, FAR)
    return {
        "source": source,
        "child": child,
        "child2": child2,
        "same_research": same_research,
        "near": near,
        "mid": mid,
        "far": far,
        "scheduled": scheduled,
        "bio": bio,
        "draft": draft,
        "unpublished": unpublished,
    }


def _ids(hits):
    return [h.article_id for h in hits]


# --- find_related -----------------------------------------------------------------


def test_ranks_by_centroid_and_excludes_self_family_and_hidden(test_db, corpus):
    hits = find_related(test_db, corpus["source"].id, k=10)
    assert _ids(hits) == [corpus["near"].id, corpus["mid"].id, corpus["far"].id]
    assert hits[0].similarity > hits[1].similarity > hits[2].similarity


def test_series_child_excludes_parent_and_siblings(test_db, corpus):
    hits = find_related(test_db, corpus["child"].id, k=10)
    assert _ids(hits) == [corpus["near"].id, corpus["mid"].id, corpus["far"].id]


def test_centroid_averages_chunks_and_languages(test_db, article_factory):
    source = article_factory(title="Src")
    _chunk(test_db, source.id, SOURCE, language="en")
    _chunk(test_db, source.id, SOURCE, language="es")
    # Centroid of (axis 1) and (axis 0) beats a single far chunk; ES-only counts.
    mixed = article_factory(title="Mixed")
    _chunk(test_db, mixed.id, FAR, language="en")
    _chunk(test_db, mixed.id, SOURCE, language="es")
    far = article_factory(title="Far only")
    _chunk(test_db, far.id, FAR)
    spanish_only = article_factory(title="ES only")
    _chunk(test_db, spanish_only.id, NEAR, language="es")
    hits = find_related(test_db, source.id, k=10)
    assert _ids(hits) == [spanish_only.id, mixed.id, far.id]


def test_ignores_claim_chunks_and_unembedded_chunks(test_db, article_factory):
    source = article_factory(title="Src")
    _chunk(test_db, source.id, SOURCE)
    other = article_factory(title="Claims only")
    _chunk(test_db, other.id, SOURCE, source_type="ARTICLE_CLAIM")
    pending = article_factory(title="Not embedded")
    _chunk(test_db, pending.id, None)
    assert find_related(test_db, source.id) == []


def test_hidden_source_returns_none_publicly(test_db, corpus):
    for key in ("scheduled", "bio", "draft", "unpublished"):
        assert find_related(test_db, corpus[key].id) is None, key
    assert find_related(test_db, 999_999) is None


def test_internal_visibility_includes_scheduled(test_db, corpus):
    hits = find_related(test_db, corpus["scheduled"].id, k=20, visibility="internal")
    ids = _ids(hits)
    assert corpus["source"].id in ids
    assert corpus["scheduled"].id not in ids
    assert corpus["draft"].id not in ids


def test_source_without_chunks_is_empty(test_db, article_factory):
    lonely = article_factory(title="No chunks")
    other = article_factory(title="Other")
    _chunk(test_db, other.id, SOURCE)
    assert find_related(test_db, lonely.id) == []


def test_rejects_bad_arguments(test_db):
    with pytest.raises(ValueError):
        find_related(test_db, 1, visibility="everyone")
    with pytest.raises(ValueError):
        find_related(test_db, 1, k=0)


# --- Endpoint ---------------------------------------------------------------------


def test_endpoint_returns_public_related_with_cache_header(test_client, test_db, corpus):
    media = Media(
        filename="near.jpg",
        original_filename="near.jpg",
        file_path="/static/images/articles/near.jpg",
        file_size=1,
        mime_type="image/jpeg",
        media_type="IMAGE",
        source="GENERATED",
        title="Near image",
        alt_text="A near image",
    )
    test_db.add(media)
    test_db.commit()
    corpus["near"].feature_image_id = media.id
    test_db.commit()

    resp = test_client.get(f"/api/v1/articles/{corpus['source'].id}/related")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=600"
    body = resp.json()
    assert [r["id"] for r in body] == [corpus["near"].id, corpus["mid"].id, corpus["far"].id]
    assert body[0]["title"] == "Near"
    assert body[0]["slug"] == corpus["near"].slug
    assert body[0]["feature_image_url"] == "/static/images/articles/near.jpg"
    assert body[0]["feature_image_alt"] == "A near image"
    assert body[1]["feature_image_url"] is None
    hidden = {corpus[k].id for k in ("scheduled", "bio", "draft", "unpublished", "child")}
    assert not hidden & {r["id"] for r in body}


def test_endpoint_spanish_with_english_fallback(test_client, corpus):
    body = test_client.get(f"/api/v1/articles/{corpus['source'].id}/related?lang=es").json()
    near, mid = body[0], body[1]
    assert near["title"] == "Cerca"
    assert near["excerpt"] == "Extracto cercano"
    assert mid["title"] == "Mid"
    assert mid["excerpt"] == "Test excerpt"


def test_endpoint_404s_for_non_public_ids(test_client, corpus):
    for key in ("scheduled", "bio", "draft", "unpublished"):
        resp = test_client.get(f"/api/v1/articles/{corpus[key].id}/related")
        assert resp.status_code == 404, key
        assert resp.json()["detail"] == "Article not found"
    assert test_client.get("/api/v1/articles/999999/related").status_code == 404


def test_by_slug_related_is_a_slug_lookup_not_the_related_route(test_client, article_factory):
    """`/{article_id}/related` must not shadow `/by-slug/{slug}` (#657).

    Declared first, it captured `by-slug` as an int path param and answered 422.
    """
    resp = test_client.get("/api/v1/articles/by-slug/related")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Article not found"

    article = article_factory(title="Related")
    assert article.slug == "related"
    resp = test_client.get("/api/v1/articles/by-slug/related")
    assert resp.status_code == 200
    assert resp.json()["id"] == article.id


def test_endpoint_caps_k(test_client, test_db, article_factory):
    source = article_factory(title="Src")
    _chunk(test_db, source.id, SOURCE)
    for i in range(9):
        a = article_factory(title=f"Candidate {i}")
        _chunk(test_db, a.id, _vec((0, 1.0), (1, 0.1 * (i + 1))))
    url = f"/api/v1/articles/{source.id}/related"
    assert len(test_client.get(url).json()) == articles_api.RELATED_DEFAULT_K
    assert len(test_client.get(f"{url}?k=50").json()) == articles_api.RELATED_MAX_K
    assert len(test_client.get(f"{url}?k=2").json()) == 2
    assert test_client.get(f"{url}?k=0").status_code == 422


def test_endpoint_empty_list_without_chunks(test_client, article_factory):
    lonely = article_factory(title="No chunks")
    resp = test_client.get(f"/api/v1/articles/{lonely.id}/related")
    assert resp.status_code == 200
    assert resp.json() == []


def test_endpoint_serves_repeat_requests_from_the_ttl_cache(test_client, test_db, corpus):
    url = f"/api/v1/articles/{corpus['source'].id}/related"
    first = test_client.get(url).json()
    test_db.execute(delete(KnowledgeChunk.__table__))
    test_db.commit()
    assert test_client.get(url).json() == first
    # A different language is its own key.
    assert test_client.get(f"{url}?lang=es").json() == []
    articles_api.clear_related_cache()
    assert test_client.get(url).json() == []


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(articles_api, "RELATED_CACHE_MAX_ENTRIES", 2)
    for i in range(3):
        articles_api._related_cache_put((i, "en", 4), [])
    assert articles_api._related_cache_get((0, "en", 4)) is None
    assert articles_api._related_cache_get((2, "en", 4)) == []
