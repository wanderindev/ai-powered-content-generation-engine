"""Persisted article slug columns and their edit policy (#474)."""

import pytest

from app.models.article import Article
from app.services.article_slugs import SlugCollisionError, sync_article_slugs

UPDATE_URL = "/api/v1/admin/dashboard/articles/{article_id}"


def _update_body(**overrides):
    body = {
        "title": "Some Title",
        "excerpt": "Excerpt",
        "content_html": "<p>Body</p>",
        "title_es": None,
        "excerpt_es": None,
        "content_html_es": None,
        "tag_ids": [],
    }
    body.update(overrides)
    return body


class TestInsertDefaults:
    """A plain ``Article(...)`` construction still gets both slugs."""

    def test_slugs_are_derived_on_insert(self, article_factory):
        article = article_factory(
            title="The Panama Canal History", title_es="La Historia del Canal"
        )
        assert article.slug == "the-panama-canal-history"
        assert article.slug_es == "la-historia-del-canal"

    def test_slug_es_is_null_without_a_spanish_title(self, article_factory):
        article = article_factory(title="No Spanish Yet")
        assert article.slug == "no-spanish-yet"
        assert article.slug_es is None


class TestSyncArticleSlugs:
    def test_regenerates_while_unpublished(self, test_db, article_factory):
        article = article_factory(title="Old Title", status="PENDING", published_at=None)

        article.title = "Brand New Title"
        article.title_es = "Titulo Nuevo"
        sync_article_slugs(test_db, article)
        test_db.commit()

        assert article.slug == "brand-new-title"
        assert article.slug_es == "titulo-nuevo"

    def test_frozen_once_approved(self, test_db, article_factory):
        article = article_factory(title="Live Article", status="APPROVED")
        original = article.slug

        article.title = "Retitled After Going Live"
        sync_article_slugs(test_db, article)
        test_db.commit()

        assert article.slug == original

    def test_frozen_once_published_even_if_not_approved(self, test_db, article_factory):
        article = article_factory(title="Published Draft", status="PENDING")
        original = article.slug

        article.title = "Something Else Entirely"
        sync_article_slugs(test_db, article)

        assert article.slug == original

    def test_collision_with_another_article_raises(self, test_db, article_factory):
        article_factory(title="The Panama Canal History")
        other = article_factory(title="Other Article", status="PENDING", published_at=None)

        other.title = "The Panama Canal History"
        with pytest.raises(SlugCollisionError) as exc:
            sync_article_slugs(test_db, other)

        assert "the-panama-canal-history" in str(exc.value)

    def test_cross_language_collision_raises(self, test_db, article_factory):
        article_factory(title="La Historia del Canal")
        other = article_factory(title="Other Article", status="PENDING", published_at=None)

        other.title_es = "La Historia del Canal"
        with pytest.raises(SlugCollisionError):
            sync_article_slugs(test_db, other)

    def test_same_article_may_share_slug_across_languages(self, test_db, article_factory):
        article = article_factory(title="Panama 1903", status="PENDING", published_at=None)

        article.title_es = "Panama 1903"
        sync_article_slugs(test_db, article)
        test_db.commit()

        assert article.slug == article.slug_es == "panama-1903"

    def test_unsluggable_title_raises(self, test_db, article_factory):
        article = article_factory(title="Fine Title", status="PENDING", published_at=None)

        article.title = "!!! ???"
        with pytest.raises(SlugCollisionError):
            sync_article_slugs(test_db, article)


class TestUpdateArticleEndpoint:
    def test_edit_regenerates_slug_while_unpublished(
        self, test_client, test_db, admin_token, article_factory
    ):
        article = article_factory(title="Draft Title", status="PENDING", published_at=None)

        response = test_client.put(
            UPDATE_URL.format(article_id=article.id),
            json=_update_body(title="A Completely New Draft Title"),
        )

        assert response.status_code == 200
        test_db.expire_all()
        assert test_db.get(Article, article.id).slug == "a-completely-new-draft-title"

    def test_edit_does_not_move_a_live_slug(
        self, test_client, test_db, admin_token, article_factory
    ):
        article = article_factory(title="Live Title", status="APPROVED")

        response = test_client.put(
            UPDATE_URL.format(article_id=article.id),
            json=_update_body(title="Renamed After Publication"),
        )

        assert response.status_code == 200
        test_db.expire_all()
        assert test_db.get(Article, article.id).slug == "live-title"

    def test_colliding_english_title_is_rejected(self, test_client, admin_token, article_factory):
        article_factory(title="The Panama Canal History")
        draft = article_factory(title="Draft", status="PENDING", published_at=None)

        response = test_client.put(
            UPDATE_URL.format(article_id=draft.id),
            json=_update_body(title="The Panama Canal History"),
        )

        assert response.status_code == 400
        assert "the-panama-canal-history" in response.json()["detail"]

    def test_colliding_spanish_title_is_rejected(self, test_client, admin_token, article_factory):
        article_factory(title="Uno", title_es="La Historia del Canal")
        draft = article_factory(title="Draft", status="PENDING", published_at=None)

        response = test_client.put(
            UPDATE_URL.format(article_id=draft.id),
            json=_update_body(title="Draft", title_es="La Historia del Canal"),
        )

        assert response.status_code == 400
        assert "la-historia-del-canal" in response.json()["detail"]

    def test_spanish_title_colliding_with_an_english_slug_is_rejected(
        self, test_client, admin_token, article_factory
    ):
        article_factory(title="La Historia del Canal")
        draft = article_factory(title="Draft", status="PENDING", published_at=None)

        response = test_client.put(
            UPDATE_URL.format(article_id=draft.id),
            json=_update_body(title="Draft", title_es="La Historia del Canal"),
        )

        assert response.status_code == 400


class TestMigrationCollisionScan:
    """The 0029 backfill must refuse ambiguous data before adding constraints."""

    @staticmethod
    def _check(rows):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "alembic/versions/0029_article_slugs.py"
        spec = importlib.util.spec_from_file_location("migration_0029", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._check_collisions(rows)

    def test_clean_data_passes(self):
        self._check([(1, "uno", "uno-es"), (2, "dos", None), (3, "tres", "tres")])

    def test_duplicate_english_slug_names_both_articles(self):
        with pytest.raises(RuntimeError) as exc:
            self._check([(1, "same", None), (2, "same", None)])
        assert "[1, 2]" in str(exc.value)

    def test_duplicate_spanish_slug_raises(self):
        with pytest.raises(RuntimeError):
            self._check([(1, "uno", "same"), (2, "dos", "same")])

    def test_cross_column_collision_raises(self):
        with pytest.raises(RuntimeError) as exc:
            self._check([(1, "historia", None), (2, "dos", "historia")])
        assert "collides" in str(exc.value)

    def test_same_row_sharing_both_slugs_is_allowed(self):
        self._check([(1, "panama-1903", "panama-1903")])

    def test_empty_slug_raises(self):
        with pytest.raises(RuntimeError) as exc:
            self._check([(1, "", None)])
        assert "empty English slug" in str(exc.value)
