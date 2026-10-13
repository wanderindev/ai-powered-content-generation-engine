"""Tests for the public delivery surface (#326/#327).

Covers the public deliverables API (`/api/v1/public-edu/deliverables`, no auth)
and the admin publish/unpublish endpoints. The operator publishes the **slides
deck** (which plays its sibling assembled video) and the **study guide**.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion

DELIVERABLES_URL = "/api/v1/public-edu/deliverables"


# --- Helpers ---


def _suggestion(db, excursion_key="panama-viejo", grade_bands=None):
    s = EduSuggestion(
        excursion_key=excursion_key,
        title="Panamá Viejo",
        status="APPROVED",
        grade_bands=grade_bands or ["3rd-6th"],
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _research(db, suggestion):
    r = EduResearch(suggestion_id=suggestion.id, content="rc " * 50, status="APPROVED")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _material(
    db,
    research,
    material_type,
    *,
    grade_band="3rd-6th",
    status="APPROVED",
    file_url="http://cdn/file",
    published_at="__past__",
    duration_ms=None,
    content_blocks=None,
):
    if published_at == "__past__":
        published_at = datetime.now(UTC) - timedelta(hours=1)
    m = EduMaterial(
        research_id=research.id,
        material_type=material_type,
        grade_band=grade_band,
        title="Material",
        status=status,
        file_url=file_url,
        published_at=published_at,
        duration_ms=duration_ms,
        content_blocks=content_blocks,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _slide(db, slides_material, position, image_url):
    s = EduSlide(
        material_id=slides_material.id,
        position=position,
        template="bullets",
        image_url=image_url,
    )
    db.add(s)
    db.commit()
    return s


def _sibling_video(db, research, *, grade_band="3rd-6th", duration_ms=960000):
    """The assembled-video material a slides deck plays (resolved via the deck,
    never published on its own)."""
    return _material(
        db,
        research,
        "video",
        grade_band=grade_band,
        file_url="http://cdn/v.mp4",
        duration_ms=duration_ms,
        published_at=None,
    )


# --- Public deliverables API (no auth) ---


class TestPublicDeliverables:
    def test_lists_published_presentation_and_guide(self, test_client, test_db):
        s = _suggestion(test_db)
        r = _research(test_db, s)
        # Published slides deck + its sibling video + rendered frames.
        slides = _material(test_db, r, "slides", file_url=None)
        _sibling_video(test_db, r, duration_ms=1_000_000)
        _slide(test_db, slides, 2, "http://cdn/frame2.png")
        _slide(test_db, slides, 1, "http://cdn/frame1.png")
        # Published guide with a cover image.
        _material(
            test_db,
            r,
            "guide",
            file_url="http://cdn/g.pdf",
            content_blocks=[{"type": "cover", "media_path": "http://cdn/cover.jpg"}],
        )

        resp = test_client.get(DELIVERABLES_URL)
        assert resp.status_code == 200
        excursions = resp.json()["excursions"]
        assert len(excursions) == 1

        exc = excursions[0]
        assert exc["excursion_key"] == "panama-viejo"
        assert exc["public_key"] == "panama_viejo"
        band = exc["bands"][0]
        assert band["grade_band"] == "3rd-6th"
        assert band["video"]["url"] == "http://cdn/v.mp4"
        assert band["video"]["duration_ms"] == 1_000_000
        assert band["video"]["thumbnail_url"] == "http://cdn/frame1.png"  # lowest position
        assert band["guide_pdf_url"] == "http://cdn/g.pdf"
        assert band["guide_thumbnail_url"] == "http://cdn/cover.jpg"

    def test_slides_without_sibling_video_excluded(self, test_client, test_db):
        # Published deck but nothing assembled to play, and no guide → drop it.
        s = _suggestion(test_db)
        r = _research(test_db, s)
        _material(test_db, r, "slides", file_url=None)
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_guide_only_band_surfaces(self, test_client, test_db):
        s = _suggestion(test_db)
        r = _research(test_db, s)
        _material(test_db, r, "guide", file_url="http://cdn/g.pdf")
        band = test_client.get(DELIVERABLES_URL).json()["excursions"][0]["bands"][0]
        assert band["video"] is None
        assert band["guide_pdf_url"] == "http://cdn/g.pdf"

    def test_directly_published_video_does_not_surface(self, test_client, test_db):
        # Only slides + guide are surfaced; a video published on its own is ignored.
        s = _suggestion(test_db)
        r = _research(test_db, s)
        _material(test_db, r, "video", file_url="http://cdn/v.mp4")
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_unpublished_excluded(self, test_client, test_db):
        s = _suggestion(test_db)
        r = _research(test_db, s)
        _material(test_db, r, "guide", published_at=None)
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_future_published_excluded(self, test_client, test_db):
        s = _suggestion(test_db)
        r = _research(test_db, s)
        _material(test_db, r, "guide", published_at=datetime.now(UTC) + timedelta(days=1))
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_non_approved_excluded(self, test_client, test_db):
        s = _suggestion(test_db)
        r = _research(test_db, s)
        _material(test_db, r, "guide", status="PENDING")
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_non_canonical_excursion_excluded(self, test_client, test_db):
        s = _suggestion(test_db, excursion_key="not-a-real-excursion")
        r = _research(test_db, s)
        _material(test_db, r, "guide", file_url="http://cdn/g.pdf")
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_empty_when_nothing_published(self, test_client, test_db):
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []


# --- Admin publish / unpublish ---


class TestPublishMaterial:
    @pytest.fixture(autouse=True)
    def _bypass_admin_auth(self):
        from app.api.dependencies import validate_admin_token
        from app.main import app

        app.dependency_overrides[validate_admin_token] = lambda: SimpleNamespace(
            user_id=None, admin_email="test@localhost"
        )
        yield
        app.dependency_overrides.pop(validate_admin_token, None)

    def _approved_deck(self, db):
        """An APPROVED (unpublished) slides deck with an assembled sibling video."""
        s = _suggestion(db)
        r = _research(db, s)
        slides = _material(db, r, "slides", file_url=None, published_at=None)
        _sibling_video(db, r)
        return slides

    def test_publish_sets_published_at_and_surfaces(self, test_client, test_db):
        m = self._approved_deck(test_db)
        resp = test_client.patch(f"/api/v1/admin/dashboard/edu/materials/{m.id}/publish")
        assert resp.status_code == 200
        assert resp.json()["published_at"] is not None
        assert len(test_client.get(DELIVERABLES_URL).json()["excursions"]) == 1

    def test_publish_rejects_non_approved(self, test_client, test_db):
        s = _suggestion(test_db)
        r = _research(test_db, s)
        m = _material(test_db, r, "slides", status="PENDING", file_url=None, published_at=None)
        resp = test_client.patch(f"/api/v1/admin/dashboard/edu/materials/{m.id}/publish")
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()

    def test_publish_missing_404(self, test_client, test_db):
        resp = test_client.patch("/api/v1/admin/dashboard/edu/materials/999999/publish")
        assert resp.status_code == 404

    def test_unpublish_clears_and_hides(self, test_client, test_db):
        m = self._approved_deck(test_db)
        test_client.patch(f"/api/v1/admin/dashboard/edu/materials/{m.id}/publish")
        resp = test_client.patch(f"/api/v1/admin/dashboard/edu/materials/{m.id}/unpublish")
        assert resp.status_code == 200
        assert resp.json()["published_at"] is None
        assert test_client.get(DELIVERABLES_URL).json()["excursions"] == []

    def test_admin_list_exposes_published_at(self, test_client, test_db):
        # The grid's publish action reads published_at off the list rows (#327).
        m = self._approved_deck(test_db)
        url = "/api/v1/admin/dashboard/edu/materials"

        row = next(r for r in test_client.get(url).json() if r["id"] == m.id)
        assert row["published_at"] is None

        test_client.patch(f"{url}/{m.id}/publish")
        row = next(r for r in test_client.get(url).json() if r["id"] == m.id)
        assert row["published_at"] is not None
