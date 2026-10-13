"""Tests for the search API endpoint (GET /api/v1/search)."""

from app.models.category import Category, Taxonomy


class TestSearchArticles:
    """Tests for GET /api/v1/search."""

    def test_search_empty_db_returns_empty_list(self, test_client):
        """No articles in DB should return [] for any query."""
        response = test_client.get("/api/v1/search?q=panama")
        assert response.status_code == 200
        assert response.json() == []

    def test_search_short_query_returns_422(self, test_client):
        """Single-character q should fail validation (min_length=2)."""
        response = test_client.get("/api/v1/search?q=a")
        assert response.status_code == 422

    def test_search_missing_query_returns_422(self, test_client):
        """Missing q parameter should fail validation."""
        response = test_client.get("/api/v1/search")
        assert response.status_code == 422

    def test_search_matches_title(self, test_client, category_factory, article_factory):
        """ILIKE match on Article.title should return the article."""
        cat = category_factory()
        article_factory(title="Panama Canal History", content="Some body", category=cat)

        response = test_client.get("/api/v1/search?q=Canal")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Panama Canal History"
        assert isinstance(data[0]["highlight"], str)
        # title match -> body doesn't contain query, but content is "Some body"
        # so highlight falls back to first N chars of content
        assert data[0]["highlight"] != ""

    def test_search_matches_content(self, test_client, category_factory, article_factory):
        """ILIKE match on Article.content should return the article."""
        cat = category_factory()
        article_factory(title="Other Title", content="The story of Canal Zone", category=cat)

        response = test_client.get("/api/v1/search?q=Canal")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Other Title"

    def test_search_matches_excerpt(self, test_client, category_factory, article_factory):
        """ILIKE match on Article.excerpt should return the article."""
        cat = category_factory()
        article_factory(
            title="Unrelated Title",
            content="Generic body text",
            excerpt="A Canal essay",
            category=cat,
        )

        response = test_client.get("/api/v1/search?q=Canal")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Unrelated Title"

    def test_search_excludes_non_approved(self, test_client, category_factory, article_factory):
        """Only APPROVED articles should be returned."""
        cat = category_factory()
        article_factory(title="Approved Canal", status="APPROVED", category=cat)
        article_factory(title="Pending Canal", status="PENDING", category=cat)

        response = test_client.get("/api/v1/search?q=Canal")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Approved Canal"

    def test_search_excludes_taxonomy_5(self, test_client, test_db, article_factory):
        """Articles in Notable Figures taxonomy (id=5) should be excluded."""
        # Raw insert because taxonomy_factory doesn't expose `id`.
        tax = Taxonomy(id=5, name="Notable Figures", description="Notable figures of Panama")
        test_db.add(tax)
        test_db.commit()
        test_db.refresh(tax)
        cat = Category(name="Biographies", taxonomy_id=tax.id, description="Biographies")
        test_db.add(cat)
        test_db.commit()
        test_db.refresh(cat)

        article_factory(title="Bio of Canal Hero", category=cat)

        response = test_client.get("/api/v1/search?q=Canal")
        assert response.status_code == 200
        assert response.json() == []

    def test_search_title_match_ranked_first(self, test_client, category_factory, article_factory):
        """Title matches should be ordered before content-only matches."""
        cat = category_factory()
        article_factory(
            title="Content Only",
            content="The Canal was built",
            category=cat,
        )
        article_factory(
            title="Canal in Title",
            content="Nothing relevant here",
            category=cat,
        )

        response = test_client.get("/api/v1/search?q=Canal")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Title match comes first per the order_by case expression
        assert data[0]["title"] == "Canal in Title"
        assert data[1]["title"] == "Content Only"

    def test_search_respects_limit(self, test_client, category_factory, article_factory):
        """The limit query param should cap result count."""
        cat = category_factory()
        for i in range(5):
            article_factory(title=f"Canal Article {i}", category=cat)

        response = test_client.get("/api/v1/search?q=Canal&limit=2")
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_search_limit_out_of_range_returns_422(self, test_client):
        """limit > 50 should fail validation."""
        response = test_client.get("/api/v1/search?q=panama&limit=100")
        assert response.status_code == 422
