"""Tests for the public media API endpoints.

GET /api/v1/public-media
GET /api/v1/public-media/stats
GET /api/v1/public-media/tags
GET /api/v1/public-media/{media_id}
GET /api/v1/public-media/{media_id}/download

Public filter applied everywhere:
  - MediaCandidate.status == "APPROVED"
  - MediaCandidate.relevance_score >= 0.7
  - Media.id NOT IN (article feature_image_ids)
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from app.models.media import Media
from app.models.media_candidate import MediaCandidate


@pytest.fixture(autouse=True)
def _bypass_educator_gate(test_client):
    """Bypass the educator access gate for the query-logic tests below.

    These tests exercise the public-media filtering/pagination/download logic,
    not authentication — the gate (require_educator_access) is covered
    end-to-end in test_educators_api.py::TestPublicMediaGate. Overriding the
    dependency here keeps these focused on the query behavior. Depends on
    test_client so it is torn down before test_client clears overrides.
    """
    from app.api.dependencies import require_educator_access
    from app.main import app

    app.dependency_overrides[require_educator_access] = lambda: None
    yield
    app.dependency_overrides.pop(require_educator_access, None)


def _make_media(test_db, **overrides):
    """Insert a Media row with sensible defaults.

    Required (non-nullable) fields per app/models/media.py: filename,
    original_filename, file_path, file_size, mime_type, media_type, source.
    """
    counter = overrides.pop("_n", 0)
    defaults = {
        "filename": f"img_{counter}.jpg",
        "original_filename": f"orig_{counter}.jpg",
        "file_path": f"https://example.com/img_{counter}.jpg",
        "thumbnail_path": f"https://example.com/thumb_{counter}.jpg",
        "file_size": 12345,
        "mime_type": "image/jpeg",
        "media_type": "IMAGE",
        "source": "WIKIMEDIA",
        "title": f"Image {counter}",
        "alt_text": f"alt text {counter}",
        "search_terms": f"panama canal photo {counter}",
        "attribution": "Test attribution",
        "license": "PD-old-100",
        "license_url": "https://example.com/license",
        "source_url": "https://example.com/source",
        "width": 800,
        "height": 600,
    }
    defaults.update(overrides)
    media = Media(**defaults)
    test_db.add(media)
    test_db.commit()
    test_db.refresh(media)
    return media


def _make_candidate(test_db, media, **overrides):
    """Insert a MediaCandidate row linked to the given media."""
    counter = overrides.pop("_n", media.id)
    defaults = {
        "source": "WIKIMEDIA",
        "external_id": f"File:Test_{counter}.jpg",
        "external_url": f"https://example.com/ext_{counter}.jpg",
        "thumbnail_url": f"https://example.com/thumb_{counter}.jpg",
        "title": f"Candidate {counter}",
        "description": f"Description {counter}",
        "author": "Test Author",
        "tags": None,
        "license": "PD-old-100",
        "width": 800,
        "height": 600,
        "mime_type": "image/jpeg",
        "file_size": 12345,
        "status": "APPROVED",
        "relevance_score": 0.9,
        "media_id": media.id,
        "crawled_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    cand = MediaCandidate(**defaults)
    test_db.add(cand)
    test_db.commit()
    test_db.refresh(cand)
    return cand


def _seed_qualifying(test_db, n=1, *, relevance=0.9, status="APPROVED", tags=None):
    """Create n qualifying Media+MediaCandidate pairs and return the list."""
    rows = []
    for i in range(n):
        media = _make_media(test_db, _n=i)
        cand = _make_candidate(
            test_db,
            media,
            _n=i,
            status=status,
            relevance_score=relevance,
            tags=tags,
        )
        rows.append((media, cand))
    return rows


class TestListPublicMedia:
    """Tests for GET /api/v1/public-media."""

    def test_list_empty(self, test_client):
        """No data returns total=0, items=[], with default page/page_size."""
        response = test_client.get("/api/v1/public-media")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1
        assert data["page_size"] == 30

    def test_list_excludes_pending(self, test_client, test_db):
        """PENDING candidates must be excluded; APPROVED returned."""
        m1 = _make_media(test_db, _n=1)
        _make_candidate(test_db, m1, status="PENDING", relevance_score=0.9)
        m2 = _make_media(test_db, _n=2)
        _make_candidate(test_db, m2, status="APPROVED", relevance_score=0.9)

        response = test_client.get("/api/v1/public-media")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == m2.id

    def test_list_excludes_low_relevance(self, test_client, test_db):
        """relevance_score < 0.7 must be excluded."""
        m1 = _make_media(test_db, _n=1)
        _make_candidate(test_db, m1, status="APPROVED", relevance_score=0.6)
        m2 = _make_media(test_db, _n=2)
        _make_candidate(test_db, m2, status="APPROVED", relevance_score=0.9)

        response = test_client.get("/api/v1/public-media")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == m2.id

    def test_list_excludes_feature_images(self, test_client, test_db, article_factory):
        """Media used as Article.feature_image_id must be excluded."""
        media = _make_media(test_db, _n=1)
        _make_candidate(test_db, media, status="APPROVED", relevance_score=0.9)

        # Wire this media as an article's feature image
        article = article_factory(title="An Article")
        article.feature_image_id = media.id
        test_db.commit()

        response = test_client.get("/api/v1/public-media")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_list_search_matches_search_terms(self, test_client, test_db):
        """?search=X does ILIKE on Media.search_terms."""
        m1 = _make_media(test_db, _n=1, search_terms="canal locks engineering")
        _make_candidate(test_db, m1, status="APPROVED", relevance_score=0.9)
        m2 = _make_media(test_db, _n=2, search_terms="rainforest birds")
        _make_candidate(test_db, m2, status="APPROVED", relevance_score=0.9)

        response = test_client.get("/api/v1/public-media?search=locks")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == m1.id

    def test_list_tag_filter(self, test_client, test_db):
        """?tag=X filters MediaCandidate.tags array."""
        m1 = _make_media(test_db, _n=1)
        _make_candidate(
            test_db,
            m1,
            status="APPROVED",
            relevance_score=0.9,
            tags=["historical"],
        )
        m2 = _make_media(test_db, _n=2)
        _make_candidate(
            test_db,
            m2,
            status="APPROVED",
            relevance_score=0.9,
            tags=["modern"],
        )

        response = test_client.get("/api/v1/public-media?tag=historical")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == m1.id

    def test_list_pagination(self, test_client, test_db):
        """?page + ?page_size paginates results."""
        _seed_qualifying(test_db, n=5)

        response = test_client.get("/api/v1/public-media?page=1&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2

        response = test_client.get("/api/v1/public-media?page=3&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["total"] == 5

    def test_list_page_size_out_of_range_returns_422(self, test_client):
        """page_size > 60 fails validation."""
        response = test_client.get("/api/v1/public-media?page_size=100")
        assert response.status_code == 422

    def test_list_page_zero_returns_422(self, test_client):
        """page < 1 fails validation."""
        response = test_client.get("/api/v1/public-media?page=0")
        assert response.status_code == 422

    def test_list_orders_by_relevance_desc(self, test_client, test_db):
        """Higher relevance_score comes first."""
        m_low = _make_media(test_db, _n=1)
        _make_candidate(test_db, m_low, status="APPROVED", relevance_score=0.8)
        m_high = _make_media(test_db, _n=2)
        _make_candidate(test_db, m_high, status="APPROVED", relevance_score=0.95)

        response = test_client.get("/api/v1/public-media")
        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["id"] == m_high.id
        assert items[1]["id"] == m_low.id


class TestPublicMediaStats:
    """Tests for GET /api/v1/public-media/stats."""

    def test_stats_empty(self, test_client):
        """Empty DB returns total=0."""
        response = test_client.get("/api/v1/public-media/stats")
        assert response.status_code == 200
        assert response.json() == {"total": 0}

    def test_stats_counts_only_qualifying(self, test_client, test_db):
        """Stats reflect the same public filter as the list endpoint."""
        m1 = _make_media(test_db, _n=1)
        _make_candidate(test_db, m1, status="PENDING", relevance_score=0.9)
        m2 = _make_media(test_db, _n=2)
        _make_candidate(test_db, m2, status="APPROVED", relevance_score=0.9)
        m3 = _make_media(test_db, _n=3)
        _make_candidate(test_db, m3, status="APPROVED", relevance_score=0.9)

        response = test_client.get("/api/v1/public-media/stats")
        assert response.status_code == 200
        assert response.json() == {"total": 2}


class TestPublicMediaTags:
    """Tests for GET /api/v1/public-media/tags."""

    def test_tags_empty(self, test_client):
        """Empty DB returns tags=[]."""
        response = test_client.get("/api/v1/public-media/tags")
        assert response.status_code == 200
        assert response.json() == {"tags": []}

    def test_tags_returns_distinct_sorted(self, test_client, test_db):
        """Distinct tags returned, alphabetically sorted."""
        m1 = _make_media(test_db, _n=1)
        _make_candidate(
            test_db,
            m1,
            status="APPROVED",
            relevance_score=0.9,
            tags=["zebra", "alpha"],
        )
        m2 = _make_media(test_db, _n=2)
        _make_candidate(
            test_db,
            m2,
            status="APPROVED",
            relevance_score=0.9,
            tags=["alpha", "mid"],
        )

        response = test_client.get("/api/v1/public-media/tags")
        assert response.status_code == 200
        assert response.json() == {"tags": ["alpha", "mid", "zebra"]}

    def test_tags_excludes_non_qualifying(self, test_client, test_db):
        """Tags from PENDING / low-relevance candidates are excluded."""
        m1 = _make_media(test_db, _n=1)
        _make_candidate(
            test_db,
            m1,
            status="PENDING",
            relevance_score=0.9,
            tags=["pending-tag"],
        )
        m2 = _make_media(test_db, _n=2)
        _make_candidate(
            test_db,
            m2,
            status="APPROVED",
            relevance_score=0.9,
            tags=["approved-tag"],
        )

        response = test_client.get("/api/v1/public-media/tags")
        assert response.status_code == 200
        tags = response.json()["tags"]
        assert "pending-tag" not in tags
        assert "approved-tag" in tags


class TestGetPublicMediaDetail:
    """Tests for GET /api/v1/public-media/{media_id}."""

    def test_get_detail_found(self, test_client, test_db):
        """Qualifying media returns full PublicMediaDetail incl. search_terms."""
        media = _make_media(test_db, _n=1, search_terms="canal photo 1900")
        _make_candidate(test_db, media, status="APPROVED", relevance_score=0.9)

        response = test_client.get(f"/api/v1/public-media/{media.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == media.id
        assert data["search_terms"] == "canal photo 1900"

    def test_get_detail_not_found(self, test_client):
        """Unknown id returns 404."""
        response = test_client.get("/api/v1/public-media/99999")
        assert response.status_code == 404
        assert response.json()["detail"] == "Media not found"

    def test_get_detail_excludes_pending(self, test_client, test_db):
        """PENDING candidates do not bypass the public filter."""
        media = _make_media(test_db, _n=1)
        _make_candidate(test_db, media, status="PENDING", relevance_score=0.9)

        response = test_client.get(f"/api/v1/public-media/{media.id}")
        assert response.status_code == 404


class TestDownloadPublicMedia:
    """Tests for GET /api/v1/public-media/{media_id}/download."""

    def test_download_not_found(self, test_client):
        """Unknown id returns 404."""
        response = test_client.get("/api/v1/public-media/99999/download")
        assert response.status_code == 404

    def test_download_excludes_pending(self, test_client, test_db):
        """PENDING candidates do not allow download."""
        media = _make_media(test_db, _n=1)
        _make_candidate(test_db, media, status="PENDING", relevance_score=0.9)

        response = test_client.get(f"/api/v1/public-media/{media.id}/download")
        assert response.status_code == 404

    @patch("app.api.public_media.httpx.Client")
    def test_download_success(self, mock_client_class, test_client, test_db):
        """Successful upstream fetch streams bytes with Content-Disposition."""
        media = _make_media(test_db, _n=1, file_path="https://example.com/img.jpg")
        _make_candidate(test_db, media, status="APPROVED", relevance_score=0.9)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fakebytes"

        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response

        mock_client_class.return_value.__enter__.return_value = mock_instance

        response = test_client.get(f"/api/v1/public-media/{media.id}/download")
        assert response.status_code == 200
        assert 'attachment; filename="img.jpg"' in response.headers["content-disposition"]
        assert response.content == b"fakebytes"

    @patch("app.api.public_media.httpx.Client")
    def test_download_upstream_502(self, mock_client_class, test_client, test_db):
        """Non-200 upstream response surfaces as 502."""
        media = _make_media(test_db, _n=1, file_path="https://example.com/img.jpg")
        _make_candidate(test_db, media, status="APPROVED", relevance_score=0.9)

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response

        mock_client_class.return_value.__enter__.return_value = mock_instance

        response = test_client.get(f"/api/v1/public-media/{media.id}/download")
        assert response.status_code == 502
        assert "Failed to fetch file" in response.json()["detail"]

    # NOTE: a ``test_download_missing_file_path`` scenario was specified in the
    # brief but ``Media.file_path`` is declared ``nullable=False`` in
    # ``app/models/media.py:45`` — inserting ``file_path=None`` raises a
    # NOT NULL violation on commit, so the branch at public_media.py:183 is
    # structurally unreachable from the public API. Skipped per the brief's
    # "do not invent a workaround" guidance; flagged as shape-#3 in the PR
    # description.
