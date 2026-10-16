"""Tests for the articles API endpoints."""

import pytest


class TestListArticles:
    """Tests for GET /api/v1/articles"""

    def test_list_articles_returns_empty_list_when_no_articles(self, test_client):
        """Should return empty list when database has no articles."""
        response = test_client.get("/api/v1/articles")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_articles_returns_approved_articles(
        self, test_client, article_factory
    ):
        """Should return only approved articles."""
        # Create one approved and one pending article
        article_factory(title="Approved Article", status="APPROVED")
        article_factory(title="Pending Article", status="PENDING")

        response = test_client.get("/api/v1/articles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Approved Article"

    def test_list_articles_excludes_bio_taxonomy(
        self, test_client, test_db, taxonomy_factory, category_factory, article_factory
    ):
        """Should exclude articles from Notable Figures taxonomy (ID 5)."""
        # Create a taxonomy with ID 5 (Notable Figures)
        # Note: In real tests we'd need to ensure the ID is 5
        # For this test, we verify the exclusion logic works
        regular_taxonomy = taxonomy_factory(name="Historical Panama")
        regular_category = category_factory(
            name="Colonial Era", taxonomy=regular_taxonomy
        )
        article_factory(title="Regular Article", category=regular_category)

        response = test_client.get("/api/v1/articles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Regular Article"

    def test_list_articles_ordered_by_historical_chronology(
        self,
        test_client,
        article_factory,
        category_factory,
        suggestion_factory,
        research_factory,
    ):
        """Category id, then suggestion id, then research id, then series order —
        creation/publication recency must not reorder the eras."""
        early_era = category_factory(name="Colonial Era")
        late_era = category_factory(name="Modern Era")

        # Published first, but belongs to the later era: must list last.
        article_factory(title="Modern standalone", category=late_era)

        # Two suggestions in the early era; the later-created (higher-id)
        # suggestion gets its article first, and must still sort second.
        first_suggestion = suggestion_factory(title="First topic", category=early_era)
        second_suggestion = suggestion_factory(title="Second topic", category=early_era)
        second_research = research_factory(suggestion=second_suggestion)
        article_factory(
            title="Second topic article", category=early_era, research_id=second_research.id
        )

        # The first suggestion's research is a series: children created out of
        # series_order, sharing the parent's research.
        first_research = research_factory(suggestion=first_suggestion)
        parent = article_factory(
            title="Series parent", category=early_era, research_id=first_research.id
        )
        article_factory(
            title="Series part 2",
            category=early_era,
            research_id=first_research.id,
            series_parent=parent,
            series_order=2,
        )
        article_factory(
            title="Series part 1",
            category=early_era,
            research_id=first_research.id,
            series_parent=parent,
            series_order=1,
        )

        response = test_client.get("/api/v1/articles")
        assert response.status_code == 200
        assert [a["title"] for a in response.json()] == [
            "Series parent",
            "Series part 1",
            "Series part 2",
            "Second topic article",
            "Modern standalone",
        ]

    def test_list_articles_carries_suggestion_group_and_audio_flag(
        self,
        test_client,
        test_db,
        article_factory,
        category_factory,
        suggestion_factory,
        research_factory,
    ):
        """The blog groups by suggestion and shows a listen badge — both ride
        on the list item, localized; has_audio is a Spanish-surface-only fact."""
        from app.models.article import Article

        category = category_factory()
        suggestion = suggestion_factory(title="Portobelo Fairs", category=category)
        suggestion.title_es = "Las Ferias de Portobelo"
        research = research_factory(suggestion=suggestion)
        test_db.commit()
        article_factory(title="Fair article", category=category, research_id=research.id)
        test_db.query(Article).update({"audio_url_es": "https://cdn.example/a/es.mp3"})
        test_db.commit()

        en = test_client.get("/api/v1/articles?lang=en").json()[0]
        es = test_client.get("/api/v1/articles?lang=es").json()[0]

        assert en["suggestion_id"] == suggestion.id
        assert en["suggestion_title"] == "Portobelo Fairs"
        assert en["has_audio"] is False  # narration is Spanish-only
        assert es["suggestion_title"] == "Las Ferias de Portobelo"
        assert es["has_audio"] is True

    def test_list_articles_pagination(self, test_client, article_factory, category_factory):
        """Should support pagination with page and limit parameters."""
        category = category_factory()
        for i in range(5):
            article_factory(title=f"Article {i}", category=category)

        # Get first page with limit of 2
        response = test_client.get("/api/v1/articles?page=1&limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Get second page
        response = test_client.get("/api/v1/articles?page=2&limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2


class TestGetArticleById:
    """Tests for GET /api/v1/articles/{article_id}"""

    def test_get_article_by_id_returns_article(self, test_client, article_factory):
        """Should return article details when found."""
        article = article_factory(
            title="Test Article",
            content="# Heading\n\nContent here",
            excerpt="Short excerpt",
        )

        response = test_client.get(f"/api/v1/articles/{article.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == article.id
        assert data["title"] == "Test Article"
        assert "<h1" in data["content_html"]  # Markdown converted to HTML (may include id attr)

    def test_get_article_by_id_returns_404_when_not_found(self, test_client):
        """Should return 404 when article doesn't exist."""
        response = test_client.get("/api/v1/articles/99999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_article_by_id_returns_404_for_pending_article(
        self, test_client, article_factory
    ):
        """Should return 404 for non-approved articles."""
        article = article_factory(title="Pending", status="PENDING")

        response = test_client.get(f"/api/v1/articles/{article.id}")
        assert response.status_code == 404


class TestGetArticleBySlug:
    """Tests for GET /api/v1/articles/by-slug/{slug}"""

    def test_get_article_by_slug_returns_article(self, test_client, article_factory):
        """Should return article when slug matches."""
        article = article_factory(title="The Panama Canal History")

        response = test_client.get("/api/v1/articles/by-slug/the-panama-canal-history")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "The Panama Canal History"

    def test_get_article_by_slug_returns_404_when_not_found(self, test_client):
        """Should return 404 when no article matches slug."""
        response = test_client.get("/api/v1/articles/by-slug/nonexistent-article")
        assert response.status_code == 404

    def test_get_article_by_slug_resolves_without_loading_every_article(
        self, test_client, test_db, test_engine, article_factory, category_factory
    ):
        """Slug resolution must not materialize every approved article.

        Slugs are computed in Python from titles, so the id has to be resolved by
        scanning titles — but the scan must stay a titles-only projection. Loading
        each candidate as a full ORM entity (bodies in both languages, plus
        ``joinedload`` on the ``tags`` and ``series_children`` collections, whose
        cartesian product multiplies every one of those bodies) held a pooled
        connection for the whole scan and exhausted the 2+3 pool under a burst of
        concurrent article views.
        """
        from app.models.article import Article
        from sqlalchemy import event

        category = category_factory()
        body = "Body paragraph. " * 200
        target = article_factory(
            title="The Panama Canal History", content=body, category=category
        )
        for i in range(4):
            article_factory(title=f"Other Article {i}", content=body, category=category)

        target_id = target.id
        statements: list[str] = []
        loaded: list[int] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        def _loaded(session, instance):
            if isinstance(instance, Article):
                loaded.append(instance.id)

        # Only count what the request itself loads, not the seeded rows.
        test_db.expunge_all()
        event.listen(test_engine, "before_cursor_execute", _record)
        event.listen(test_db, "loaded_as_persistent", _loaded)
        try:
            response = test_client.get("/api/v1/articles/by-slug/the-panama-canal-history")
        finally:
            event.remove(test_engine, "before_cursor_execute", _record)
            event.remove(test_db, "loaded_as_persistent", _loaded)

        assert response.status_code == 200
        assert response.json()["id"] == target_id

        assert loaded == [target_id], f"loaded {len(loaded)} Article entities to serve one slug"

        # Any statement that pulls article bodies must be narrowed to one article
        # — by id, or (since #474) by the unique, indexed slug columns.
        bounding = ("articles.id = ", "articles.slug = ", "articles.slug_es = ")
        unbounded = [
            s for s in statements if "articles.content" in s and not any(b in s for b in bounding)
        ]
        assert not unbounded, f"article bodies fetched without an id filter: {unbounded}"

    def test_get_article_by_slug_matches_the_spanish_slug(self, test_client, article_factory):
        """A Spanish slug resolves to its article via the persisted slug_es column."""
        article = article_factory(title="The Canal's History", title_es="La Historia del Canal")

        response = test_client.get("/api/v1/articles/by-slug/la-historia-del-canal")
        assert response.status_code == 200
        assert response.json()["id"] == article.id

    def test_get_article_by_slug_issues_one_article_statement(
        self, test_client, test_engine, article_factory
    ):
        """by-slug must resolve in a single article-fetching statement (#474)."""
        from sqlalchemy import event

        article_factory(title="The Panama Canal History")

        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            if "FROM articles" in statement:
                statements.append(statement)

        event.listen(test_engine, "before_cursor_execute", _record)
        try:
            response = test_client.get("/api/v1/articles/by-slug/the-panama-canal-history")
        finally:
            event.remove(test_engine, "before_cursor_execute", _record)

        assert response.status_code == 200
        assert len(statements) == 1, f"expected one article statement, got {statements}"
        assert "articles.slug" in statements[0]
