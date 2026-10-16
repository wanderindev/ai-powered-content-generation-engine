"""Ranked, forgiving media search (#590).

One helper (app/services/media_search.py) serves the picker
(GET /media-library/media), the library grid (GET /media-library/candidates)
and the public library (GET /public-media), over the stored generated
``search_vector`` columns (migration 0041, SQL in app/models/search_sql.py).
"""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.models import search_sql
from app.models.media import Media
from app.models.media_candidate import MediaCandidate
from app.models.tag import Tag
from app.services.media_search import tokenize
from sqlalchemy import event, func, select, text
from sqlalchemy.dialects import postgresql

_n = 0


def _media(test_db, **overrides) -> Media:
    global _n
    _n += 1
    defaults = {
        "filename": f"img_{_n}.jpg",
        "original_filename": f"orig_{_n}.jpg",
        "file_path": f"https://cdn.example.com/img_{_n}.jpg",
        "thumbnail_path": f"https://cdn.example.com/thumb_{_n}.jpg",
        "file_size": 12345,
        "mime_type": "image/jpeg",
        "media_type": "IMAGE",
        "source": "WIKIMEDIA",
        "title": None,
        "caption": None,
        "alt_text": None,
        "attribution": None,
        "search_terms": None,
    }
    defaults.update(overrides)
    m = Media(**defaults)
    test_db.add(m)
    test_db.commit()
    test_db.refresh(m)
    return m


def _candidate(test_db, media=None, **overrides) -> MediaCandidate:
    global _n
    _n += 1
    defaults = {
        "source": "WIKIMEDIA",
        "external_id": f"File:Scan_{_n}.jpg",
        "external_url": f"https://example.com/ext_{_n}.jpg",
        "title": f"Scan {_n}",
        "description": None,
        "tags": None,
        "source_categories": None,
        "status": "APPROVED",
        "relevance_score": 0.9,
        "media_id": media.id if media else None,
        "crawled_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    c = MediaCandidate(**defaults)
    test_db.add(c)
    test_db.commit()
    test_db.refresh(c)
    return c


def _search_media(test_client, term):
    response = test_client.get("/api/v1/media-library/media", params={"search": term})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def culebra_rows(test_db):
    b = _media(
        test_db,
        title="Workers excavating the Culebra Cut, 1913",
        search_terms="obreros, excavación, Corte Culebra",
    )
    a = _media(test_db, title="Steam shovel at Culebra Cut")
    c = _media(test_db, title="Cathedral bell tower")
    return a, b, c


# ── Unit ─────────────────────────────────────────────────────────────────


def test_tokenize_dedupes_lowercases_and_keeps_accents():
    assert tokenize("Colón colón, Panamá!  ") == ["colón", "panamá"]
    assert tokenize("   ") == []
    assert tokenize(None) == []
    assert len(tokenize(" ".join(f"w{i}" for i in range(40)))) == 16


# ── /media-library/media (the picker) ────────────────────────────────────


class TestPickerSearch:
    def test_multi_word_partial_matches_rank(self, test_client, admin_token, culebra_rows):
        a, b, c = culebra_rows
        data = _search_media(test_client, "canal construction workers excavation Culebra 1910")
        assert data["total"] == 2
        ids = [i["id"] for i in data["items"]]
        assert ids == [b.id, a.id]

    def test_plural_and_singular_fold(self, test_client, admin_token, culebra_rows):
        _, b, _ = culebra_rows
        for term in ["worker", "excavation"]:
            data = _search_media(test_client, term)
            assert [i["id"] for i in data["items"]] == [b.id], term

    def test_accents_do_not_matter(self, test_client, test_db, admin_token):
        colon = _media(test_db, title="Harbour view", search_terms="puerto de Colón")
        panama = _media(test_db, title="Panama City waterfront")
        for term in ["colon", "Colón"]:
            assert [i["id"] for i in _search_media(test_client, term)["items"]] == [colon.id]
        assert [i["id"] for i in _search_media(test_client, "Panamá")["items"]] == [panama.id]

    def test_candidate_tag_and_category_only_matches(self, test_client, test_db, admin_token):
        by_tag = _media(test_db, title="Untitled scan one")
        _candidate(test_db, by_tag, tags=["Gatun Locks"])
        by_cat = _media(test_db, title="Untitled scan two")
        _candidate(test_db, by_cat, source_categories=["Miraflores Locks"])
        _media(test_db, title="Untitled scan three")

        assert [i["id"] for i in _search_media(test_client, "gatun")["items"]] == [by_tag.id]
        assert [i["id"] for i in _search_media(test_client, "miraflores")["items"]] == [by_cat.id]

    def test_media_tags_match(self, test_client, test_db, admin_token):
        tagged = _media(test_db, title="Untitled fort scan")
        _media(test_db, title="Another untitled scan")
        tag = Tag(name="Portobelo", name_es="Portobelo")
        test_db.add(tag)
        tagged.tags.append(tag)
        test_db.commit()

        assert [i["id"] for i in _search_media(test_client, "portobelo")["items"]] == [tagged.id]

    def test_long_image_query_ranks_instead_of_emptying(
        self, test_client, admin_token, culebra_rows
    ):
        _, b, c = culebra_rows
        data = _search_media(
            test_client,
            "Black and white photograph of workers excavating the Culebra Cut "
            "with steam shovels during canal construction",
        )
        assert data["total"] >= 1
        assert data["items"][0]["id"] == b.id
        assert c.id not in [i["id"] for i in data["items"]]

    def test_trigram_fallback_on_typo(self, test_client, admin_token, culebra_rows):
        a, b, c = culebra_rows
        data = _search_media(test_client, "Culebrra")
        assert {i["id"] for i in data["items"]} == {a.id, b.id}
        assert data["total"] == 2
        assert _search_media(test_client, "zeppelin")["total"] == 0

    def test_spanish_stopwords_do_not_match(self, test_client, test_db, admin_token, culebra_rows):
        _, b, _ = culebra_rows
        only_del = _media(test_db, title="Old bridge", search_terms="Puente del Rey")
        ids = [i["id"] for i in _search_media(test_client, "obreros del canal")["items"]]
        assert b.id in ids
        assert only_del.id not in ids

    def test_no_search_keeps_newest_first(self, test_client, admin_token, culebra_rows):
        data = _search_media(test_client, "")
        assert data["total"] == 3
        response = test_client.get("/api/v1/media-library/media")
        assert response.json()["total"] == 3


# ── /media-library/candidates ────────────────────────────────────────────


class TestCandidateSearch:
    def test_description_and_tag_matches_ranked_with_status_filter(
        self, test_client, test_db, admin_token
    ):
        by_desc = _candidate(
            test_db, status="PENDING", description="Gaillard Cut dredging operations"
        )
        by_tag = _candidate(test_db, status="PENDING", tags=["Gaillard Cut"])
        _candidate(test_db, status="REJECTED", description="Gaillard Cut slide")
        _candidate(test_db, status="PENDING", description="Cathedral bell tower")

        response = test_client.get(
            "/api/v1/media-library/candidates",
            params={"search": "gaillard", "status": "PENDING"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        # Tags weigh B, description C.
        assert [i["id"] for i in data["items"]] == [by_tag.id, by_desc.id]

        response = test_client.get(
            "/api/v1/media-library/candidates", params={"search": "gaillard"}
        )
        assert response.json()["total"] == 3


# ── /public-media ────────────────────────────────────────────────────────


class TestPublicSearch:
    def test_ranked_search_via_candidate_description(self, test_client, test_db):
        hit = _media(test_db, title="Untitled scan")
        _candidate(test_db, hit, description="Miraflores locks under construction")
        miss = _media(test_db, title="Another untitled scan")
        _candidate(test_db, miss, description="Cathedral bell tower")

        data = test_client.get("/api/v1/public-media", params={"search": "miraflores"}).json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == hit.id

    def test_visibility_filters_still_apply(self, test_client, test_db, article_factory):
        low = _media(test_db, title="Miraflores locks, low score")
        _candidate(test_db, low, relevance_score=0.5)
        featured = _media(test_db, title="Miraflores locks, featured")
        _candidate(test_db, featured)
        article = article_factory(title="An Article")
        article.feature_image_id = featured.id
        test_db.commit()
        visible = _media(test_db, title="Miraflores locks, visible")
        _candidate(test_db, visible)

        data = test_client.get("/api/v1/public-media", params={"search": "miraflores"}).json()
        assert [i["id"] for i in data["items"]] == [visible.id]
        assert data["total"] == 1

    def test_search_and_tag_combine(self, test_client, test_db):
        tagged = _media(test_db, title="Miraflores locks aerial")
        _candidate(test_db, tagged, tags=["Panama Canal"])
        untagged = _media(test_db, title="Miraflores locks gates")
        _candidate(test_db, untagged, tags=["Other"])

        data = test_client.get(
            "/api/v1/public-media", params={"search": "miraflores", "tag": "Panama Canal"}
        ).json()
        assert [i["id"] for i in data["items"]] == [tagged.id]
        assert data["total"] == 1


# ── Schema guards ────────────────────────────────────────────────────────


class TestSchemaGuards:
    def test_search_vector_is_not_an_orm_attribute(self, test_db):
        """No ORM statement may name the column: top-level entity SELECTs AND the
        count-subquery idiom every no-search path uses (``deferred`` was not
        enough — it still renders inside ``select(Media).subquery()``)."""
        dialect = postgresql.dialect()
        joined = select(Media, MediaCandidate).join(
            MediaCandidate, MediaCandidate.media_id == Media.id
        )
        statements = [
            select(Media),
            select(MediaCandidate),
            joined,
            select(func.count()).select_from(select(Media).subquery()),
            select(func.count()).select_from(select(MediaCandidate).subquery()),
            select(func.count()).select_from(joined.subquery()),
        ]
        for stmt in statements:
            assert "search_vector" not in str(stmt.compile(dialect=dialect)), str(stmt)

        captured: list[str] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            captured.append(statement)

        bind = test_db.get_bind()
        event.listen(bind, "before_cursor_execute", _capture)
        try:
            test_db.query(Media).count()
            test_db.query(MediaCandidate).count()
        finally:
            event.remove(bind, "before_cursor_execute", _capture)
        assert captured
        assert not any("search_vector" in s for s in captured)

        assert "search_vector" not in Media.__mapper__.attrs
        assert "search_vector" not in MediaCandidate.__mapper__.attrs
        assert "search_vector" in Media.__table__.c
        assert "search_vector" in MediaCandidate.__table__.c

    def test_non_search_paths_work_before_migration_0041(self, test_client, test_db, admin_token):
        """Simulate prod between deploy and ``alembic upgrade head``: hide both
        columns (rename, restored in ``finally``) and exercise the no-search
        reads plus ORM insert / update / refresh."""
        seeded = _media(test_db, title="Seeded before hiding")
        _candidate(test_db, seeded)
        bind = test_db.get_bind()

        def _rename(src: str, dst: str) -> None:
            with bind.begin() as conn:
                for table in ("media", "media_candidates"):
                    conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {src} TO {dst}"))

        test_db.commit()
        _rename("search_vector", "search_vector_hidden_590")
        try:
            for url in (
                "/api/v1/media-library/media",
                "/api/v1/media-library/candidates",
                "/api/v1/public-media",
                "/api/v1/public-media/stats",
                f"/api/v1/public-media/{seeded.id}",
            ):
                response = test_client.get(url)
                assert response.status_code == 200, (url, response.text)

            m = _media(test_db, title="Inserted while hidden")
            c = _candidate(test_db, m, description="Inserted while hidden")
            m.caption = "updated"
            c.review_notes = "updated"
            test_db.commit()
            test_db.refresh(m)
            test_db.refresh(c)
            assert m.caption == "updated"
        finally:
            test_db.rollback()
            _rename("search_vector_hidden_590", "search_vector")

    def test_orm_insert_never_writes_search_vector(self, test_db):
        statements: list[str] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        bind = test_db.get_bind()
        event.listen(bind, "before_cursor_execute", _capture)
        try:
            m = _media(test_db, title="Insert guard")
            _candidate(test_db, m, description="Insert guard")
        finally:
            event.remove(bind, "before_cursor_execute", _capture)

        inserts = [s for s in statements if s.lstrip().upper().startswith("INSERT")]
        assert inserts
        assert not any("search_vector" in s for s in inserts)

    def test_migration_sql_matches_models(self):
        path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0041_media_search.py"
        spec = importlib.util.spec_from_file_location("migration_0041", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def norm(s: str) -> str:
            return " ".join(s.split())

        assert [norm(s) for s in module._PREREQUISITES] == [
            norm(s) for s in search_sql.SEARCH_PREREQUISITES_SQL
        ]
        assert norm(module._MEDIA_VECTOR) == norm(search_sql.MEDIA_SEARCH_VECTOR_SQL)
        assert norm(module._CANDIDATE_VECTOR) == norm(search_sql.CANDIDATE_SEARCH_VECTOR_SQL)
        assert module.revision == "0041_media_search"
        assert module.down_revision == "0040_article_narration_job"
