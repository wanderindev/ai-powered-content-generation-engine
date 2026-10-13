"""Tests for the categories API endpoints.

GET /api/v1/categories
GET /api/v1/categories/with-content
GET /api/v1/categories/{category_slug}
GET /api/v1/categories/taxonomies
"""

from app.models.category import Category, Taxonomy


class TestListCategories:
    """Tests for GET /api/v1/categories."""

    def test_list_empty(self, test_client):
        """No categories in DB returns []."""
        response = test_client.get("/api/v1/categories")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_returns_categories_with_counts(
        self, test_client, category_factory, article_factory
    ):
        """Each category's article_count counts only APPROVED articles."""
        cat = category_factory(name="Colonial Era")
        article_factory(title="A1", status="APPROVED", category=cat)
        article_factory(title="A2", status="APPROVED", category=cat)
        article_factory(title="A3", status="PENDING", category=cat)

        response = test_client.get("/api/v1/categories")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Colonial Era"
        assert data[0]["article_count"] == 2

    def test_list_excludes_taxonomy_5(self, test_client, test_db):
        """Categories under taxonomy id=5 (Notable Figures) must be filtered out."""
        tax = Taxonomy(id=5, name="Notable Figures", description="Notable figures of Panama")
        test_db.add(tax)
        test_db.commit()
        cat = Category(name="Biographies", taxonomy_id=tax.id, description="Biographies")
        test_db.add(cat)
        test_db.commit()

        response = test_client.get("/api/v1/categories")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_filters_by_taxonomy_id(self, test_client, taxonomy_factory, category_factory):
        """?taxonomy_id=X should return only categories in that taxonomy."""
        tax1 = taxonomy_factory(name="Tax 1")
        tax2 = taxonomy_factory(name="Tax 2")
        category_factory(name="Cat 1", taxonomy=tax1)
        category_factory(name="Cat 2", taxonomy=tax2)

        response = test_client.get(f"/api/v1/categories?taxonomy_id={tax1.id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Cat 1"


class TestListCategoriesWithContent:
    """Tests for GET /api/v1/categories/with-content."""

    def test_with_content_excludes_empty_categories(
        self, test_client, category_factory, article_factory
    ):
        """Only categories with at least one APPROVED article should appear."""
        cat1 = category_factory(name="Has Articles")
        cat2 = category_factory(name="Empty")
        article_factory(title="A", category=cat1)
        # cat2 has no articles
        _ = cat2

        response = test_client.get("/api/v1/categories/with-content")
        assert response.status_code == 200
        data = response.json()
        names = [c["name"] for c in data]
        assert names == ["Has Articles"]

    def test_with_content_excludes_pending_only_categories(
        self, test_client, category_factory, article_factory
    ):
        """A category whose only article is PENDING should be excluded."""
        cat = category_factory(name="Pending Only")
        article_factory(title="P", status="PENDING", category=cat)

        response = test_client.get("/api/v1/categories/with-content")
        assert response.status_code == 200
        assert response.json() == []


class TestGetCategoryBySlug:
    """Tests for GET /api/v1/categories/{category_slug}."""

    def test_get_by_slug_found(self, test_client, category_factory, article_factory):
        """Category name -> generate_slug match returns full payload."""
        cat = category_factory(name="Colonial Era")
        article_factory(title="A", category=cat)

        response = test_client.get("/api/v1/categories/colonial-era")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == cat.id
        assert data["name"] == "Colonial Era"
        assert data["slug"] == "colonial-era"
        assert data["taxonomy_id"] == cat.taxonomy_id
        assert "taxonomy_name" in data
        assert data["article_count"] == 1

    def test_get_by_slug_not_found(self, test_client):
        """No matching category -> 404."""
        response = test_client.get("/api/v1/categories/nonexistent-category")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_by_slug_counts_only_approved(self, test_client, category_factory, article_factory):
        """article_count must not include PENDING articles."""
        cat = category_factory(name="Modern Era")
        article_factory(title="A1", status="APPROVED", category=cat)
        article_factory(title="A2", status="APPROVED", category=cat)
        article_factory(title="A3", status="PENDING", category=cat)

        response = test_client.get("/api/v1/categories/modern-era")
        assert response.status_code == 200
        assert response.json()["article_count"] == 2


class TestListTaxonomies:
    """Tests for GET /api/v1/categories/taxonomies."""

    def test_taxonomies_endpoint_returns_taxonomies(self, test_client, taxonomy_factory):
        """
        The /taxonomies literal route is declared before /{category_slug}, so it
        is reachable and returns the list of taxonomies as {id, name, description}.
        """
        tax = taxonomy_factory(name="Historical Panama", description="History of Panama")

        response = test_client.get("/api/v1/categories/taxonomies")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        entry = data[0]
        assert set(entry.keys()) == {"id", "name", "description"}
        assert entry["id"] == tax.id
        assert entry["name"] == "Historical Panama"
        assert entry["description"] == "History of Panama"

    def test_taxonomies_endpoint_excludes_taxonomy_5(self, test_client, test_db, taxonomy_factory):
        """Taxonomy id=5 (Notable Figures) must be excluded from the response."""
        excluded = Taxonomy(id=5, name="Notable Figures", description="Notable figures of Panama")
        test_db.add(excluded)
        test_db.commit()
        normal = taxonomy_factory(name="Historical Panama")

        response = test_client.get("/api/v1/categories/taxonomies")
        assert response.status_code == 200
        names = [t["name"] for t in response.json()]
        assert "Historical Panama" in names
        assert "Notable Figures" not in names
        ids = [t["id"] for t in response.json()]
        assert normal.id in ids
        assert 5 not in ids
