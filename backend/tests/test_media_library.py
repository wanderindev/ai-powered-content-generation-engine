"""Tests for the media library — wikimedia service, LOC service, scoring, and API endpoints."""

import json
import logging
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.models.media import Media
from app.models.media_candidate import CandidateSource, CandidateStatus, MediaCandidate
from app.services.loc import LOCService, _determine_license, _extract_resource_id
from app.services.wikimedia import (
    WikimediaService,
    _clean_html,
    _make_thumbnail_url,
)


@pytest.fixture(autouse=True)
def _no_search_terms_generation():
    """No test here reaches Haiku: write-time generation (#589) is stubbed to
    "no result" unless a test overrides it."""
    with patch("app.api.media_library.try_generate_search_metadata", return_value=None) as m:
        yield m


# ── Utility function tests ──────────────────────────────────────────────


class TestCleanHtml:
    def test_strips_tags(self):
        assert _clean_html("<b>Hello</b> <i>world</i>") == "Hello world"

    def test_unescapes_entities(self):
        assert _clean_html("a &amp; b &lt; c") == "a & b < c"

    def test_normalizes_whitespace(self):
        assert _clean_html("  too   many   spaces  ") == "too many spaces"

    def test_returns_none_for_empty(self):
        assert _clean_html(None) is None
        assert _clean_html("") is None
        assert _clean_html("   ") is None


class TestMakeThumbnailUrl:
    def test_standard_jpg(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Panama_Canal.jpg"
        thumb = _make_thumbnail_url(url, 300)
        assert thumb == (
            "https://upload.wikimedia.org/wikipedia/commons/thumb"
            "/a/ab/Panama_Canal.jpg/300px-Panama_Canal.jpg"
        )

    def test_svg_returns_original(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Map.svg"
        assert _make_thumbnail_url(url) == url

    def test_non_wikimedia_returns_none(self):
        assert _make_thumbnail_url("https://example.com/photo.jpg") is None

    def test_empty_returns_none(self):
        assert _make_thumbnail_url("") is None
        assert _make_thumbnail_url(None) is None


# ── WikimediaService unit tests ─────────────────────────────────────────


class TestWikimediaServiceMetadataExtraction:
    """Test metadata extraction from API responses without making real API calls."""

    def setup_method(self):
        self.service = WikimediaService(rate_limit=0)

    def test_extract_metadata_complete(self):
        page = {
            "title": "File:Panama Canal 1913.jpg",
            "imageinfo": [
                {
                    "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Panama_Canal_1913.jpg",
                    "width": 1200,
                    "height": 800,
                    "mime": "image/jpeg",
                    "size": 150000,
                    "extmetadata": {
                        "ObjectName": {"value": "Panama Canal construction 1913"},
                        "ImageDescription": {
                            "value": "<p>Workers at the <b>Panama Canal</b> locks</p>"
                        },
                        "Artist": {"value": '<a href="/wiki/User:Foo">John Doe</a>'},
                        "License": {"value": "PD-old-100"},
                        "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/"},
                    },
                }
            ],
            "categories": [
                {"title": "Category:Panama Canal"},
                {"title": "Category:1913 in Panama"},
            ],
        }

        result = self.service._extract_metadata(page)

        assert result is not None
        assert result["source"] == "WIKIMEDIA"
        assert result["external_id"] == "File:Panama Canal 1913.jpg"
        assert result["title"] == "Panama Canal construction 1913"
        assert result["description"] == "Workers at the Panama Canal locks"
        assert result["author"] == "John Doe"
        assert result["license"] == "PD-old-100"
        assert result["width"] == 1200
        assert result["height"] == 800
        assert result["mime_type"] == "image/jpeg"
        assert result["file_size"] == 150000
        assert result["source_categories"] == ["Panama Canal", "1913 in Panama"]
        assert result["thumbnail_url"] is not None

    def test_extract_metadata_missing_imageinfo(self):
        page = {"title": "File:Missing.jpg"}
        assert self.service._extract_metadata(page) is None

    def test_extract_metadata_uses_filename_when_no_objectname(self):
        page = {
            "title": "File:SomePanamaPhoto.jpg",
            "imageinfo": [
                {
                    "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/SomePanamaPhoto.jpg",
                    "width": 640,
                    "height": 480,
                    "mime": "image/jpeg",
                    "size": 50000,
                    "extmetadata": {},
                }
            ],
        }

        result = self.service._extract_metadata(page)
        assert result is not None
        assert result["title"] == "SomePanamaPhoto.jpg"


# ── Service crawl logic tests (with mocked API) ─────────────────────────


class TestWikimediaServiceCrawl:
    def setup_method(self):
        self.service = WikimediaService(rate_limit=0)

    @patch.object(WikimediaService, "_api_request")
    def test_get_subcategories_single_level(self, mock_api):
        mock_api.return_value = {
            "query": {
                "categorymembers": [
                    {"title": "Category:Subcategory A"},
                    {"title": "Category:Subcategory B"},
                ]
            }
        }

        result = self.service.get_subcategories("History of Panama", max_depth=1)

        assert "History of Panama" in result
        assert "Subcategory A" in result
        assert "Subcategory B" in result

    @patch.object(WikimediaService, "_api_request")
    def test_get_category_files(self, mock_api):
        mock_api.return_value = {
            "query": {
                "categorymembers": [
                    {"title": "File:Photo1.jpg"},
                    {"title": "File:Photo2.jpg"},
                ]
            }
        }

        result = self.service.get_category_files("Panama Canal", limit=10)
        assert result == ["File:Photo1.jpg", "File:Photo2.jpg"]

    @patch.object(WikimediaService, "_api_request")
    def test_get_category_files_with_pagination(self, mock_api):
        mock_api.side_effect = [
            {
                "query": {"categorymembers": [{"title": "File:Photo1.jpg"}]},
                "continue": {"cmcontinue": "page2"},
            },
            {
                "query": {"categorymembers": [{"title": "File:Photo2.jpg"}]},
            },
        ]

        result = self.service.get_category_files("Panama Canal", limit=10)
        assert len(result) == 2


# ── LOC utility function tests ───────────────────────────────────────────


class TestExtractResourceId:
    def test_from_item_url(self):
        assert _extract_resource_id("/item/2007660946/") == "2007660946"

    def test_from_full_url(self):
        assert _extract_resource_id("https://www.loc.gov/item/2007660946/") == "2007660946"

    def test_empty_returns_none(self):
        assert _extract_resource_id("") is None
        assert _extract_resource_id(None) is None


class TestDetermineLicense:
    def test_no_known_restrictions(self):
        assert (
            _determine_license(["No known restrictions on publication."])
            == "pd-no-known-restrictions"
        )

    def test_public_domain(self):
        assert _determine_license("This is in the public domain") == "pd-us-gov"

    def test_copyright(self):
        assert _determine_license(["Copyright by someone"]) == "copyright-check-required"

    def test_empty_defaults_to_pd(self):
        assert _determine_license(None) == "pd-us-gov"
        assert _determine_license([]) == "pd-us-gov"


# ── LOC service unit tests ───────────────────────────────────────────────


class TestLOCServiceMetadataExtraction:
    """Test metadata extraction from LOC API responses."""

    def setup_method(self):
        self.service = LOCService(rate_limit=0)

    def test_extract_metadata_complete(self):
        result = {
            "id": "/item/2007660946/",
            "title": "Bird's eye view of the Panama Canal",
            "description": ["A panoramic photograph of the canal under construction"],
            "contributor": ["Detroit Publishing Co."],
            "url": "https://www.loc.gov/item/2007660946/",
            "image_url": [
                "https://tile.loc.gov/storage-services/service/pnp/pan/6a/small.jpg",
                "https://tile.loc.gov/storage-services/service/pnp/pan/6a/large.jpg",
            ],
            "subject": ["Panama Canal", "Construction", "Canals"],
            "rights": ["No known restrictions on publication."],
            "partof": ["Panoramic photographs"],
        }

        meta = self.service._extract_metadata(result)

        assert meta is not None
        assert meta["source"] == "LOC"
        assert meta["external_id"] == "/item/2007660946"
        assert meta["title"] == "Bird's eye view of the Panama Canal"
        assert "panoramic photograph" in meta["description"]
        assert meta["author"] == "Detroit Publishing Co."
        assert meta["page_url"] == "https://www.loc.gov/item/2007660946/"
        assert meta["license"] == "pd-no-known-restrictions"
        assert "Panama Canal" in meta["tags"]
        assert "Panoramic photographs" in meta["source_categories"]

    def test_extract_metadata_missing_id(self):
        assert self.service._extract_metadata({}) is None
        assert self.service._extract_metadata({"title": "No ID"}) is None

    def test_extract_metadata_missing_title(self):
        assert self.service._extract_metadata({"id": "/item/123/"}) is None

    def test_extract_metadata_string_description(self):
        result = {
            "id": "/item/123/",
            "title": "Test",
            "description": "A simple string description",
            "image_url": ["https://example.com/img.jpg"],
        }
        meta = self.service._extract_metadata(result)
        assert meta is not None
        assert meta["description"] == "A simple string description"

    def test_extract_metadata_no_image_uses_iiif_fallback(self):
        """Items with no image_url field should still get IIIF-based URLs from their ID."""
        result = {
            "id": "/item/2007660946/",
            "title": "Text-only item",
        }
        meta = self.service._extract_metadata(result)
        assert meta is not None
        assert "tile.loc.gov/image-services/iiif" in meta["external_url"]

    def test_extract_metadata_no_id_and_no_image_returns_none(self):
        """Items with neither ID-based IIIF nor image_url should be skipped."""
        result = {
            "id": "/item///",
            "title": "Really no image",
        }
        # No usable resource_id and no image_url → skipped.
        assert self.service._extract_metadata(result) is None
        # A truly empty case (no id at all) is likewise skipped.
        assert self.service._extract_metadata({"title": "No ID"}) is None

    def test_extract_metadata_builds_iiif_thumbnail(self):
        result = {
            "id": "/item/2007660946/",
            "title": "Test",
            "image_url": ["https://example.com/img.jpg"],
        }
        meta = self.service._extract_metadata(result)
        assert meta is not None
        assert "tile.loc.gov/image-services/iiif" in meta["thumbnail_url"]
        assert "2007660946" in meta["thumbnail_url"]


class TestLOCServiceSearch:
    def setup_method(self):
        self.service = LOCService(rate_limit=0)

    @patch.object(LOCService, "_api_request")
    def test_search_basic(self, mock_api):
        mock_api.return_value = {
            "results": [
                {"id": "/item/1/", "title": "Photo 1"},
                {"id": "/item/2/", "title": "Photo 2"},
            ],
            "pagination": {"total": 1},
        }

        results = self.service.search("panama canal", limit=10)
        assert len(results) == 2
        mock_api.assert_called_once()

    @patch.object(LOCService, "_api_request")
    def test_search_with_collection(self, mock_api):
        mock_api.return_value = {
            "results": [{"id": "/item/1/", "title": "Panoramic"}],
            "pagination": {"total": 1},
        }

        self.service.search("panama", collection="panoramic-photographs", limit=10)

        call_args = mock_api.call_args
        assert "panoramic-photographs" in call_args[0][0]

    @patch.object(LOCService, "_api_request")
    def test_search_pagination(self, mock_api):
        mock_api.side_effect = [
            {
                "results": [{"id": f"/item/{i}/", "title": f"Photo {i}"} for i in range(50)],
                "pagination": {"total": 2},
            },
            {
                "results": [{"id": f"/item/{i}/", "title": f"Photo {i}"} for i in range(50, 70)],
                "pagination": {"total": 2},
            },
        ]

        results = self.service.search("panama canal", limit=100)
        assert len(results) == 70
        assert mock_api.call_count == 2

    @patch.object(LOCService, "_api_request")
    def test_search_respects_limit(self, mock_api):
        mock_api.return_value = {
            "results": [{"id": f"/item/{i}/", "title": f"Photo {i}"} for i in range(50)],
            "pagination": {"total": 3},
        }

        results = self.service.search("panama canal", limit=20)
        assert len(results) == 20


# ── Database integration tests ───────────────────────────────────────────


class TestMediaCandidateModel:
    def test_create_candidate(self, test_db):
        candidate = MediaCandidate(
            source=CandidateSource.WIKIMEDIA.value,
            external_id="File:Test.jpg",
            external_url="https://upload.wikimedia.org/wikipedia/commons/a/ab/Test.jpg",
            title="Test Image",
            status=CandidateStatus.PENDING.value,
            crawled_at=datetime.now(UTC),
        )
        test_db.add(candidate)
        test_db.commit()
        test_db.refresh(candidate)

        assert candidate.id is not None
        assert candidate.status == "PENDING"
        assert candidate.source == "WIKIMEDIA"

    def test_unique_constraint(self, test_db):
        """Same source + external_id should be rejected."""
        from sqlalchemy.exc import IntegrityError

        c1 = MediaCandidate(
            source="WIKIMEDIA",
            external_id="File:Duplicate.jpg",
            external_url="https://example.com/1.jpg",
            title="First",
            status="PENDING",
        )
        test_db.add(c1)
        test_db.commit()

        c2 = MediaCandidate(
            source="WIKIMEDIA",
            external_id="File:Duplicate.jpg",
            external_url="https://example.com/2.jpg",
            title="Second",
            status="PENDING",
        )
        test_db.add(c2)
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()


# ── API endpoint tests ───────────────────────────────────────────────────


class TestMediaLibraryAPI:
    def _seed_candidates(self, test_db, count=5):
        """Helper to create test candidates."""
        candidates = []
        for i in range(count):
            c = MediaCandidate(
                source="WIKIMEDIA",
                external_id=f"File:Test_{i}.jpg",
                external_url=f"https://example.com/{i}.jpg",
                thumbnail_url=f"https://example.com/thumb/{i}.jpg",
                title=f"Panama Canal Photo {i}",
                description=f"A photo of the Panama Canal, image {i}",
                author="Test Author",
                license="PD-old-100",
                width=800,
                height=600,
                mime_type="image/jpeg",
                file_size=50000,
                status="PENDING",
                relevance_score=0.5 + (i * 0.1) if i < 3 else None,
                source_categories=["Panama Canal"],
                crawled_at=datetime.now(UTC),
            )
            test_db.add(c)
            candidates.append(c)
        test_db.commit()
        for c in candidates:
            test_db.refresh(c)
        return candidates

    def test_list_candidates_empty(self, test_client, admin_token):
        response = test_client.get(f"/api/v1/media-library/candidates?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_candidates_with_data(self, test_client, test_db, admin_token):
        self._seed_candidates(test_db)
        response = test_client.get(f"/api/v1/media-library/candidates?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5

    def test_list_candidates_filter_status(self, test_client, test_db, admin_token):
        candidates = self._seed_candidates(test_db, 3)
        candidates[0].status = "APPROVED"
        test_db.commit()

        response = test_client.get(
            f"/api/v1/media-library/candidates?token={admin_token}&status=APPROVED"
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_list_candidates_filter_min_score(self, test_client, test_db, admin_token):
        self._seed_candidates(test_db)
        response = test_client.get(
            f"/api/v1/media-library/candidates?token={admin_token}&min_score=0.7"
        )
        assert response.status_code == 200
        # Only candidates with score >= 0.7 (0.7 from i=2)
        assert response.json()["total"] == 1

    def test_list_candidates_search(self, test_client, test_db, admin_token):
        self._seed_candidates(test_db)
        response = test_client.get(
            f"/api/v1/media-library/candidates?token={admin_token}&search=Canal"
        )
        assert response.status_code == 200
        assert response.json()["total"] == 5

    def test_list_candidates_pagination(self, test_client, test_db, admin_token):
        self._seed_candidates(test_db, 10)
        response = test_client.get(
            f"/api/v1/media-library/candidates?token={admin_token}&page=1&page_size=3"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 10
        assert len(data["items"]) == 3
        assert data["page"] == 1
        assert data["page_size"] == 3

    def test_get_candidate_detail(self, test_client, test_db, admin_token):
        candidates = self._seed_candidates(test_db, 1)
        cid = candidates[0].id
        response = test_client.get(f"/api/v1/media-library/candidates/{cid}?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == cid
        assert data["external_url"] is not None

    def test_get_candidate_not_found(self, test_client, admin_token):
        response = test_client.get(f"/api/v1/media-library/candidates/99999?token={admin_token}")
        assert response.status_code == 404

    def test_candidates_require_admin_token(self, test_client):
        """Calls without an admin token are rejected with 401/422."""
        response = test_client.get("/api/v1/media-library/candidates")
        assert response.status_code in (401, 422)

    def test_candidates_reject_invalid_token(self, test_client):
        """Calls with an invalid token are rejected with 401."""
        response = test_client.get("/api/v1/media-library/candidates?token=not-a-real-token")
        assert response.status_code == 401

    def test_approve_candidate(self, test_client, test_db, admin_token):
        candidates = self._seed_candidates(test_db, 1)
        cid = candidates[0].id
        response = test_client.post(
            f"/api/v1/media-library/candidates/{cid}/approve?token={admin_token}",
            json={"notes": "Good image"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "APPROVED"
        assert data["review_notes"] == "Good image"
        assert data["reviewed_at"] is not None

    def test_approve_already_approved(self, test_client, test_db, admin_token):
        candidates = self._seed_candidates(test_db, 1)
        candidates[0].status = "APPROVED"
        test_db.commit()
        cid = candidates[0].id
        response = test_client.post(
            f"/api/v1/media-library/candidates/{cid}/approve?token={admin_token}",
            json={},
        )
        assert response.status_code == 400

    def test_reject_candidate(self, test_client, test_db, admin_token):
        candidates = self._seed_candidates(test_db, 1)
        cid = candidates[0].id
        response = test_client.post(
            f"/api/v1/media-library/candidates/{cid}/reject?token={admin_token}",
            json={"notes": "Not relevant"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "REJECTED"

    def test_stats_empty(self, test_client, admin_token):
        response = test_client.get(f"/api/v1/media-library/stats?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_stats_with_data(self, test_client, test_db, admin_token):
        self._seed_candidates(test_db, 5)
        response = test_client.get(f"/api/v1/media-library/stats?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["by_source"]["WIKIMEDIA"] == 5
        assert data["by_status"]["PENDING"] == 5
        assert data["scored"] == 3  # First 3 have scores in _seed_candidates
        assert data["unscored"] == 2

    @patch("app.api.media_library.run_crawl_job")
    def test_crawl_endpoint(self, mock_run, test_client, admin_token):
        """Wikimedia crawl now kicks off a background job and returns 202 + job id."""
        response = test_client.post(
            f"/api/v1/media-library/crawl?token={admin_token}",
            json={"max_depth": 1, "max_per_category": 10},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["job_type"] == "WIKIMEDIA"
        assert data["status"] == "PENDING"
        # The crawl was scheduled off the request path with the new job's id.
        mock_run.assert_called_once_with(data["id"])

    @patch("app.api.media_library.run_crawl_job")
    def test_crawl_loc_endpoint(self, mock_run, test_client, admin_token):
        """LOC crawl now kicks off a background job and returns 202 + job id."""
        response = test_client.post(
            f"/api/v1/media-library/crawl-loc?token={admin_token}",
            json={"max_per_query": 50},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["job_type"] == "LOC"
        assert data["status"] == "PENDING"
        mock_run.assert_called_once_with(data["id"])

    def test_loc_candidates_in_list(self, test_client, test_db, admin_token):
        """LOC candidates should appear in the candidates list filtered by source."""
        c = MediaCandidate(
            source="LOC",
            external_id="/item/2007660946",
            external_url="https://tile.loc.gov/image.jpg",
            title="Panama Canal panoramic",
            status="PENDING",
            crawled_at=datetime.now(UTC),
        )
        test_db.add(c)
        test_db.commit()

        response = test_client.get(
            f"/api/v1/media-library/candidates?token={admin_token}&source=LOC"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["source"] == "LOC"


class TestListMediaEndpoint:
    """GET /media-library/media — the image-picker's backend (guide/slide editors)."""

    def _seed_media(self, test_db, **overrides):
        n = overrides.pop("_n", 0)
        defaults = {
            "filename": f"img_{n}.jpg",
            "original_filename": f"orig_{n}.jpg",
            "file_path": f"https://cdn.example.com/img_{n}.jpg",
            "thumbnail_path": f"https://cdn.example.com/thumb_{n}.jpg",
            "file_size": 12345,
            "mime_type": "image/jpeg",
            "media_type": "IMAGE",
            "source": "WIKIMEDIA",
            "title": f"Cathedral Tower {n}",
            "caption": f"The stone bell tower of the old cathedral, image {n}",
            "search_terms": f"catedral campanario torre cathedral bell tower {n}",
        }
        defaults.update(overrides)
        m = Media(**defaults)
        test_db.add(m)
        test_db.commit()
        test_db.refresh(m)
        return m

    def test_items_include_thumbnail_path(self, test_client, test_db, admin_token):
        """The picker grid renders thumbnails, not multi-MB full scans."""
        self._seed_media(test_db)
        response = test_client.get(f"/api/v1/media-library/media?token={admin_token}")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["thumbnail_path"] == "https://cdn.example.com/thumb_0.jpg"
        assert item["file_path"] == "https://cdn.example.com/img_0.jpg"

    def test_search_matches_title_caption_and_search_terms(self, test_client, test_db, admin_token):
        """Search must be as discoverable as the library views: LOC/Wikimedia
        titles are terse English, so caption and the bilingual search_terms
        (what /public-media searches) have to match too."""
        self._seed_media(test_db, _n=1)  # matches all three fields
        self._seed_media(
            test_db,
            _n=2,
            title="Unrelated title",
            caption="An unrelated caption",
            search_terms="mercado market vendedores",
        )

        for term, expect_n in [
            ("Cathedral Tower", 1),  # title
            ("stone bell tower", 1),  # caption only
            ("catedral", 1),  # Spanish search_terms only
            ("mercado", 2),  # the other row's search_terms
        ]:
            response = test_client.get(
                f"/api/v1/media-library/media?token={admin_token}&search={term}"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1, term
            assert data["items"][0]["title"] == (
                "Cathedral Tower 1" if expect_n == 1 else "Unrelated title"
            )

    def test_search_no_match(self, test_client, test_db, admin_token):
        self._seed_media(test_db)
        response = test_client.get(
            f"/api/v1/media-library/media?token={admin_token}&search=zeppelin"
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_search_tokenizes_across_fields_any_order(self, test_client, test_db, admin_token):
        """Multi-word queries match word-by-word across the combined fields, not
        as one phrase — 'Casco Antiguo wall' must find an image titled 'City
        wall' whose search_terms say 'casco antiguo' (the phrase never occurs)."""
        self._seed_media(
            test_db,
            _n=1,
            title="City wall fortifications",
            caption="Defensive stone wall of the walled city",
            search_terms="casco antiguo muralla ciudad amurallada",
        )
        self._seed_media(test_db, _n=2)  # cathedral row — must not match

        for term in ["Casco Antiguo wall", "wall casco", "muralla fortifications"]:
            response = test_client.get(
                f"/api/v1/media-library/media?token={admin_token}&search={term}"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1, term
            assert data["items"][0]["title"] == "City wall fortifications"

        # OR semantics (#590): a token with no match anywhere no longer empties
        # the result — the row still ranks on the words that do match.
        response = test_client.get(
            f"/api/v1/media-library/media?token={admin_token}&search=casco zeppelin"
        )
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "City wall fortifications"


def _tiny_jpeg() -> bytes:
    """A real decodable 40×30 JPEG for the import tests."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (40, 30), "salmon").save(buf, format="JPEG")
    return buf.getvalue()


class TestImportMediaFromUrl:
    """POST /media-library/media/import-url — the picker's paste-a-web-image
    escape hatch. DO Spaces + external HTTP are mocked via ImageStorageService."""

    URL = "https://example.org/photos/Casco%20wall.jpg"

    def _mock_storage(self):
        storage = MagicMock()
        storage.download_image.return_value = (_tiny_jpeg(), "image/jpeg")
        storage.upload_image.return_value = "https://cdn.example.com/media/local/abc_Casco_wall.jpg"
        storage.generate_thumbnail.return_value = (
            "https://cdn.example.com/media/local/thumbs/abc_Casco_wall.jpg"
        )
        ctx = MagicMock()
        ctx.return_value.__enter__ = MagicMock(return_value=storage)
        ctx.return_value.__exit__ = MagicMock(return_value=False)
        return ctx, storage

    def test_import_creates_local_media_with_thumbnail(self, test_client, test_db, admin_token):
        from app.models.media import Media as MediaModel

        ctx, storage = self._mock_storage()
        with patch("app.api.media_library.ImageStorageService", ctx):
            response = test_client.post(
                f"/api/v1/media-library/media/import-url?token={admin_token}",
                json={"url": self.URL},
            )
        assert response.status_code == 201
        item = response.json()
        assert item["source"] == "LOCAL"
        assert item["file_path"].startswith("https://cdn.example.com/")
        assert item["thumbnail_path"].endswith("thumbs/abc_Casco_wall.jpg")
        assert item["width"] == 40 and item["height"] == 30

        storage.download_image.assert_called_once_with(self.URL)
        row = test_db.get(MediaModel, item["id"])
        assert row.source_url == self.URL
        assert row.media_type == "IMAGE"
        # The Spaces key embeds a URL hash + sanitized filename.
        assert row.filename.endswith("_Casco_20wall.jpg")

    def test_reimporting_same_url_returns_existing_row(self, test_client, test_db, admin_token):
        ctx, storage = self._mock_storage()
        with patch("app.api.media_library.ImageStorageService", ctx):
            first = test_client.post(
                f"/api/v1/media-library/media/import-url?token={admin_token}",
                json={"url": self.URL},
            ).json()
            second = test_client.post(
                f"/api/v1/media-library/media/import-url?token={admin_token}",
                json={"url": self.URL},
            ).json()
        assert second["id"] == first["id"]
        storage.download_image.assert_called_once()  # no second download

    def test_rejects_non_http_url(self, test_client, admin_token):
        response = test_client.post(
            f"/api/v1/media-library/media/import-url?token={admin_token}",
            json={"url": "ftp://example.org/x.jpg"},
        )
        assert response.status_code == 400

    def test_download_validation_failure_is_400(self, test_client, admin_token):
        ctx, storage = self._mock_storage()
        storage.download_image.side_effect = ValueError("Image too large: 999 bytes")
        with patch("app.api.media_library.ImageStorageService", ctx):
            response = test_client.post(
                f"/api/v1/media-library/media/import-url?token={admin_token}",
                json={"url": self.URL},
            )
        assert response.status_code == 400
        assert "too large" in response.json()["detail"]

    def test_fetch_failure_is_502(self, test_client, admin_token, caplog):
        ctx, storage = self._mock_storage()
        storage.download_image.side_effect = httpx.ConnectError("dns blocked")
        with patch("app.api.media_library.ImageStorageService", ctx):
            with caplog.at_level(logging.WARNING, logger="app.api.media_library"):
                response = test_client.post(
                    f"/api/v1/media-library/media/import-url?token={admin_token}",
                    json={"url": self.URL},
                )
        assert response.status_code == 502
        # Sentry scrubs the request body (#9), so the log line is the only record
        # of which URL failed — without it the 502 is undiagnosable.
        assert self.URL in caplog.text

    def test_imported_media_appears_in_picker_listing(self, test_client, test_db, admin_token):
        """The picker filters source=WIKIMEDIA,LOC,LOCAL — imports must show up."""
        ctx, _ = self._mock_storage()
        with patch("app.api.media_library.ImageStorageService", ctx):
            test_client.post(
                f"/api/v1/media-library/media/import-url?token={admin_token}",
                json={"url": self.URL, "title": "Casco wall"},
            )
        response = test_client.get(
            f"/api/v1/media-library/media?token={admin_token}&source=WIKIMEDIA,LOC,LOCAL"
        )
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["title"] == "Casco wall"


class TestMediaMetadataWrites:
    """#589 — metadata on link imports, PATCH /media/{id}, terms at approve."""

    URL = "https://example.org/photos/Casco%20wall.jpg"
    PAGE = "https://example.org/gallery/casco"
    IMPORT = "/api/v1/media-library/media/import-url"
    FULL = {
        "title": "Casco wall",
        "caption": "The sea wall of Casco Antiguo",
        "attribution": "Jane Doe",
        "source_page_url": "https://example.org/gallery/casco",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "keywords": "Casco, muralla",
    }

    def _import(self, test_client, body, ctx=None):
        ctx = ctx or TestImportMediaFromUrl._mock_storage(None)[0]
        with patch("app.api.media_library.ImageStorageService", ctx):
            return test_client.post(self.IMPORT, json=body)

    def _row(self, test_db, mid):
        test_db.expire_all()
        return test_db.get(Media, mid)

    def test_import_stores_every_metadata_field(self, test_client, test_db, admin_token):
        response = self._import(test_client, {"url": self.URL, **self.FULL})
        assert response.status_code == 201
        item = response.json()
        row = self._row(test_db, item["id"])
        assert row.title == "Casco wall"
        assert row.caption == "The sea wall of Casco Antiguo"
        assert row.attribution == "Jane Doe"
        assert row.license == "CC BY 4.0"
        assert row.license_url == self.FULL["license_url"]
        assert row.source_url == self.PAGE
        assert row.search_terms == "Casco, muralla"
        assert row.alt_text == "Casco wall"
        assert item["caption"] == row.caption and item["source_url"] == self.PAGE

    def test_import_merges_generated_terms_and_replaces_default_alt(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        _no_search_terms_generation.return_value = {
            "search_terms": "casco, sea wall, muralla de mar",
            "alt_text": "Stone sea wall at dusk",
        }
        item = self._import(test_client, {"url": self.URL, **self.FULL}).json()
        row = self._row(test_db, item["id"])
        assert row.search_terms == "Casco, muralla, sea wall, muralla de mar"
        assert row.alt_text == "Stone sea wall at dusk"
        sent = _no_search_terms_generation.call_args.args[0]
        assert sent.keywords == "Casco, muralla" and sent.credit == "Jane Doe"

    def test_import_preserves_operator_alt_text(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        _no_search_terms_generation.return_value = {"search_terms": "x", "alt_text": "Generated"}
        item = self._import(
            test_client, {"url": self.URL, "title": "T", "alt_text": "Operator alt"}
        ).json()
        assert self._row(test_db, item["id"]).alt_text == "Operator alt"

    def test_llm_failure_still_imports(self, test_client, test_db, admin_token):
        from app.services.media_metadata import try_generate_search_metadata

        with (
            patch(
                "app.api.media_library.try_generate_search_metadata", try_generate_search_metadata
            ),
            patch(
                "app.services.media_metadata.get_settings",
                return_value=MagicMock(anthropic_api_key="k"),
            ),
            patch(
                "app.services.media_metadata.create_message", side_effect=RuntimeError("down")
            ) as cm,
        ):
            with_kw = self._import(test_client, {"url": self.URL, "keywords": "Casco"})
            without_kw = self._import(test_client, {"url": "https://example.org/other.jpg"})
        assert cm.call_count == 2
        assert with_kw.status_code == 201 and without_kw.status_code == 201
        assert self._row(test_db, with_kw.json()["id"]).search_terms == "Casco"
        assert self._row(test_db, without_kw.json()["id"]).search_terms is None

    def test_reimport_with_source_page_dedupes_on_hashed_filename(
        self, test_client, test_db, admin_token
    ):
        ctx, storage = TestImportMediaFromUrl._mock_storage(None)
        first = self._import(test_client, {"url": self.URL, **self.FULL}, ctx).json()
        second = self._import(test_client, {"url": self.URL, "title": "Other"}, ctx).json()
        assert second["id"] == first["id"]
        assert second["title"] == "Casco wall"  # existing row returned unchanged
        storage.download_image.assert_called_once()

    @pytest.mark.parametrize("field", ["source_page_url", "license_url"])
    def test_non_http_url_field_is_422(self, test_client, admin_token, field):
        response = test_client.post(self.IMPORT, json={"url": self.URL, field: "javascript:x"})
        assert response.status_code == 422

    def _seed_media(self, test_db, **overrides):
        values = {
            "filename": "abc_x.jpg",
            "original_filename": "x.jpg",
            "file_path": "https://cdn.example.com/x.jpg",
            "file_size": 10,
            "mime_type": "image/jpeg",
            "media_type": "IMAGE",
            "source": "LOCAL",
            "title": "Old title",
            "alt_text": "Old title",
            "attribution": "Jane",
            "search_terms": "old, terms",
        }
        values.update(overrides)
        m = Media(**values)
        test_db.add(m)
        test_db.commit()
        return m.id

    def test_patch_partial_update_without_regeneration(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        mid = self._seed_media(test_db, caption="Same")
        # Only caption, unchanged value → nothing to regenerate.
        response = test_client.patch(f"/api/v1/media-library/media/{mid}", json={"caption": "Same"})
        assert response.status_code == 200
        response = test_client.patch(f"/api/v1/media-library/media/{mid}", json={"license": "PD"})
        assert response.status_code == 200
        _no_search_terms_generation.assert_not_called()
        row = self._row(test_db, mid)
        assert row.license == "PD" and row.caption == "Same"
        assert row.title == "Old title" and row.attribution == "Jane"
        assert row.search_terms == "old, terms"

    def test_patch_caption_change_leaves_other_fields_alone(
        self, test_client, test_db, admin_token
    ):
        mid = self._seed_media(test_db)
        test_client.patch(f"/api/v1/media-library/media/{mid}", json={"caption": "New cap"})
        row = self._row(test_db, mid)
        assert row.caption == "New cap" and row.title == "Old title"
        # Generation returned nothing (stub) → terms unchanged.
        assert row.attribution == "Jane" and row.search_terms == "old, terms"

    def test_patch_title_regenerates_and_replaces_default_alt(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        _no_search_terms_generation.return_value = {"search_terms": "new, terms", "alt_text": "Gen"}
        mid = self._seed_media(test_db)
        item = test_client.patch(
            f"/api/v1/media-library/media/{mid}", json={"title": "New title"}
        ).json()
        _no_search_terms_generation.assert_called_once()
        sent = _no_search_terms_generation.call_args.args[0]
        assert sent.title == "New title" and sent.keywords == "old, terms"
        assert item["search_terms"] == "new, terms"
        assert item["alt_text"] == "Gen"

    def test_patch_title_keeps_alt_text_sent_in_body(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        _no_search_terms_generation.return_value = {"search_terms": "new", "alt_text": "Gen"}
        mid = self._seed_media(test_db)
        item = test_client.patch(
            f"/api/v1/media-library/media/{mid}",
            json={"title": "New title", "alt_text": "Mine"},
        ).json()
        assert item["alt_text"] == "Mine" and item["search_terms"] == "new"

    def test_patch_title_keeps_non_default_alt(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        _no_search_terms_generation.return_value = {"search_terms": "new", "alt_text": "Gen"}
        mid = self._seed_media(test_db, alt_text="Operator alt")
        item = test_client.patch(
            f"/api/v1/media-library/media/{mid}", json={"title": "New title"}
        ).json()
        assert item["alt_text"] == "Operator alt"

    def test_patch_search_terms_verbatim_without_generation(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        mid = self._seed_media(test_db)
        item = test_client.patch(
            f"/api/v1/media-library/media/{mid}",
            json={"title": "New title", "search_terms": "hand, written"},
        ).json()
        _no_search_terms_generation.assert_not_called()
        assert item["search_terms"] == "hand, written" and item["title"] == "New title"

    def test_patch_null_clears_field(self, test_client, test_db, admin_token):
        mid = self._seed_media(test_db)
        test_client.patch(f"/api/v1/media-library/media/{mid}", json={"attribution": None})
        assert self._row(test_db, mid).attribution is None

    def test_patch_null_title_is_400_and_unknown_id_404(self, test_client, test_db, admin_token):
        mid = self._seed_media(test_db)
        response = test_client.patch(f"/api/v1/media-library/media/{mid}", json={"title": None})
        assert response.status_code == 400
        assert response.json()["detail"] == "Title cannot be empty"
        response = test_client.patch("/api/v1/media-library/media/999999", json={"caption": "x"})
        assert response.status_code == 404

    def test_approve_writes_search_terms(
        self, test_client, test_db, admin_token, _no_search_terms_generation
    ):
        _no_search_terms_generation.return_value = {
            "search_terms": "canal, Canal de Panamá",
            "alt_text": "Steam shovels in the Culebra Cut",
        }
        candidate = MediaCandidate(
            source="WIKIMEDIA",
            external_id="File:Cut.jpg",
            external_url="https://upload.wikimedia.org/x/Cut.jpg",
            title="Culebra Cut",
            description="Excavation, 1910",
            tags=["canal"],
            status="PENDING",
            crawled_at=datetime.now(UTC),
        )
        test_db.add(candidate)
        test_db.commit()
        storage = MagicMock()
        storage.download_and_upload.return_value = (
            "https://cdn.example.com/cut.jpg",
            100,
            "image/jpeg",
            "https://cdn.example.com/thumbs/cut.jpg",
        )
        ctx = MagicMock()
        ctx.return_value.__enter__ = MagicMock(return_value=storage)
        ctx.return_value.__exit__ = MagicMock(return_value=False)
        with patch("app.api.media_library.ImageStorageService", ctx):
            response = test_client.post(
                f"/api/v1/media-library/candidates/{candidate.id}/approve", json={}
            )
        assert response.status_code == 200
        row = self._row(test_db, response.json()["media_id"])
        assert row.search_terms == "canal, Canal de Panamá"
        assert row.alt_text == "Steam shovels in the Culebra Cut"
        sent = _no_search_terms_generation.call_args.args[0]
        assert sent.description == "Excavation, 1910" and sent.tags == ["canal"]


# ── Wikimedia: batched metadata fetching ─────────────────────────────────


class TestWikimediaServiceFilesMetadata:
    """Covers WikimediaService.get_files_metadata batching and per-page dropping."""

    def setup_method(self):
        self.service = WikimediaService(rate_limit=0)

    @patch.object(WikimediaService, "_api_request")
    def test_returns_metadata_for_each_page(self, mock_api):
        mock_api.return_value = {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:A.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/A.jpg",
                                "width": 100,
                                "height": 100,
                                "mime": "image/jpeg",
                                "size": 1000,
                                "extmetadata": {
                                    "ObjectName": {"value": "Photo A"},
                                },
                            }
                        ],
                    },
                    "2": {
                        "title": "File:B.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/wikipedia/commons/c/cd/B.jpg",
                                "width": 200,
                                "height": 200,
                                "mime": "image/jpeg",
                                "size": 2000,
                                "extmetadata": {
                                    "ObjectName": {"value": "Photo B"},
                                },
                            }
                        ],
                    },
                }
            }
        }

        out = self.service.get_files_metadata(["File:A.jpg", "File:B.jpg"])

        assert len(out) == 2
        assert {m["source"] for m in out} == {"WIKIMEDIA"}

    @patch.object(WikimediaService, "_api_request")
    def test_empty_titles_returns_empty_without_api_call(self, mock_api):
        out = self.service.get_files_metadata([])
        assert out == []
        mock_api.assert_not_called()

    @patch.object(WikimediaService, "_api_request")
    def test_pages_missing_imageinfo_are_silently_dropped(self, mock_api):
        mock_api.return_value = {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:Good.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Good.jpg",
                                "width": 100,
                                "height": 100,
                                "mime": "image/jpeg",
                                "size": 1000,
                                "extmetadata": {"ObjectName": {"value": "Good"}},
                            }
                        ],
                    },
                    "2": {"title": "File:Missing.jpg"},  # no imageinfo → dropped
                }
            }
        }

        out = self.service.get_files_metadata(["File:Good.jpg", "File:Missing.jpg"])
        assert len(out) == 1
        assert out[0]["external_id"] == "File:Good.jpg"


# ── Wikimedia: upsert behavior ───────────────────────────────────────────


class TestWikimediaServiceUpsert:
    """Covers WikimediaService._upsert_candidates — insert vs merge-categories."""

    def setup_method(self):
        self.service = WikimediaService(rate_limit=0)

    def _meta(self, external_id: str, categories=None):
        return {
            "source": CandidateSource.WIKIMEDIA.value,
            "external_id": external_id,
            "external_url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{external_id}.jpg",
            "thumbnail_url": None,
            "page_url": None,
            "title": f"Title for {external_id}",
            "description": None,
            "author": None,
            "license": None,
            "license_url": None,
            "width": None,
            "height": None,
            "mime_type": None,
            "file_size": None,
            "source_categories": categories,
        }

    def test_inserts_new_candidates_when_none_exist(self, test_db):
        now = datetime.now(UTC)
        metas = [self._meta("File:N1.jpg"), self._meta("File:N2.jpg")]
        new_count, skip_count = self.service._upsert_candidates(test_db, metas, now)
        assert new_count == 2
        assert skip_count == 0

    def test_skips_and_merges_categories_for_existing_row(self, test_db):
        now = datetime.now(UTC)
        existing = MediaCandidate(
            source="WIKIMEDIA",
            external_id="File:Dup.jpg",
            external_url="https://example.com/dup.jpg",
            title="Existing",
            status="PENDING",
            source_categories=["A"],
            crawled_at=now,
        )
        test_db.add(existing)
        test_db.commit()
        test_db.refresh(existing)

        new_count, skip_count = self.service._upsert_candidates(
            test_db, [self._meta("File:Dup.jpg", categories=["B"])], now
        )

        assert new_count == 0
        assert skip_count == 1
        test_db.refresh(existing)
        assert existing.source_categories == ["A", "B"]

    def test_no_rewrite_when_categories_already_present(self, test_db):
        now = datetime.now(UTC)
        existing = MediaCandidate(
            source="WIKIMEDIA",
            external_id="File:SameCats.jpg",
            external_url="https://example.com/x.jpg",
            title="Existing",
            status="PENDING",
            source_categories=["A", "B"],
            crawled_at=now,
        )
        test_db.add(existing)
        test_db.commit()
        test_db.refresh(existing)

        # Incoming categories are a subset of existing; nothing new to merge.
        self.service._upsert_candidates(
            test_db, [self._meta("File:SameCats.jpg", categories=["A"])], now
        )
        test_db.refresh(existing)
        # Existing list is left as-is (still in original order)
        assert existing.source_categories == ["A", "B"]


# ── Wikimedia: crawl orchestrator ────────────────────────────────────────


class TestWikimediaServiceCrawlOrchestrator:
    """Covers WikimediaService.crawl_categories."""

    def setup_method(self):
        self.service = WikimediaService(rate_limit=0)

    def test_aggregates_counts_from_mocked_subcalls(self, test_db):
        with (
            patch.object(WikimediaService, "get_subcategories") as mock_subcats,
            patch.object(WikimediaService, "get_category_files") as mock_files,
            patch.object(WikimediaService, "get_files_metadata") as mock_meta,
        ):
            mock_subcats.return_value = ["History of Panama"]
            mock_files.return_value = ["File:X.jpg"]
            mock_meta.return_value = [
                {
                    "source": "WIKIMEDIA",
                    "external_id": "File:X.jpg",
                    "external_url": "https://example.com/x.jpg",
                    "thumbnail_url": None,
                    "page_url": None,
                    "title": "X",
                    "description": None,
                    "author": None,
                    "license": None,
                    "license_url": None,
                    "width": None,
                    "height": None,
                    "mime_type": None,
                    "file_size": None,
                    "source_categories": ["History of Panama"],
                }
            ]

            stats = self.service.crawl_categories(
                test_db,
                seed_categories=["History of Panama"],
                max_depth=1,
                max_per_category=10,
            )

        assert stats["categories_crawled"] == 1
        assert stats["images_found"] == 1
        assert stats["images_new"] == 1
        assert stats["images_skipped"] == 0
        assert stats["errors"] == []

    def test_records_errors_from_failing_seed_and_continues(self, test_db):
        with (
            patch.object(WikimediaService, "get_subcategories") as mock_subcats,
            patch.object(WikimediaService, "get_category_files") as mock_files,
            patch.object(WikimediaService, "get_files_metadata") as mock_meta,
        ):
            # First seed blows up, second succeeds with one category and no files
            mock_subcats.side_effect = [
                Exception("API down"),
                ["Panama Canal"],
            ]
            mock_files.return_value = []  # nothing to crawl
            mock_meta.return_value = []

            stats = self.service.crawl_categories(
                test_db,
                seed_categories=["BadSeed", "GoodSeed"],
                max_depth=1,
            )

        assert any("BadSeed" in err for err in stats["errors"])
        # The crawl continued and processed the second seed without raising
        assert stats["images_new"] == 0


# ── LOC: upsert behavior ─────────────────────────────────────────────────


class TestLOCServiceUpsert:
    """Covers LOCService._upsert_candidates — merges both tags and source_categories."""

    def setup_method(self):
        self.service = LOCService(rate_limit=0)

    def _meta(self, external_id: str, tags=None, categories=None):
        return {
            "source": CandidateSource.LOC.value,
            "external_id": external_id,
            "external_url": f"https://tile.loc.gov{external_id}.jpg",
            "thumbnail_url": None,
            "page_url": None,
            "title": f"Title {external_id}",
            "description": None,
            "author": None,
            "tags": tags,
            "license": None,
            "license_url": None,
            "source_categories": categories,
        }

    def test_inserts_new_candidates(self, test_db):
        now = datetime.now(UTC)
        new_count, skip_count = self.service._upsert_candidates(
            test_db,
            [self._meta("/item/loc-a"), self._meta("/item/loc-b")],
            now,
        )
        assert new_count == 2
        assert skip_count == 0

    def test_merges_tags_and_categories_for_existing_row(self, test_db):
        now = datetime.now(UTC)
        existing = MediaCandidate(
            source="LOC",
            external_id="/item/dup-loc",
            external_url="https://tile.loc.gov/dup.jpg",
            title="Existing",
            status="PENDING",
            tags=["tagA"],
            source_categories=["catA"],
            crawled_at=now,
        )
        test_db.add(existing)
        test_db.commit()
        test_db.refresh(existing)

        new_count, skip_count = self.service._upsert_candidates(
            test_db,
            [self._meta("/item/dup-loc", tags=["tagB"], categories=["catB"])],
            now,
        )

        assert new_count == 0
        assert skip_count == 1
        test_db.refresh(existing)
        # Both fields merged and sorted (per source lines 354-364)
        assert existing.tags == ["tagA", "tagB"]
        assert existing.source_categories == ["catA", "catB"]


# ── LOC: crawl orchestrator ──────────────────────────────────────────────


class TestLOCServiceCrawlOrchestrator:
    """Covers LOCService.crawl_panama."""

    def setup_method(self):
        self.service = LOCService(rate_limit=0)

    def test_runs_one_loop_per_query_and_aggregates_stats(self, test_db):
        with (
            patch.object(LOCService, "search") as mock_search,
            patch.object(LOCService, "_extract_metadata") as mock_extract,
        ):
            mock_search.return_value = [{"id": "/item/x/", "title": "X"}]
            mock_extract.return_value = {
                "source": "LOC",
                "external_id": "/item/x",
                "external_url": "https://tile.loc.gov/x.jpg",
                "thumbnail_url": None,
                "page_url": None,
                "title": "X",
                "description": None,
                "author": None,
                "tags": None,
                "license": "pd-us-gov",
                "license_url": None,
                "source_categories": None,
            }

            stats = self.service.crawl_panama(
                test_db,
                queries=[{"query": "panama"}],
                max_per_query=10,
            )

        assert stats["queries_executed"] == 1
        assert stats["images_found"] == 1
        assert stats["images_new"] == 1
        assert stats["errors"] == []

    def test_uses_provided_queries_list_length(self, test_db):
        with (
            patch.object(LOCService, "search") as mock_search,
            patch.object(LOCService, "_extract_metadata") as mock_extract,
        ):
            mock_search.return_value = []  # no results
            mock_extract.return_value = None

            stats = self.service.crawl_panama(
                test_db,
                queries=[{"query": "single"}],
            )

        assert stats["queries_executed"] == 1
        assert mock_search.call_count == 1

    def test_records_error_and_continues_on_search_failure(self, test_db):
        with (
            patch.object(LOCService, "search") as mock_search,
            patch.object(LOCService, "_extract_metadata") as mock_extract,
        ):
            mock_search.side_effect = [Exception("boom"), []]
            mock_extract.return_value = None

            stats = self.service.crawl_panama(
                test_db,
                queries=[{"query": "bad"}, {"query": "good"}],
            )

        assert any("boom" in err for err in stats["errors"])
        # Second query still ran
        assert mock_search.call_count == 2


# ── LOC: item detail fetch ───────────────────────────────────────────────


class TestLOCGetItemDetail:
    """Covers LOCService.get_item_detail — wraps _api_request, swallows errors."""

    def setup_method(self):
        self.service = LOCService(rate_limit=0)

    @patch.object(LOCService, "_api_request")
    def test_returns_api_response_dict(self, mock_api):
        mock_api.return_value = {"item": {"title": "Test"}}
        assert self.service.get_item_detail("https://www.loc.gov/item/123/") == {
            "item": {"title": "Test"}
        }

    @patch.object(LOCService, "_api_request")
    def test_returns_none_when_api_raises(self, mock_api):
        mock_api.side_effect = Exception("network down")
        assert self.service.get_item_detail("https://www.loc.gov/item/123/") is None


# ── LOC: get_best_image_url (module-level helper) ────────────────────────


class TestLOCGetBestImageUrl:
    """Covers loc.get_best_image_url — JPEG-only, size-capped, fallback to None."""

    def _mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    def _wire_client(self, mock_client_cls, json_data):
        """Wire patched make_hardened_client to return a context-manager that
        yields a mock client whose `.get(...)` returns the given JSON."""
        client_instance = MagicMock()
        client_instance.get.return_value = self._mock_response(json_data)
        # `with httpx.Client(...) as client:` calls __enter__ on the instance
        mock_client_cls.return_value.__enter__.return_value = client_instance
        return client_instance

    def test_returns_largest_jpeg_under_cap(self):
        from app.services.loc import get_best_image_url

        json_data = {
            "resources": [
                {
                    "files": [
                        [
                            {
                                "mimetype": "image/jpeg",
                                "url": "https://tile.loc.gov/small.jpg",
                                "size": 10000,
                            },
                            {
                                "mimetype": "image/jpeg",
                                "url": "https://tile.loc.gov/large.jpg",
                                "size": 500000,
                            },
                        ]
                    ]
                }
            ]
        }
        with patch("app.services.loc.make_hardened_client") as mock_client_cls:
            self._wire_client(mock_client_cls, json_data)
            url = get_best_image_url("https://www.loc.gov/item/123/")
        assert url == "https://tile.loc.gov/large.jpg"

    def test_skips_jpegs_over_10mb_cap(self):
        from app.services.loc import get_best_image_url

        json_data = {
            "resources": [
                {
                    "files": [
                        [
                            {
                                "mimetype": "image/jpeg",
                                "url": "https://tile.loc.gov/huge.jpg",
                                "size": 15_000_000,  # > 10MB cap
                            },
                            {
                                "mimetype": "image/jpeg",
                                "url": "https://tile.loc.gov/medium.jpg",
                                "size": 800_000,
                            },
                        ]
                    ]
                }
            ]
        }
        with patch("app.services.loc.make_hardened_client") as mock_client_cls:
            self._wire_client(mock_client_cls, json_data)
            url = get_best_image_url("https://www.loc.gov/item/123/")
        assert url == "https://tile.loc.gov/medium.jpg"

    def test_returns_none_when_only_non_jpeg(self):
        from app.services.loc import get_best_image_url

        # All files are TIFF; no image_url fallback in 'item', no 150px to vary
        json_data = {
            "resources": [
                {
                    "files": [
                        [
                            {
                                "mimetype": "image/tiff",
                                "url": "https://tile.loc.gov/raw.tif",
                                "size": 5_000_000,
                            }
                        ]
                    ]
                }
            ],
            "item": {},
        }
        with patch("app.services.loc.make_hardened_client") as mock_client_cls:
            self._wire_client(mock_client_cls, json_data)
            url = get_best_image_url("https://www.loc.gov/item/123/")
        assert url is None

    def test_returns_none_on_client_exception(self):
        from app.services.loc import get_best_image_url

        with patch("app.services.loc.make_hardened_client") as mock_client_cls:
            mock_client_cls.side_effect = Exception("connection refused")
            url = get_best_image_url("https://www.loc.gov/item/123/")
        assert url is None


class TestApiRequestRetry:
    """_api_request routes through run_with_retry — transient 5xx is retried."""

    def test_loc_api_request_retries_transient_5xx(self):
        service = LOCService(rate_limit=0)
        req = httpx.Request("GET", "https://www.loc.gov/x")
        r503 = httpx.Response(503, request=req)
        r200 = httpx.Response(200, json={"results": [1]}, request=req)
        service._http = MagicMock()
        service._http.get.side_effect = [r503, r200]

        result = service._api_request("https://www.loc.gov/x")

        assert result == {"results": [1]}
        assert service._http.get.call_count == 2

    def test_wikimedia_api_request_retries_transient_5xx(self):
        service = WikimediaService(rate_limit=0)
        req = httpx.Request("GET", "https://commons.wikimedia.org/w/api.php")
        r500 = httpx.Response(500, request=req)
        r200 = httpx.Response(200, json={"query": {"pages": {}}}, request=req)
        service._http = MagicMock()
        service._http.get.side_effect = [r500, r200]

        result = service._api_request({"action": "query"})

        assert result == {"query": {"pages": {}}}
        assert service._http.get.call_count == 2

    def test_loc_api_request_does_not_retry_4xx(self):
        service = LOCService(rate_limit=0)
        req = httpx.Request("GET", "https://www.loc.gov/x")
        r404 = httpx.Response(404, request=req)
        service._http = MagicMock()
        service._http.get.return_value = r404

        with pytest.raises(httpx.HTTPStatusError):
            service._api_request("https://www.loc.gov/x")
        assert service._http.get.call_count == 1
