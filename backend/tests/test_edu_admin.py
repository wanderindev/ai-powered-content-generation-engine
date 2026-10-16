"""Unit tests for the lowest-risk read/CRUD endpoints in app.api.edu.

Scoped per the brief to read/CRUD endpoints only (suggestions/research/materials
list, detail, status update). Upload/download/generate/thumbnail endpoints are
explicitly OUT of scope.

The router is mounted at /api/v1/admin/dashboard/edu (router prefix
"/admin/dashboard/edu"). Admin auth uses the `admin_token` fixture from
conftest.py — no patching needed.
"""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_suggestion import EduSuggestion
from app.schemas.edu import (
    EduMaterialListItem,
    EduResearchListItem,
    EduSuggestionDetail,
    EduSuggestionListItem,
)

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Bypass admin token validation for all tests in this module (#54).

    The router gates auth via ``dependencies=[Depends(validate_admin_token)]``,
    so we override the dependency rather than patching the imported name.
    """
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


# --- Helpers (local, no conftest changes) ---


def _create_suggestion(
    db,
    title="Edu Suggestion",
    grade_bands=None,
    sub_topics=None,
    status="PENDING",
):
    s = EduSuggestion(
        title=title,
        description="Test description",
        grade_bands=grade_bands if grade_bands is not None else ["3rd-6th"],
        sub_topics=sub_topics if sub_topics is not None else ["A", "B"],
        status=status,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _create_research(db, suggestion, content="rc " * 100, status="PENDING"):
    r = EduResearch(
        suggestion_id=suggestion.id,
        content=content,
        status=status,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _create_material(
    db,
    research,
    material_type="guide",
    grade_band="3rd-6th",
    title="Material",
    status="PENDING",
):
    m = EduMaterial(
        research_id=research.id,
        material_type=material_type,
        grade_band=grade_band,
        title=title,
        content="content",
        status=status,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# --- POST /materials/{id}/translate (DeepL EN->ES, #318) ---


class TestTranslateEduGuide:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/translate"

    def _approved_guide(self, db, content="Hello world content", content_es=None):
        s = _create_suggestion(db, status="APPROVED")
        r = _create_research(db, s, status="APPROVED")
        m = _create_material(db, r, material_type="guide", status="APPROVED")
        m.content = content
        m.content_es = content_es
        db.commit()
        db.refresh(m)
        return m

    @staticmethod
    def _mock_service(translated="Hola mundo"):
        svc = MagicMock()
        svc.translate_markdown = AsyncMock(return_value=translated)
        return svc

    @patch("app.services.translation.get_translation_service")
    def test_translates_guide_to_content_es(self, mock_get_service, test_client, test_db):
        svc = self._mock_service("Hola mundo")
        mock_get_service.return_value = svc
        m = self._approved_guide(test_db)

        resp = test_client.post(self.URL.format(id=m.id))

        assert resp.status_code == 200
        body = resp.json()
        assert body["material_id"] == m.id
        assert body["word_count"] == 2
        test_db.refresh(m)
        assert m.content_es == "Hola mundo"
        svc.translate_markdown.assert_awaited_once()

    @patch("app.services.translation.get_translation_service")
    def test_translates_structured_blocks(self, mock_get_service, test_client, test_db):
        # #340: a structured guide translates its blocks (not the markdown round-trip).
        svc = MagicMock()
        es_blocks = [{"type": "paragraph", "text": "Hola"}]
        svc.translate_blocks = AsyncMock(return_value=es_blocks)
        mock_get_service.return_value = svc
        m = self._approved_guide(test_db)
        m.content_blocks = [{"type": "paragraph", "text": "Hello"}]
        test_db.commit()

        resp = test_client.post(self.URL.format(id=m.id))

        assert resp.status_code == 200
        test_db.refresh(m)
        assert m.content_blocks_es == es_blocks
        assert m.content_es == "Hola"  # markdown projection of the ES blocks
        svc.translate_blocks.assert_awaited_once()

    @patch("app.services.translation.get_translation_service")
    def test_passes_formality_through(self, mock_get_service, test_client, test_db):
        svc = self._mock_service()
        mock_get_service.return_value = svc
        m = self._approved_guide(test_db)

        test_client.post(self.URL.format(id=m.id) + "?formality=less")

        assert svc.translate_markdown.await_args.kwargs["formality"] == "less"

    def test_missing_material_404(self, test_client, test_db):
        resp = test_client.post(self.URL.format(id=999999))
        assert resp.status_code == 404

    def test_non_guide_rejected_400(self, test_client, test_db):
        s = _create_suggestion(test_db, status="APPROVED")
        r = _create_research(test_db, s, status="APPROVED")
        m = _create_material(test_db, r, material_type="slides", status="APPROVED")
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400
        assert "guide" in resp.json()["detail"].lower()

    def test_non_approved_rejected_400(self, test_client, test_db):
        s = _create_suggestion(test_db, status="APPROVED")
        r = _create_research(test_db, s, status="APPROVED")
        m = _create_material(test_db, r, material_type="guide", status="PENDING")
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()

    @patch("app.services.translation.get_translation_service")
    def test_existing_es_blocks_without_force(self, mock_get_service, test_client, test_db):
        svc = self._mock_service()
        mock_get_service.return_value = svc
        m = self._approved_guide(test_db, content_es="ya traducido")

        resp = test_client.post(self.URL.format(id=m.id))

        assert resp.status_code == 400
        svc.translate_markdown.assert_not_awaited()

    @patch("app.services.translation.get_translation_service")
    def test_force_overwrites_existing_es(self, mock_get_service, test_client, test_db):
        svc = self._mock_service("nueva traduccion")
        mock_get_service.return_value = svc
        m = self._approved_guide(test_db, content_es="vieja")

        resp = test_client.post(self.URL.format(id=m.id) + "?force=true")

        assert resp.status_code == 200
        test_db.refresh(m)
        assert m.content_es == "nueva traduccion"

    @patch("app.services.translation.get_translation_service")
    def test_service_unavailable_500(self, mock_get_service, test_client, test_db):
        mock_get_service.return_value = None
        m = self._approved_guide(test_db)

        resp = test_client.post(self.URL.format(id=m.id))

        assert resp.status_code == 500


# --- GET /coverage (owed vs produced per excursion x grade band, #319) ---


class TestExcursionCoverage:
    URL = "/api/v1/admin/dashboard/edu/coverage"

    def test_owed_combos_and_produced_status(self, test_client, test_db):
        # Seed panama-viejo with an APPROVED guide for the 3rd-6th band.
        s = _create_suggestion(
            test_db, grade_bands=["3rd-6th", "7th-8th", "9th-12th"], status="APPROVED"
        )
        s.excursion_key = "panama-viejo"
        test_db.commit()
        r = _create_research(test_db, s, status="APPROVED")
        _create_material(test_db, r, material_type="guide", grade_band="3rd-6th", status="APPROVED")

        resp = test_client.get(self.URL, params={"token": "any"})

        assert resp.status_code == 200
        items = resp.json()
        # The owed list is the canonical 24 combos regardless of what's seeded.
        assert len(items) == 24

        by = {(i["excursion_key"], i["grade_band"]): i for i in items}

        produced = by[("panama-viejo", "3rd-6th")]
        assert produced["public_key"] == "panama_viejo"
        assert produced["has_suggestion"] is True
        assert produced["has_research"] is True
        assert produced["guide_status"] == "APPROVED"
        assert produced["slides_status"] is None
        assert produced["video_status"] is None

        # A band of the same excursion with no material is owed-but-empty.
        assert by[("panama-viejo", "9th-12th")]["guide_status"] is None

        # An excursion with no suggestion at all is all-empty.
        unseeded = by[("biomuseo", "3rd-6th")]
        assert unseeded["has_suggestion"] is False
        assert unseeded["has_research"] is False
        assert unseeded["guide_status"] is None

        # Religious surfaces as two excursions under the one public page.
        religious = {i["excursion_key"] for i in items if i["public_key"] == "religious"}
        assert religious == {"inter-religiosa-abrahamica", "inter-religiosa-oriental"}


# --- GET /suggestions ---


class TestListEduSuggestions:
    def test_returns_list_with_schema_fields(self, test_client, test_db):
        _create_suggestion(test_db, title="One")

        response = test_client.get(
            "/api/v1/admin/dashboard/edu/suggestions",
            params={"token": "any"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        # Every item must satisfy the response_model schema.
        expected = set(EduSuggestionListItem.model_fields.keys())
        for item in data:
            assert expected.issubset(set(item.keys()))


# --- GET /suggestions/{suggestion_id} ---


class TestGetEduSuggestion:
    def test_returns_detail_with_schema_fields(self, test_client, test_db):
        s = _create_suggestion(test_db, title="Detail Target")

        response = test_client.get(
            f"/api/v1/admin/dashboard/edu/suggestions/{s.id}",
            params={"token": "any"},
        )

        assert response.status_code == 200
        data = response.json()
        expected = set(EduSuggestionDetail.model_fields.keys())
        assert expected.issubset(set(data.keys()))
        assert data["id"] == s.id
        assert data["title"] == "Detail Target"

    def test_missing_id_returns_404(self, test_client, test_db):
        response = test_client.get(
            "/api/v1/admin/dashboard/edu/suggestions/99999",
            params={"token": "any"},
        )
        assert response.status_code == 404


# --- PATCH /suggestions/{suggestion_id}/status ---


class TestUpdateEduSuggestionStatus:
    def test_valid_transition_pending_to_approved(self, test_client, test_db):
        s = _create_suggestion(test_db, status="PENDING")

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/suggestions/{s.id}/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == s.id
        assert body["status"] == "APPROVED"

    def test_missing_id_returns_404(self, test_client, test_db):
        response = test_client.patch(
            "/api/v1/admin/dashboard/edu/suggestions/99999/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )
        assert response.status_code == 404

    def test_invalid_transition_returns_400(self, test_client, test_db):
        # APPROVED -> APPROVED is not in VALID_TRANSITIONS (which only
        # lists transitions BETWEEN distinct states).
        s = _create_suggestion(test_db, status="APPROVED")

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/suggestions/{s.id}/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )
        assert response.status_code == 400


# --- PUT /suggestions/{suggestion_id} ---


class TestUpdateEduSuggestion:
    def test_partial_update_changes_only_supplied_fields(self, test_client, test_db):
        s = _create_suggestion(test_db, title="Original", status="PENDING")

        response = test_client.put(
            f"/api/v1/admin/dashboard/edu/suggestions/{s.id}",
            params={"token": "any"},
            json={"title": "Updated"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated"
        # Status is not updatable via this endpoint and should be unchanged.
        assert data["status"] == "PENDING"
        # Response shape conforms to detail schema.
        expected = set(EduSuggestionDetail.model_fields.keys())
        assert expected.issubset(set(data.keys()))

    def test_missing_id_returns_404(self, test_client, test_db):
        response = test_client.put(
            "/api/v1/admin/dashboard/edu/suggestions/99999",
            params={"token": "any"},
            json={"title": "Whatever"},
        )
        assert response.status_code == 404

    def test_oversized_grade_bands_rejected_422(self, test_client, test_db):
        # #92: the grade_bands list is capped to bound per-request LLM cost.
        s = _create_suggestion(test_db, status="PENDING")

        response = test_client.put(
            f"/api/v1/admin/dashboard/edu/suggestions/{s.id}",
            params={"token": "any"},
            json={"grade_bands": [f"band-{i}" for i in range(7)]},
        )

        assert response.status_code == 422

    def test_grade_bands_at_cap_accepted(self, test_client, test_db):
        s = _create_suggestion(test_db, status="PENDING")

        response = test_client.put(
            f"/api/v1/admin/dashboard/edu/suggestions/{s.id}",
            params={"token": "any"},
            json={"grade_bands": [f"band-{i}" for i in range(6)]},
        )

        assert response.status_code == 200
        assert len(response.json()["grade_bands"]) == 6


# --- GET /research ---


class TestListEduResearch:
    def test_returns_list_with_schema_fields(self, test_client, test_db):
        s = _create_suggestion(test_db)
        _create_research(test_db, s)

        response = test_client.get(
            "/api/v1/admin/dashboard/edu/research",
            params={"token": "any"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        expected = set(EduResearchListItem.model_fields.keys())
        for item in data:
            assert expected.issubset(set(item.keys()))


# --- PATCH /research/{research_id}/status ---


class TestUpdateEduResearchStatus:
    def test_valid_transition_pending_to_approved(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s, status="PENDING")

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/research/{r.id}/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == r.id
        assert body["status"] == "APPROVED"

    def test_missing_id_returns_404(self, test_client, test_db):
        response = test_client.patch(
            "/api/v1/admin/dashboard/edu/research/99999/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )
        assert response.status_code == 404

    def test_invalid_transition_returns_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s, status="PENDING")

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/research/{r.id}/status",
            params={"token": "any"},
            json={"status": "PENDING"},
        )
        assert response.status_code == 400


# --- POST /research/upload ---


class TestUploadEduResearch:
    URL = "/api/v1/admin/dashboard/edu/research/upload"

    _VALID = {
        "valid": True,
        "checks": [],
        "word_count": 4321,
        "content": "# Title\n\nFinal content with refs.",
    }

    @patch("app.api.edu.validate_edu_research_document")
    def test_creates_new_research(self, mock_validate, test_client, test_db):
        mock_validate.return_value = self._VALID
        s = _create_suggestion(test_db)
        response = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# Title\n\nbody", "text/markdown")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["suggestion_id"] == s.id
        assert body["status"] == "PENDING"
        r = test_db.query(EduResearch).filter(EduResearch.suggestion_id == s.id).one()
        assert "Final content" in r.content

    @patch("app.api.edu.validate_edu_research_document")
    def test_updates_existing_pending_and_resets_status(self, mock_validate, test_client, test_db):
        mock_validate.return_value = {**self._VALID, "content": "NEW CONTENT"}
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s, content="OLD", status="REJECTED")
        response = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# Title\n", "text/markdown")},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"
        test_db.refresh(r)
        assert r.content == "NEW CONTENT"
        assert r.status == "PENDING"

    def test_unknown_suggestion_404(self, test_client, test_db):
        response = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"suggestion_id": 9_999_999},
            files={"file": ("research.md", b"# Title", "text/markdown")},
        )
        assert response.status_code == 404

    @patch("app.api.edu.validate_edu_research_document")
    def test_over_approved_blocked_409(self, mock_validate, test_client, test_db):
        mock_validate.return_value = {**self._VALID, "content": "NEW CONTENT"}
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s, content="APPROVED CONTENT", status="APPROVED")
        response = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# Title", "text/markdown")},
        )
        assert response.status_code == 409
        test_db.refresh(r)
        assert r.content == "APPROVED CONTENT"
        assert r.status == "APPROVED"

    @patch("app.api.edu.validate_edu_research_document")
    def test_over_approved_with_force_overwrites_and_resets(
        self, mock_validate, test_client, test_db
    ):
        mock_validate.return_value = {**self._VALID, "content": "FORCED NEW"}
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s, content="APPROVED CONTENT", status="APPROVED")
        response = test_client.post(
            self.URL,
            params={"token": "any", "force": "true"},
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# Title", "text/markdown")},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"
        test_db.refresh(r)
        assert r.content == "FORCED NEW"
        assert r.status == "PENDING"

    def test_non_utf8_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        response = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"\xff\xfe\x00bad", "text/markdown")},
        )
        assert response.status_code == 400
        assert "utf-8" in response.json()["detail"].lower()

    def test_oversize_413(self, test_client, test_db):
        from app.services.research_common import MAX_RESEARCH_UPLOAD_BYTES

        s = _create_suggestion(test_db)
        oversize = b"a" * (MAX_RESEARCH_UPLOAD_BYTES + 1)
        response = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"suggestion_id": s.id},
            files={"file": ("research.md", oversize, "text/markdown")},
        )
        assert response.status_code == 413


# --- POST /materials/upload (watermark hard-fail + file-type validation, #94) ---


def _real_png() -> bytes:
    """A small but genuinely decodable PNG.

    The upload path now decodes infographics to strip the NotebookLM watermark
    (#312), so the fixture must be a real image — magic bytes alone no longer
    survive. Kept smaller than the watermark box so strip() re-encodes it
    unchanged (the strip painting itself is covered in test_image_storage.py).
    """
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


class TestUploadMaterial:
    URL = "/api/v1/admin/dashboard/edu/materials/upload"
    PNG = _real_png()
    PDF = b"%PDF-1.4 fakepdf"

    def _storage(self):
        inst = MagicMock()
        inst.upload_image.return_value = "http://cdn/file"
        inst.generate_thumbnail.return_value = "http://cdn/thumb"
        return inst

    @patch("app.services.image_storage.ImageStorageService")
    def test_type_mismatch_rejected_400_nothing_stored(
        self, mock_storage_cls, test_client, test_db, research_factory
    ):
        # A PDF declared as an infographic is rejected before any upload (#94).
        inst = self._storage()
        mock_storage_cls.return_value = inst
        r = research_factory()
        resp = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"material_type": "infographic", "blog_research_id": r.id},
            files={"file": ("x.pdf", self.PDF, "application/pdf")},
        )
        assert resp.status_code == 400
        inst.upload_image.assert_not_called()
        assert test_db.query(EduMaterial).count() == 0

    @patch("app.api.edu.sentry_sdk")
    @patch("app.services.image_storage.watermark_infographic", side_effect=RuntimeError("boom"))
    @patch("app.services.image_storage.ImageStorageService")
    def test_watermark_failure_aborts_500_no_upload(
        self, mock_storage_cls, _mock_wm, mock_sentry, test_client, test_db, research_factory
    ):
        # A watermark failure must abort — never ship an un-watermarked file (#94).
        inst = self._storage()
        mock_storage_cls.return_value = inst
        r = research_factory()
        resp = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"material_type": "infographic", "blog_research_id": r.id},
            files={"file": ("x.png", self.PNG, "image/png")},
        )
        assert resp.status_code == 500
        inst.upload_image.assert_not_called()
        assert test_db.query(EduMaterial).count() == 0
        mock_sentry.capture_exception.assert_called_once()

    @patch("app.api.edu.sentry_sdk")
    @patch("app.services.image_storage.watermark_slides", side_effect=ImportError("no fitz"))
    @patch("app.services.image_storage.ImageStorageService")
    def test_missing_watermark_dependency_500(
        self, mock_storage_cls, _mock_wm, mock_sentry, test_client, test_db, research_factory
    ):
        # A missing watermarking dependency is a loud deploy error (#94).
        inst = self._storage()
        mock_storage_cls.return_value = inst
        r = research_factory()
        resp = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"material_type": "slides", "blog_research_id": r.id},
            files={"file": ("x.pdf", self.PDF, "application/pdf")},
        )
        assert resp.status_code == 500
        assert "dependency" in resp.json()["detail"].lower()
        inst.upload_image.assert_not_called()
        mock_sentry.capture_exception.assert_called_once()

    @patch("app.services.image_storage.watermark_infographic", return_value=b"WATERMARKED")
    @patch("app.services.image_storage.ImageStorageService")
    def test_valid_infographic_uploads_watermarked_bytes_200(
        self, mock_storage_cls, _mock_wm, test_client, test_db, research_factory
    ):
        # A valid file is watermarked and only then stored (#94).
        inst = self._storage()
        mock_storage_cls.return_value = inst
        r = research_factory()
        resp = test_client.post(
            self.URL,
            params={"token": "any"},
            data={"material_type": "infographic", "blog_research_id": r.id},
            files={"file": ("x.png", self.PNG, "image/png")},
        )
        assert resp.status_code == 200
        uploaded_bytes = inst.upload_image.call_args.args[0]
        assert uploaded_bytes == b"WATERMARKED"
        material = test_db.query(EduMaterial).one()
        assert material.material_type == "infographic"
        assert material.blog_research_id == r.id


# --- GET /materials ---


class TestListEduMaterials:
    def test_returns_list_with_schema_fields(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        _create_material(test_db, r)

        response = test_client.get(
            "/api/v1/admin/dashboard/edu/materials",
            params={"token": "any"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        expected = set(EduMaterialListItem.model_fields.keys())
        for item in data:
            assert expected.issubset(set(item.keys()))

    def test_has_spanish_reflects_translation(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, material_type="guide", status="APPROVED")
        m.content, m.content_es = "## Guide", "## Guía"
        test_db.commit()

        data = test_client.get(
            "/api/v1/admin/dashboard/edu/materials", params={"token": "any"}
        ).json()
        row = next(x for x in data if x["id"] == m.id)
        assert row["has_spanish"] is True


# --- GET /materials/{material_id}/detail (EN + ES content) ---


class TestEduMaterialDetail:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/detail"

    def test_returns_spanish_content_and_flag_when_translated(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, material_type="guide", status="APPROVED")
        m.content = "## Guide\n\nEnglish body."
        m.content_es = "## Guía\n\nCuerpo en español."
        test_db.commit()

        body = test_client.get(self.URL.format(id=m.id), params={"token": "any"}).json()
        assert body["has_spanish"] is True
        assert "English body" in body["content_html"]
        assert "español" in body["content_es_html"]

    def test_guide_detail_lists_both_language_pdfs(self, test_client, test_db):
        """file_url only remembers the last render; the detail probes the other
        language's deterministic key so the drawer can link EN and ES."""
        from unittest.mock import MagicMock, patch

        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, material_type="guide", status="APPROVED")
        m.grade_band = "3rd-6th"
        m.file_url = f"https://cdn.example/materials/guide/{r.id}/3rd_6th_es.pdf"
        test_db.commit()

        storage = MagicMock()
        storage.__enter__.return_value = storage
        storage.object_exists.side_effect = lambda key: key.endswith("_es.pdf")
        storage.public_url.side_effect = lambda key: f"https://cdn.example/{key}"
        with patch("app.services.image_storage.ImageStorageService", return_value=storage):
            body = test_client.get(self.URL.format(id=m.id), params={"token": "any"}).json()

        assert body["pdf_urls"] == {
            "en": None,
            "es": f"https://cdn.example/materials/guide/{r.id}/3rd_6th_es.pdf",
        }

    def test_guide_detail_without_pdf_does_not_probe_storage(self, test_client, test_db):
        from unittest.mock import patch

        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, material_type="guide", status="APPROVED")
        test_db.commit()

        with patch("app.services.image_storage.ImageStorageService") as storage_cls:
            body = test_client.get(self.URL.format(id=m.id), params={"token": "any"}).json()
        storage_cls.assert_not_called()
        assert body["pdf_urls"] is None

    def test_untranslated_has_no_spanish(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, material_type="guide", status="APPROVED")
        m.content = "## Guide"
        test_db.commit()

        body = test_client.get(self.URL.format(id=m.id), params={"token": "any"}).json()
        assert body["has_spanish"] is False
        assert body["content_es_html"] == ""


# --- PATCH /materials/{material_id}/status ---


class TestUpdateEduMaterialStatus:
    def test_valid_transition_pending_to_approved(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, status="PENDING")

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/materials/{m.id}/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == m.id
        assert body["status"] == "APPROVED"

    def test_missing_id_returns_404(self, test_client, test_db):
        response = test_client.patch(
            "/api/v1/admin/dashboard/edu/materials/99999/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )
        assert response.status_code == 404

    def test_invalid_transition_returns_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_material(test_db, r, status="APPROVED")

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/materials/{m.id}/status",
            params={"token": "any"},
            json={"status": "APPROVED"},
        )
        assert response.status_code == 400


# --- translate endpoint: Spanish block overrides (study-guide redesign) ---


class TestTranslateSpanishOverrides:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/translate"

    @patch("app.services.translation.get_translation_service")
    def test_excursion_cover_and_review_titles_use_canonical_spanish(
        self, mock_get_service, test_client, test_db
    ):
        s = _create_suggestion(test_db, status="APPROVED")
        s.excursion_key = "panama-viejo"
        r = _create_research(test_db, s, status="APPROVED")
        m = _create_material(test_db, r, material_type="guide", status="APPROVED")
        m.content_blocks = [
            {"type": "cover", "title": "Panama Viejo Archeological Complex", "subtitle": "s"},
            {"type": "section", "title": "Test Your Knowledge"},
            {"type": "section", "title": "For Reflection"},
        ]
        test_db.commit()

        # DeepL mock returns machine-translated titles the overrides must replace.
        svc = MagicMock()
        svc.translate_blocks = AsyncMock(
            return_value=[
                {"type": "cover", "title": "Complejo DeepL", "subtitle": "s2"},
                {"type": "section", "title": "Prueba tu conocimiento"},
                {"type": "section", "title": "Reflexiona"},
            ]
        )
        mock_get_service.return_value = svc

        resp = test_client.post(self.URL.format(id=m.id))

        assert resp.status_code == 200
        test_db.refresh(m)
        assert m.content_blocks_es[0]["title"] == "Complejo Arqueológico de Panamá Viejo"
        assert m.content_blocks_es[1]["title"] == "Pon a prueba tus conocimientos"
        assert m.content_blocks_es[2]["title"] == "Para reflexionar"


# --- PUT /materials/{id}/content (admin block editing, study-guide redesign) ---


class TestUpdateEduMaterialContent:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/content"

    def _guide(self, db, **kw):
        s = _create_suggestion(db, status="APPROVED")
        s.excursion_key = kw.pop("excursion_key", None)
        r = _create_research(db, s, status="APPROVED")
        m = _create_material(db, r, material_type=kw.pop("material_type", "guide"))
        m.content_blocks = kw.pop(
            "content_blocks",
            [
                {"type": "cover", "title": "T", "subtitle": None},
                {"type": "paragraph", "text": "Original."},
            ],
        )
        db.commit()
        db.refresh(m)
        return m

    def test_saves_blocks_and_regenerates_markdown_projection(self, test_client, test_db):
        m = self._guide(test_db)
        new_blocks = [
            {"type": "cover", "title": "Edited Title", "subtitle": "New sub"},
            {"type": "paragraph", "text": "Edited body."},
            {"type": "mc_question", "question": "Q?", "choices": ["a", "b"]},
        ]
        resp = test_client.put(
            self.URL.format(id=m.id),
            json={"title": "Edited Title", "content_blocks": new_blocks},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Edited Title"
        assert body["content_blocks"][0]["title"] == "Edited Title"
        test_db.refresh(m)
        assert m.title == "Edited Title"
        assert m.content_blocks[2]["type"] == "mc_question"
        assert "Edited body." in m.content  # markdown projection regenerated
        assert "a) a" in m.content  # mc question projected too

    def test_spanish_blocks_update_spanish_projection_only(self, test_client, test_db):
        m = self._guide(test_db)
        original_en = m.content
        resp = test_client.put(
            self.URL.format(id=m.id),
            json={"content_blocks_es": [{"type": "paragraph", "text": "Cuerpo editado."}]},
        )
        assert resp.status_code == 200
        test_db.refresh(m)
        assert m.content_blocks_es == [{"type": "paragraph", "text": "Cuerpo editado."}]
        assert m.content_es == "Cuerpo editado."
        assert m.content == original_en  # EN untouched

    def test_invalid_block_rejected_with_422_naming_it(self, test_client, test_db):
        m = self._guide(test_db)
        original = list(m.content_blocks)
        resp = test_client.put(
            self.URL.format(id=m.id),
            json={"content_blocks": [{"type": "paragraph", "text": "ok"}, {"type": "bogus"}]},
        )
        assert resp.status_code == 422
        assert "block 1" in resp.json()["detail"]
        test_db.refresh(m)
        assert m.content_blocks == original  # nothing saved

    def test_empty_blocks_rejected(self, test_client, test_db):
        m = self._guide(test_db)
        resp = test_client.put(self.URL.format(id=m.id), json={"content_blocks": []})
        assert resp.status_code == 422

    def test_blank_title_rejected(self, test_client, test_db):
        m = self._guide(test_db)
        resp = test_client.put(self.URL.format(id=m.id), json={"title": "   "})
        assert resp.status_code == 422

    def test_non_guide_400(self, test_client, test_db):
        m = self._guide(test_db, material_type="slides", content_blocks=None)
        resp = test_client.put(
            self.URL.format(id=m.id),
            json={"content_blocks": [{"type": "paragraph", "text": "x"}]},
        )
        assert resp.status_code == 400

    def test_missing_material_404(self, test_client, test_db):
        resp = test_client.put(
            self.URL.format(id=999999),
            json={"content_blocks": [{"type": "paragraph", "text": "x"}]},
        )
        assert resp.status_code == 404

    def test_detail_exposes_blocks_and_excursion_key(self, test_client, test_db):
        m = self._guide(test_db, excursion_key="panama-viejo")
        resp = test_client.get(f"/api/v1/admin/dashboard/edu/materials/{m.id}/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_blocks"][0]["type"] == "cover"
        assert body["content_blocks_es"] is None
        assert body["excursion_key"] == "panama-viejo"


# --- PUT /materials/{id}/slides + POST /materials/{id}/generate-narration ---
# (slides redesign: admin deck editing + the stage-2 narration pass)


def _create_slides_material(db, research, status="PENDING", narrations=(None, None)):
    """A slides material with len(narrations) bullet rows."""
    from app.models.edu_slide import EduSlide

    m = EduMaterial(
        research_id=research.id,
        material_type="slides",
        grade_band="3rd-6th",
        title="Deck",
        content="preview",
        source_briefing="BRIEFING",
        status=status,
    )
    db.add(m)
    db.flush()
    for i, narration in enumerate(narrations, start=1):
        m.slides.append(
            EduSlide(
                position=i,
                template="bullets",
                title=f"Slide {i}",
                body="Uno\nDos",
                image_query=f"query {i}",
                narration=narration,
                image_url=f"https://cdn.example/frames/{i}.png",
                audio_url=f"https://cdn.example/audio/{i}.mp3" if narration else None,
                duration_ms=4000 if narration else None,
            )
        )
    db.commit()
    db.refresh(m)
    return m


def _slide_payload(slide):
    return {
        "id": slide.id,
        "template": slide.template,
        "title": slide.title,
        "body": slide.body,
        "image_query": slide.image_query,
        "media_id": slide.media_id,
        "media_path": slide.media_path,
        "narration": slide.narration,
    }


class TestUpdateEduSlides:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/slides"

    def test_material_not_found_404(self, test_client, test_db):
        resp = test_client.put(
            self.URL.format(id=999999),
            json={"slides": [{"template": "bullets", "title": "X"}]},
        )
        assert resp.status_code == 404

    def test_non_slides_material_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        guide = _create_material(test_db, r, material_type="guide")
        resp = test_client.put(
            self.URL.format(id=guide.id),
            json={"slides": [{"template": "bullets", "title": "X"}]},
        )
        assert resp.status_code == 400

    def test_unknown_template_422_names_slide(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        payload = [_slide_payload(sl) for sl in m.slides]
        payload[1]["template"] = "hologram"
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": payload})
        assert resp.status_code == 422
        assert "slide 2" in resp.json()["detail"]
        assert "hologram" in resp.json()["detail"]

    def test_empty_slide_422(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        payload = [_slide_payload(sl) for sl in m.slides]
        payload[0]["title"] = None
        payload[0]["body"] = None
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": payload})
        assert resp.status_code == 422
        assert "slide 1" in resp.json()["detail"]

    def test_round_trip_preserves_artifacts_when_unchanged(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, narrations=("Hola.", "Adios."))
        payload = [_slide_payload(sl) for sl in m.slides]
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": payload})
        assert resp.status_code == 200
        slides = resp.json()["slides"]
        assert [sl["position"] for sl in slides] == [1, 2]
        assert slides[0]["image_url"] == "https://cdn.example/frames/1.png"
        assert slides[0]["audio_url"] == "https://cdn.example/audio/1.mp3"
        assert slides[0]["duration_ms"] == 4000

    def test_edited_body_clears_frame_but_keeps_audio(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, narrations=("Hola.", "Adios."))
        payload = [_slide_payload(sl) for sl in m.slides]
        payload[0]["body"] = "Uno\nDos\nTres nuevos"
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": payload})
        assert resp.status_code == 200
        slides = resp.json()["slides"]
        assert slides[0]["image_url"] is None  # frame stale: on-screen content changed
        assert slides[0]["audio_url"] == "https://cdn.example/audio/1.mp3"  # script unchanged
        assert slides[1]["image_url"] == "https://cdn.example/frames/2.png"

    def test_edited_narration_clears_audio_but_keeps_frame(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, narrations=("Hola.", "Adios."))
        payload = [_slide_payload(sl) for sl in m.slides]
        payload[0]["narration"] = "Guion nuevo."
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": payload})
        assert resp.status_code == 200
        slides = resp.json()["slides"]
        assert slides[0]["audio_url"] is None
        assert slides[0]["duration_ms"] is None
        assert slides[0]["image_url"] == "https://cdn.example/frames/1.png"

    def test_media_pick_clears_frame_and_persists(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        payload = [_slide_payload(sl) for sl in m.slides]
        payload[0]["media_id"] = 77
        payload[0]["media_path"] = "https://cdn.example/photos/plaza.jpg"
        # media_id has a FK — use a real Media row? The FK is enforced in tests.
        from app.models.media import Media

        media = Media(
            filename="plaza.jpg",
            original_filename="plaza.jpg",
            file_path="https://cdn.example/photos/plaza.jpg",
            file_size=123,
            mime_type="image/jpeg",
            media_type="IMAGE",
            source="WIKIMEDIA",
        )
        test_db.add(media)
        test_db.commit()
        payload[0]["media_id"] = media.id
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": payload})
        assert resp.status_code == 200
        slides = resp.json()["slides"]
        assert slides[0]["media_id"] == media.id
        assert slides[0]["media_path"] == "https://cdn.example/photos/plaza.jpg"
        assert slides[0]["image_url"] is None  # embedded photo changed → frame stale

    def test_reorder_add_remove_and_status_untouched(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, status="APPROVED", narrations=(None, None))
        payload = [_slide_payload(sl) for sl in m.slides]
        # Reverse order, drop nothing, add a new slide (no id → no artifacts).
        payload = [payload[1], payload[0], {"template": "closing", "title": "Gracias"}]
        resp = test_client.put(
            self.URL.format(id=m.id), json={"title": "Deck v2", "slides": payload}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Deck v2"
        assert body["status"] == "APPROVED"  # editing does not reset approval
        slides = body["slides"]
        assert [sl["position"] for sl in slides] == [1, 2, 3]
        assert slides[0]["title"] == "Slide 2"  # reordered
        assert slides[2]["template"] == "closing"
        assert slides[2]["image_url"] is None

    def test_artifacts_never_accepted_from_client(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        payload = [_slide_payload(sl) for sl in m.slides]
        # A payload without id is a new row; forged artifact fields must be ignored.
        forged = {
            "template": "bullets",
            "title": "Nuevo",
            "image_url": "https://evil.example/x.png",
            "audio_url": "https://evil.example/x.mp3",
            "duration_ms": 999,
        }
        resp = test_client.put(self.URL.format(id=m.id), json={"slides": [*payload, forged]})
        assert resp.status_code == 200
        assert resp.json()["slides"][2]["image_url"] is None
        assert resp.json()["slides"][2]["audio_url"] is None


class TestGenerateNarrationEndpoint:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/generate-narration"

    def test_material_not_found_404(self, test_client, test_db):
        resp = test_client.post(self.URL.format(id=999999), json={})
        assert resp.status_code == 404

    def test_non_slides_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        guide = _create_material(test_db, r, material_type="guide")
        resp = test_client.post(self.URL.format(id=guide.id), json={})
        assert resp.status_code == 400

    def test_pending_deck_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, status="PENDING")
        resp = test_client.post(self.URL.format(id=m.id), json={})
        assert resp.status_code == 400
        assert "Approve" in resp.json()["detail"]

    def test_existing_narration_409_without_force(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, status="APPROVED", narrations=("Ya.", None))
        resp = test_client.post(self.URL.format(id=m.id), json={})
        assert resp.status_code == 409

    @patch("app.api.edu.generate_edu_narration")
    def test_success(self, mock_gen, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, status="APPROVED")
        mock_gen.return_value = {"slides_narrated": 2, "total_words": 2400}
        resp = test_client.post(self.URL.format(id=m.id), json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["slides_narrated"] == 2
        assert body["total_words"] == 2400
        mock_gen.assert_called_once()

    @patch("app.api.edu.generate_edu_narration")
    def test_subset_skips_force_guard_and_threads_positions(self, mock_gen, test_client, test_db):
        # An already-narrated deck would 409 for a whole-deck rewrite, but a
        # positions subset is intentional — it skips the guard and passes a set.
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, status="APPROVED", narrations=("Ya.", "Ya."))
        mock_gen.return_value = {"slides_narrated": 1, "total_words": 150}
        resp = test_client.post(self.URL.format(id=m.id), json={"positions": [1]})
        assert resp.status_code == 200
        assert mock_gen.call_args.kwargs["positions"] == {1}

    def test_subset_out_of_range_422(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, status="APPROVED", narrations=("Ya.", None))
        with patch("app.api.edu.generate_edu_narration") as svc:
            resp = test_client.post(self.URL.format(id=m.id), json={"positions": [7]})
        assert resp.status_code == 422
        svc.assert_not_called()


class TestRegenerateSlidesSubsetEndpoint:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/regenerate-slides"

    def test_material_not_found_404(self, test_client, test_db):
        resp = test_client.post(self.URL.format(id=999999), json={"positions": [1]})
        assert resp.status_code == 404

    def test_non_slides_400(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        guide = _create_material(test_db, r, material_type="guide")
        resp = test_client.post(self.URL.format(id=guide.id), json={"positions": [1]})
        assert resp.status_code == 400

    def test_missing_positions_422(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        with patch("app.api.edu.regenerate_slides_subset") as svc:
            resp = test_client.post(self.URL.format(id=m.id), json={})
        assert resp.status_code == 422  # positions is required for this endpoint
        svc.assert_not_called()

    def test_out_of_range_422(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        with patch("app.api.edu.regenerate_slides_subset") as svc:
            resp = test_client.post(self.URL.format(id=m.id), json={"positions": [9]})
        assert resp.status_code == 422
        svc.assert_not_called()

    @patch("app.api.edu.regenerate_slides_subset")
    def test_success_threads_positions_and_returns_summary(self, mock_svc, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, narrations=("Uno.", "Dos."))
        mock_svc.return_value = {"positions": [2], "slides_regenerated": 1}
        resp = test_client.post(self.URL.format(id=m.id), json={"positions": [2]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["positions"] == [2]
        assert body["slides_regenerated"] == 1
        assert body["material_id"] == m.id
        assert mock_svc.call_args.args[1] == {2}  # positions set passed to service


class TestEduMaterialDetailSlides:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/detail"

    def test_detail_exposes_slides_for_deck(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r, narrations=("Hola.", None))
        resp = test_client.get(self.URL.format(id=m.id))
        assert resp.status_code == 200
        slides = resp.json()["slides"]
        assert len(slides) == 2
        assert slides[0]["template"] == "bullets"
        assert slides[0]["narration"] == "Hola."
        assert slides[0]["media_path"] is None
        assert slides[0]["image_query"] == "query 1"

    def test_detail_slides_none_for_guides(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        guide = _create_material(test_db, r, material_type="guide")
        resp = test_client.get(self.URL.format(id=guide.id))
        assert resp.status_code == 200
        assert resp.json()["slides"] is None

    def test_detail_exposes_sibling_video_for_deck(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)
        video = EduMaterial(
            research_id=r.id,
            material_type="video",
            grade_band=m.grade_band,
            title="Deck video",
            file_url="https://cdn.example/video.mp4",
            duration_ms=930_000,
            status="PENDING",
        )
        test_db.add(video)
        test_db.commit()

        resp = test_client.get(self.URL.format(id=m.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["video_material_id"] == video.id
        assert body["video_url"] == "https://cdn.example/video.mp4"
        assert body["video_duration_ms"] == 930_000

    def test_detail_video_fields_none_without_matching_sibling(self, test_client, test_db):
        s = _create_suggestion(test_db)
        r = _create_research(test_db, s)
        m = _create_slides_material(test_db, r)  # grade_band 3rd-6th
        # A video for a DIFFERENT band must not be picked up.
        other_band = EduMaterial(
            research_id=r.id,
            material_type="video",
            grade_band="9th-12th",
            title="Other band video",
            file_url="https://cdn.example/other.mp4",
            status="PENDING",
        )
        test_db.add(other_band)
        test_db.commit()

        resp = test_client.get(self.URL.format(id=m.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["video_material_id"] is None
        assert body["video_url"] is None
        assert body["video_duration_ms"] is None


# --- GET /research/available-for-materials ---


class TestResearchAvailableForMaterials:
    URL = "/api/v1/admin/dashboard/edu/research/available-for-materials"

    def _row(self, test_client, research_id):
        data = test_client.get(self.URL).json()
        return next((x for x in data if x["id"] == research_id), None)

    def test_untouched_research_lists_every_band(self, test_client, test_db):
        s = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th", "9th-12th"])
        r = _create_research(test_db, s, status="APPROVED")

        row = self._row(test_client, r.id)
        assert row["bands_to_generate"] == ["3rd-6th", "7th-8th", "9th-12th"]

    def test_partially_generated_research_stays_listed_with_missing_band(
        self, test_client, test_db
    ):
        # The Panama History Museum case: 9th-12th failed, the other two guides
        # were approved, and a slide deck exists — the research must stay
        # offered so the missing band can be generated.
        s = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th", "9th-12th"])
        r = _create_research(test_db, s, status="APPROVED")
        _create_material(test_db, r, grade_band="3rd-6th", status="APPROVED")
        _create_material(test_db, r, grade_band="7th-8th", status="APPROVED")
        _create_material(test_db, r, material_type="slides", grade_band="3rd-6th")

        row = self._row(test_client, r.id)
        assert row is not None
        assert row["grade_bands"] == ["3rd-6th", "7th-8th", "9th-12th"]
        assert row["bands_to_generate"] == ["9th-12th"]

    def test_pending_guide_counts_as_to_generate(self, test_client, test_db):
        s = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th"])
        r = _create_research(test_db, s, status="APPROVED")
        _create_material(test_db, r, grade_band="3rd-6th", status="APPROVED")
        _create_material(test_db, r, grade_band="7th-8th", status="PENDING")

        assert self._row(test_client, r.id)["bands_to_generate"] == ["7th-8th"]

    def test_all_bands_approved_is_not_listed(self, test_client, test_db):
        s = _create_suggestion(test_db, grade_bands=["3rd-6th", "7th-8th"])
        r = _create_research(test_db, s, status="APPROVED")
        _create_material(test_db, r, grade_band="3rd-6th", status="APPROVED")
        _create_material(test_db, r, grade_band="7th-8th", status="APPROVED")

        assert self._row(test_client, r.id) is None

    def test_unapproved_research_is_not_listed(self, test_client, test_db):
        s = _create_suggestion(test_db, grade_bands=["3rd-6th"])
        r = _create_research(test_db, s, status="PENDING")

        assert self._row(test_client, r.id) is None

    def test_bandless_research_listed_only_until_it_has_materials(self, test_client, test_db):
        s = _create_suggestion(test_db, grade_bands=[])
        r = _create_research(test_db, s, status="APPROVED")
        assert self._row(test_client, r.id)["bands_to_generate"] == []

        _create_material(test_db, r, grade_band=None)
        assert self._row(test_client, r.id) is None
