"""Tests for the admin dashboard endpoints in app/api/dashboard.py.

These tests cover the request/response contracts of every endpoint in the
admin dashboard router that wasn't already exercised by another test file
(test_article_generation.py, test_series_sections.py). They use the
canonical real-MagicLink ``admin_token`` fixture (no monkey-patched auth
shim) and mock every external service so no network calls escape the
container.

The oracle for each assertion is the matching Pydantic response_model in
``app/schemas/dashboard.py`` plus the endpoint's docstring; the source is
only consulted to enumerate the set of error preconditions, not to define
the shape of each successful response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import (
    AdminEmail,
    Media,
    PricingConfig,
    Research,
    Tag,
)

# ---------------------------------------------------------------------------
# Helpers (per-file; conftest.py is intentionally untouched per the brief)
# ---------------------------------------------------------------------------


def _seed_pricing(test_db):
    """Insert the canonical six pricing keys with deterministic values."""
    rows = [
        PricingConfig(key="base_fee", value="10.00", description="Base fee per tour"),
        PricingConfig(key="hourly_rate_pax_1", value="20.00"),
        PricingConfig(key="hourly_rate_pax_2_3", value="15.00"),
        PricingConfig(key="hourly_rate_pax_4_5", value="10.00"),
        PricingConfig(key="hourly_rate_pax_6_7", value="8.00"),
        PricingConfig(key="senior_discount_percent", value="20"),
    ]
    for r in rows:
        test_db.add(r)
    test_db.commit()
    return rows


def _seed_tag(test_db, name="Panama Canal", status="APPROVED"):
    now = datetime.now(UTC)
    tag = Tag(name=name, status=status, created_at=now, updated_at=now)
    test_db.add(tag)
    test_db.commit()
    test_db.refresh(tag)
    return tag


def _seed_media(test_db, filename="photo.jpg") -> Media:
    now = datetime.now(UTC)
    media = Media(
        filename=filename,
        original_filename=filename,
        file_path=f"/static/images/articles/{filename}",
        file_size=1234,
        mime_type="image/jpeg",
        media_type="IMAGE",
        source="GENERATED",
        title="Test media",
        created_at=now,
        updated_at=now,
    )
    test_db.add(media)
    test_db.commit()
    test_db.refresh(media)
    return media


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestDashboardStats:
    """GET /admin/dashboard/stats."""

    URL = "/api/v1/admin/dashboard/stats"

    def test_stats_empty_db(self, test_client, admin_token):
        response = test_client.get(f"{self.URL}?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        for key in (
            "bookings",
            "orders",
            "suggestions",
            "research",
            "articles",
            "instagram",
            "edu_suggestions",
            "edu_research",
            "edu_materials",
            "edu_materials_by_type",
        ):
            assert key in data
            assert data[key]["total"] == 0
            assert data[key]["by_status"] == {}
        assert data["edu_published_total"] == 0

    def test_stats_with_seeded_records(
        self, test_client, test_db, admin_token, suggestion_factory, research_factory
    ):
        # one suggestion APPROVED + one research APPROVED
        suggestion_factory(status="APPROVED")
        research_factory()

        response = test_client.get(f"{self.URL}?token={admin_token}")
        assert response.status_code == 200
        data = response.json()
        # research_factory creates its own suggestion internally → total 2
        assert data["suggestions"]["total"] == 2
        assert data["suggestions"]["by_status"]["APPROVED"] == 2
        assert data["research"]["total"] == 1
        assert data["research"]["by_status"]["APPROVED"] == 1

    def test_instagram_derived_state_buckets(self, test_client, test_db, admin_token):
        from datetime import timedelta

        from app.models.instagram_post import InstagramPost

        def post(**kw):
            fields = {"post_type": "SABIAS_QUE", "status": "PENDING", "title": "p"}
            fields.update(kw)
            test_db.add(InstagramPost(**fields))

        now = datetime.now(UTC)
        post()  # DRAFT (pending)
        post(status="REJECTED")  # DRAFT too — anything not approved
        post(status="APPROVED", image_url="u")  # APPROVED, unscheduled
        post(status="APPROVED", image_url="u", scheduled_at=now + timedelta(days=1))  # SCHEDULED
        post(  # FAILED beats scheduled
            status="APPROVED",
            image_url="u",
            scheduled_at=now - timedelta(hours=1),
            publish_error="boom",
        )
        post(  # PUBLISHED beats everything
            status="APPROVED",
            image_url="u",
            scheduled_at=now - timedelta(hours=2),
            published_at=now - timedelta(hours=1),
            ig_media_id="M1",
        )
        test_db.commit()

        data = test_client.get(f"{self.URL}?token={admin_token}").json()
        assert data["instagram"]["total"] == 6
        assert data["instagram"]["by_status"] == {
            "DRAFT": 2,
            "APPROVED": 1,
            "SCHEDULED": 1,
            "FAILED": 1,
            "PUBLISHED": 1,
        }

    def test_edu_pipeline_counts(self, test_client, test_db, admin_token):
        from datetime import timedelta

        from app.models import EduMaterial, EduResearch, EduSuggestion

        sug = EduSuggestion(title="Panamá Viejo", status="APPROVED")
        test_db.add(sug)
        test_db.flush()
        research = EduResearch(suggestion_id=sug.id, content="x", status="APPROVED")
        test_db.add(research)
        test_db.flush()
        now = datetime.now(UTC)
        test_db.add(
            EduMaterial(
                research_id=research.id,
                material_type="guide",
                grade_band="3rd-6th",
                title="g",
                status="APPROVED",
                published_at=now - timedelta(days=1),  # live on the portal
            )
        )
        test_db.add(
            EduMaterial(
                research_id=research.id,
                material_type="slides",
                grade_band="3rd-6th",
                title="s",
                status="APPROVED",
                published_at=now + timedelta(days=1),  # scheduled → not live yet
            )
        )
        test_db.add(
            EduMaterial(
                research_id=research.id,
                material_type="video",
                grade_band="3rd-6th",
                title="v",
                status="PENDING",
            )
        )
        test_db.commit()

        data = test_client.get(f"{self.URL}?token={admin_token}").json()
        assert data["edu_suggestions"]["by_status"] == {"APPROVED": 1}
        assert data["edu_research"]["by_status"] == {"APPROVED": 1}
        assert data["edu_materials"]["total"] == 3
        assert data["edu_materials"]["by_status"] == {"APPROVED": 2, "PENDING": 1}
        assert data["edu_materials_by_type"]["by_status"] == {
            "guide": 1,
            "slides": 1,
            "video": 1,
        }
        assert data["edu_published_total"] == 1

    def test_stats_requires_token(self, test_client):
        response = test_client.get(self.URL)
        assert response.status_code in (401, 422)

    def test_stats_rejects_invalid_token(self, test_client):
        response = test_client.get(f"{self.URL}?token=not-a-real-token")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


class TestDashboardSuggestions:
    """All /admin/dashboard/suggestions* endpoints."""

    LIST_URL = "/api/v1/admin/dashboard/suggestions"
    AVAIL_RESEARCH_URL = "/api/v1/admin/dashboard/suggestions/available-for-research"
    AVAIL_GEN_URL = "/api/v1/admin/dashboard/suggestions/available-for-generation"
    GENERATE_URL = "/api/v1/admin/dashboard/suggestions/generate"

    # --- list ---

    def test_list_empty(self, test_client, admin_token):
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_returns_seeded(self, test_client, admin_token, suggestion_factory):
        suggestion_factory(title="My Suggestion")
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        item = body[0]
        assert item["title"] == "My Suggestion"
        assert item["has_research"] is False
        assert item["articles_status"] == "NONE"
        assert item["category_name"] is not None
        assert item["taxonomy_name"] is not None

    # --- available-for-research ---

    def test_available_for_research_only_approved_without_research(
        self,
        test_client,
        admin_token,
        suggestion_factory,
        research_factory,
    ):
        # an APPROVED suggestion with no research
        bare = suggestion_factory(title="Bare", status="APPROVED")
        # an APPROVED suggestion that already has research → excluded
        with_research = suggestion_factory(title="WithResearch", status="APPROVED")
        research_factory(suggestion=with_research)
        # a PENDING suggestion → excluded
        suggestion_factory(title="Pending", status="PENDING")

        response = test_client.get(f"{self.AVAIL_RESEARCH_URL}?token={admin_token}")
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()]
        assert ids == [bare.id]

    # --- available-for-generation ---

    def test_available_for_generation_only_historical_panama_with_no_suggestions(
        self,
        test_client,
        admin_token,
        taxonomy_factory,
        category_factory,
        suggestion_factory,
    ):
        # Historical Panama category with no suggestions → eligible
        tax_hist = taxonomy_factory(name="Historical Panama")
        eligible = category_factory(name="Pre-Columbian", taxonomy=tax_hist)
        # Historical Panama category with a suggestion → excluded
        seeded = category_factory(name="Spanish Era", taxonomy=tax_hist)
        suggestion_factory(category=seeded)
        # Non-Historical category → excluded
        tax_other = taxonomy_factory(name="Geography")
        category_factory(name="Provinces", taxonomy=tax_other)

        response = test_client.get(f"{self.AVAIL_GEN_URL}?token={admin_token}")
        assert response.status_code == 200
        ids = [c["id"] for c in response.json()]
        assert ids == [eligible.id]

    # --- generate (POST) ---

    @patch("app.api.dashboard.generate_historical_suggestions")
    def test_generate_happy_path(
        self,
        mock_generate,
        test_client,
        admin_token,
        taxonomy_factory,
        category_factory,
    ):
        tax = taxonomy_factory(name="Historical Panama")
        category = category_factory(name="Pre-Columbian", taxonomy=tax)
        # mock returns objects exposing `.id`
        mock_generate.return_value = [MagicMock(id=11), MagicMock(id=12)]

        response = test_client.post(
            f"{self.GENERATE_URL}?token={admin_token}",
            json={"category_id": category.id, "num_suggestions": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["category_id"] == category.id
        assert body["category_name"] == "Pre-Columbian"
        assert body["suggestions_created"] == 2
        assert body["suggestion_ids"] == [11, 12]

    def test_generate_rejects_zero_num_suggestions(
        self, test_client, admin_token, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory(name="Historical Panama")
        category = category_factory(taxonomy=tax)
        response = test_client.post(
            f"{self.GENERATE_URL}?token={admin_token}",
            json={"category_id": category.id, "num_suggestions": 0},
        )
        assert response.status_code == 400
        assert "between 1 and 20" in response.json()["detail"]

    def test_generate_category_not_found(self, test_client, admin_token):
        response = test_client.post(
            f"{self.GENERATE_URL}?token={admin_token}",
            json={"category_id": 9_999_999, "num_suggestions": 5},
        )
        assert response.status_code == 404

    def test_generate_rejects_non_historical_taxonomy(
        self, test_client, admin_token, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory(name="Geography")
        category = category_factory(taxonomy=tax)
        response = test_client.post(
            f"{self.GENERATE_URL}?token={admin_token}",
            json={"category_id": category.id, "num_suggestions": 5},
        )
        assert response.status_code == 400
        assert "Historical Panama" in response.json()["detail"]

    # --- detail (GET /{id}) ---

    def test_detail_happy(self, test_client, admin_token, suggestion_factory):
        s = suggestion_factory(title="Detail Test", main_topic="x", sub_topics=["a", "b"])
        response = test_client.get(
            f"/api/v1/admin/dashboard/suggestions/{s.id}?token={admin_token}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == s.id
        assert body["title"] == "Detail Test"
        assert body["sub_topics"] == ["a", "b"]
        assert body["point_of_view"] == "narrative"

    def test_detail_404(self, test_client, admin_token):
        response = test_client.get(
            f"/api/v1/admin/dashboard/suggestions/9999999?token={admin_token}"
        )
        assert response.status_code == 404

    # --- update (PUT /{id}) ---

    def test_update_happy(self, test_client, test_db, admin_token, suggestion_factory):
        s = suggestion_factory(title="Old Title", main_topic="old topic")
        response = test_client.put(
            f"/api/v1/admin/dashboard/suggestions/{s.id}?token={admin_token}",
            json={
                "title": "New Title",
                "title_es": "Nuevo Título",
                "main_topic": "new topic",
                "sub_topics": ["x", "y"],
                "point_of_view": "analytical",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "New Title"
        assert body["title_es"] == "Nuevo Título"
        # DB side
        test_db.refresh(s)
        assert s.title == "New Title"
        assert s.point_of_view == "analytical"

    def test_update_404(self, test_client, admin_token):
        response = test_client.put(
            f"/api/v1/admin/dashboard/suggestions/9999999?token={admin_token}",
            json={
                "title": "X",
                "main_topic": "y",
                "sub_topics": [],
                "point_of_view": "narrative",
            },
        )
        assert response.status_code == 404

    # --- auth (once per resource group) ---

    def test_list_requires_token(self, test_client):
        response = test_client.get(self.LIST_URL)
        assert response.status_code in (401, 422)

    def test_list_rejects_invalid_token(self, test_client):
        response = test_client.get(f"{self.LIST_URL}?token=bogus")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestDashboardTags:
    """GET/POST /admin/dashboard/tags."""

    LIST_URL = "/api/v1/admin/dashboard/tags"

    def test_list_empty(self, test_client, admin_token):
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_with_seeded(self, test_client, test_db, admin_token):
        _seed_tag(test_db, name="Panama Canal")
        _seed_tag(test_db, name="History")
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        names = [t["name"] for t in response.json()]
        # ordered by name
        assert names == ["History", "Panama Canal"]

    def test_list_with_query(self, test_client, test_db, admin_token):
        _seed_tag(test_db, name="Panama Canal")
        _seed_tag(test_db, name="History")
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}&q=panama")
        assert response.status_code == 200
        names = [t["name"] for t in response.json()]
        assert names == ["Panama Canal"]

    def test_create_happy(self, test_client, test_db, admin_token):
        response = test_client.post(
            f"{self.LIST_URL}?token={admin_token}",
            json={"name": "Brand New Tag"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Brand New Tag"
        assert "id" in body
        # DB side
        assert test_db.query(Tag).filter(Tag.name == "Brand New Tag").one()

    def test_create_duplicate_case_insensitive(self, test_client, test_db, admin_token):
        _seed_tag(test_db, name="Already Here")
        response = test_client.post(
            f"{self.LIST_URL}?token={admin_token}",
            json={"name": "already here"},
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_create_requires_token(self, test_client):
        response = test_client.post(self.LIST_URL, json={"name": "X"})
        assert response.status_code in (401, 422)


# ---------------------------------------------------------------------------
# Settings: admin emails
# ---------------------------------------------------------------------------


class TestDashboardAdminEmails:
    """GET/POST/DELETE /admin/dashboard/settings/admin-emails."""

    URL = "/api/v1/admin/dashboard/settings/admin-emails"

    def test_list_empty(self, test_client, admin_token):
        response = test_client.get(f"{self.URL}?token={admin_token}")
        assert response.status_code == 200
        # The admin_token fixture seeds its own active allowlist entry (#56),
        # so the list is never truly empty — that fixture admin is the only row.
        assert {r["email"] for r in response.json()} == {"test-admin@example.com"}

    def test_list_with_seeded(self, test_client, test_db, admin_token):
        test_db.add(AdminEmail(email="a@example.com", name="A", is_active=True))
        test_db.add(AdminEmail(email="b@example.com", name=None, is_active=True))
        test_db.commit()
        response = test_client.get(f"{self.URL}?token={admin_token}")
        assert response.status_code == 200
        emails = {r["email"] for r in response.json()}
        # Includes the admin_token fixture's own allowlist entry (#56).
        assert emails == {"a@example.com", "b@example.com", "test-admin@example.com"}

    def test_create_happy(self, test_client, test_db, admin_token):
        response = test_client.post(
            f"{self.URL}?token={admin_token}",
            json={"email": "new@example.com", "name": "Newbie"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new@example.com"
        assert body["name"] == "Newbie"
        assert body["is_active"] is True
        assert test_db.query(AdminEmail).filter(AdminEmail.email == "new@example.com").one()

    def test_create_duplicate_case_insensitive(self, test_client, test_db, admin_token):
        test_db.add(AdminEmail(email="existing@example.com", is_active=True))
        test_db.commit()
        response = test_client.post(
            f"{self.URL}?token={admin_token}",
            json={"email": "EXISTING@example.com"},
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_delete_happy(self, test_client, test_db, admin_token):
        ae = AdminEmail(email="to-delete@example.com", is_active=True)
        test_db.add(ae)
        test_db.commit()
        test_db.refresh(ae)
        response = test_client.delete(f"{self.URL}/{ae.id}?token={admin_token}")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert test_db.query(AdminEmail).filter(AdminEmail.id == ae.id).first() is None

    def test_delete_404(self, test_client, admin_token):
        response = test_client.delete(f"{self.URL}/9999999?token={admin_token}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Settings: pricing
# ---------------------------------------------------------------------------


class TestDashboardPricing:
    """GET/PUT /admin/dashboard/settings/pricing."""

    URL = "/api/v1/admin/dashboard/settings/pricing"

    def test_get_empty(self, test_client, admin_token):
        response = test_client.get(f"{self.URL}?token={admin_token}")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_with_seeded(self, test_client, test_db, admin_token):
        _seed_pricing(test_db)
        response = test_client.get(f"{self.URL}?token={admin_token}")
        assert response.status_code == 200
        keys = {row["key"] for row in response.json()}
        assert keys == {
            "base_fee",
            "hourly_rate_pax_1",
            "hourly_rate_pax_2_3",
            "hourly_rate_pax_4_5",
            "hourly_rate_pax_6_7",
            "senior_discount_percent",
        }

    def test_put_updates_existing_and_inserts_new(self, test_client, test_db, admin_token):
        # seed only one existing key
        test_db.add(PricingConfig(key="base_fee", value="5.00", description="old fee"))
        test_db.commit()
        response = test_client.put(
            f"{self.URL}?token={admin_token}",
            json=[
                {"key": "base_fee", "value": "25.00"},
                {"key": "hourly_rate_pax_1", "value": "30.00"},
            ],
        )
        assert response.status_code == 200
        rows = {row["key"]: row["value"] for row in response.json()}
        assert rows["base_fee"] == "25.00"
        assert rows["hourly_rate_pax_1"] == "30.00"

    def test_put_rejects_invalid_key(self, test_client, admin_token):
        response = test_client.put(
            f"{self.URL}?token={admin_token}",
            json=[{"key": "not_a_pricing_key", "value": "1.00"}],
        )
        assert response.status_code == 400
        assert "Invalid pricing key" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


class TestDashboardResearch:
    """All /admin/dashboard/research* endpoints not covered elsewhere."""

    LIST_URL = "/api/v1/admin/dashboard/research"
    AVAIL_OUTLINES_URL = "/api/v1/admin/dashboard/research/available-for-outlines"
    GENERATE_PROMPT_URL = "/api/v1/admin/dashboard/research/generate-prompt"
    UPLOAD_URL = "/api/v1/admin/dashboard/research/upload"

    # --- list ---

    def test_list_empty(self, test_client, admin_token):
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_with_seeded(self, test_client, admin_token, research_factory):
        research_factory()
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        item = body[0]
        assert item["has_content"] is True
        assert item["has_summary"] is False
        assert item["status"] == "APPROVED"
        assert item["articles_status"] == "NONE"

    # --- available-for-outlines (next-only) ---

    def test_available_for_outlines_next_only(self, test_client, admin_token, research_factory):
        r1 = research_factory()
        r2 = research_factory()
        response = test_client.get(f"{self.AVAIL_OUTLINES_URL}?token={admin_token}")
        assert response.status_code == 200
        body = response.json()
        # endpoint returns the next (lowest suggestion_id) approved research only
        assert len(body) == 1
        assert body[0]["id"] in (r1.id, r2.id)

    # --- generate-prompt (POST) ---

    @patch("app.api.dashboard.generate_research_prompt")
    def test_generate_prompt_happy(self, mock_gen, test_client, admin_token, suggestion_factory):
        mock_gen.return_value = "Generated prompt text here."
        s = suggestion_factory(title="Prompt-able")
        response = test_client.post(
            f"{self.GENERATE_PROMPT_URL}?token={admin_token}",
            json={"suggestion_id": s.id},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["suggestion_id"] == s.id
        assert body["suggestion_title"] == "Prompt-able"
        assert body["prompt_text"] == "Generated prompt text here."

    def test_generate_prompt_suggestion_not_found(self, test_client, admin_token):
        response = test_client.post(
            f"{self.GENERATE_PROMPT_URL}?token={admin_token}",
            json={"suggestion_id": 9_999_999},
        )
        assert response.status_code == 404

    def test_generate_prompt_rejects_non_approved(
        self, test_client, admin_token, suggestion_factory
    ):
        s = suggestion_factory(status="PENDING")
        response = test_client.post(
            f"{self.GENERATE_PROMPT_URL}?token={admin_token}",
            json={"suggestion_id": s.id},
        )
        assert response.status_code == 400
        assert "APPROVED" in response.json()["detail"]

    def test_generate_prompt_rejects_when_research_exists(
        self, test_client, admin_token, suggestion_factory, research_factory
    ):
        s = suggestion_factory()
        research_factory(suggestion=s)
        response = test_client.post(
            f"{self.GENERATE_PROMPT_URL}?token={admin_token}",
            json={"suggestion_id": s.id},
        )
        assert response.status_code == 400
        assert "already has a research" in response.json()["detail"]

    # --- upload (POST) ---

    @patch("app.api.dashboard.validate_research_document")
    def test_upload_creates_new_research(
        self,
        mock_validate,
        test_client,
        test_db,
        admin_token,
        suggestion_factory,
    ):
        s = suggestion_factory(title="Matchable Title")
        mock_validate.return_value = {
            "valid": True,
            "checks": [],
            "word_count": 4321,
            "content": "# Matchable Title\n\nFinal content with refs.",
        }
        upload_bytes = b"# Matchable Title\n\nUploaded content"
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", upload_bytes, "text/markdown")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["suggestion_id"] == s.id
        assert body["word_count"] == 4321
        assert body["status"] == "PENDING"
        # DB side
        research = test_db.query(Research).filter(Research.suggestion_id == s.id).one()
        assert "Final content" in research.content

    @patch("app.api.dashboard.validate_research_document")
    def test_upload_updates_existing_pending_and_resets_status(
        self,
        mock_validate,
        test_client,
        test_db,
        admin_token,
        suggestion_factory,
        research_factory,
    ):
        s = suggestion_factory(title="Updateable")
        original = research_factory(suggestion=s, content="OLD", status="PENDING")
        mock_validate.return_value = {
            "valid": True,
            "checks": [],
            "word_count": 100,
            "content": "NEW CONTENT",
        }
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# Updateable\n", "text/markdown")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["research_id"] == original.id
        assert body["status"] == "PENDING"
        test_db.refresh(original)
        assert original.content == "NEW CONTENT"

    @patch("app.api.dashboard.validate_research_document")
    def test_upload_resets_rejected_status_to_pending(
        self,
        mock_validate,
        test_client,
        test_db,
        admin_token,
        suggestion_factory,
        research_factory,
    ):
        s = suggestion_factory()
        original = research_factory(suggestion=s, content="OLD", status="REJECTED")
        mock_validate.return_value = {
            "valid": True,
            "checks": [],
            "word_count": 100,
            "content": "NEW CONTENT",
        }
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# x\n", "text/markdown")},
        )
        assert response.status_code == 200
        test_db.refresh(original)
        assert original.status == "PENDING"

    def test_upload_unknown_suggestion_404(self, test_client, admin_token):
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": 9_999_999},
            files={"file": ("research.md", b"# Anything", "text/markdown")},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("app.api.dashboard.validate_research_document")
    def test_upload_over_approved_blocked_409(
        self,
        mock_validate,
        test_client,
        test_db,
        admin_token,
        suggestion_factory,
        research_factory,
    ):
        s = suggestion_factory()
        original = research_factory(suggestion=s, content="APPROVED CONTENT", status="APPROVED")
        mock_validate.return_value = {
            "valid": True,
            "checks": [],
            "word_count": 100,
            "content": "NEW CONTENT",
        }
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# x\n", "text/markdown")},
        )
        assert response.status_code == 409
        # Original content untouched
        test_db.refresh(original)
        assert original.content == "APPROVED CONTENT"
        assert original.status == "APPROVED"

    @patch("app.api.dashboard.validate_research_document")
    def test_upload_over_approved_with_force_overwrites_and_resets(
        self,
        mock_validate,
        test_client,
        test_db,
        admin_token,
        suggestion_factory,
        research_factory,
    ):
        s = suggestion_factory()
        original = research_factory(suggestion=s, content="APPROVED CONTENT", status="APPROVED")
        mock_validate.return_value = {
            "valid": True,
            "checks": [],
            "word_count": 100,
            "content": "FORCED NEW CONTENT",
        }
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}&force=true",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# x\n", "text/markdown")},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"
        test_db.refresh(original)
        assert original.content == "FORCED NEW CONTENT"
        assert original.status == "PENDING"

    def test_upload_non_utf8_400(self, test_client, admin_token, suggestion_factory):
        s = suggestion_factory()
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"\xff\xfe\x00bad", "text/markdown")},
        )
        assert response.status_code == 400
        assert "utf-8" in response.json()["detail"].lower()

    def test_upload_oversize_413(self, test_client, admin_token, suggestion_factory):
        s = suggestion_factory()
        from app.services.research_common import MAX_RESEARCH_UPLOAD_BYTES

        oversize = b"a" * (MAX_RESEARCH_UPLOAD_BYTES + 1)
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", oversize, "text/markdown")},
        )
        assert response.status_code == 413

    @patch("app.api.dashboard.validate_research_document")
    def test_upload_validation_failure_400(
        self,
        mock_validate,
        test_client,
        admin_token,
        suggestion_factory,
    ):
        s = suggestion_factory(title="Matchable Title")
        mock_validate.return_value = {
            "valid": False,
            "checks": [{"name": "word_count", "passed": False}],
            "word_count": 50,
            "content": "tiny",
        }
        response = test_client.post(
            f"{self.UPLOAD_URL}?token={admin_token}",
            data={"suggestion_id": s.id},
            files={"file": ("research.md", b"# Matchable Title\ntiny", "text/markdown")},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert isinstance(detail, dict)
        assert "failed quality checks" in detail["message"]
        assert detail["word_count"] == 50

    # --- detail (GET /{id}) ---

    def test_detail_happy(self, test_client, admin_token, research_factory):
        r = research_factory(content="# Heading\n\nbody")
        response = test_client.get(f"/api/v1/admin/dashboard/research/{r.id}?token={admin_token}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == r.id
        # markdown_to_html converts "# Heading" → "<h1 id=…>Heading</h1>"
        assert "<h1" in body["content_html"]
        assert "Heading" in body["content_html"]

    def test_detail_404(self, test_client, admin_token):
        response = test_client.get(f"/api/v1/admin/dashboard/research/9999999?token={admin_token}")
        assert response.status_code == 404

    # --- update (PUT /{id}) ---

    def test_update_happy(self, test_client, test_db, admin_token, research_factory):
        r = research_factory(content="# Old")
        response = test_client.put(
            f"/api/v1/admin/dashboard/research/{r.id}?token={admin_token}",
            json={"content_html": "<h1>Brand New Heading</h1><p>fresh</p>"},
        )
        assert response.status_code == 200
        test_db.refresh(r)
        assert "Brand New Heading" in r.content

    def test_update_404(self, test_client, admin_token):
        response = test_client.put(
            f"/api/v1/admin/dashboard/research/9999999?token={admin_token}",
            json={"content_html": "<p>x</p>"},
        )
        assert response.status_code == 404

    # --- download (GET /{id}/download) ---

    def test_download_happy(self, test_client, admin_token, research_factory):
        r = research_factory(content="# Markdown Body\n\nhello")
        response = test_client.get(
            f"/api/v1/admin/dashboard/research/{r.id}/download?token={admin_token}"
        )
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert "_research.md" in response.headers["content-disposition"]
        assert "# Markdown Body" in response.text

    def test_download_404(self, test_client, admin_token):
        response = test_client.get(
            f"/api/v1/admin/dashboard/research/9999999/download?token={admin_token}"
        )
        assert response.status_code == 404

    # --- generate-summary (POST) ---

    @patch("app.services.research_summary.generate_research_summary")
    def test_generate_summary_happy(
        self, mock_gen, test_client, test_db, admin_token, research_factory
    ):
        mock_gen.return_value = "an inline summary " * 10
        r = research_factory()
        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{r.id}/generate-summary?token={admin_token}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == r.id
        assert body["summary_length"] > 0
        test_db.refresh(r)
        assert r.summary is not None

    def test_generate_summary_404(self, test_client, admin_token):
        response = test_client.post(
            f"/api/v1/admin/dashboard/research/9999999/generate-summary?token={admin_token}"
        )
        assert response.status_code == 404

    def test_generate_summary_rejects_non_approved(
        self, test_client, admin_token, research_factory
    ):
        r = research_factory(status="PENDING")
        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{r.id}/generate-summary?token={admin_token}"
        )
        assert response.status_code == 400
        assert "approved" in response.json()["detail"].lower()

    def test_generate_summary_rejects_empty_content(
        self, test_client, admin_token, research_factory
    ):
        r = research_factory(content="   ")
        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{r.id}/generate-summary?token={admin_token}"
        )
        assert response.status_code == 400
        assert "no content" in response.json()["detail"].lower()

    @patch("app.api.dashboard.sentry_sdk.capture_exception")
    @patch("app.services.research_summary.generate_research_summary")
    def test_generate_summary_service_failure_502(
        self, mock_gen, mock_capture, test_client, admin_token, research_factory
    ):
        mock_gen.side_effect = RuntimeError("boom")
        r = research_factory()
        response = test_client.post(
            f"/api/v1/admin/dashboard/research/{r.id}/generate-summary?token={admin_token}"
        )
        assert response.status_code == 502
        assert "Summary generation failed" in response.json()["detail"]
        # The swallowed vendor failure must reach Sentry (it would otherwise be
        # invisible — see the endpoint's except block).
        mock_capture.assert_called_once()

    # --- download-summary (GET /{id}/download-summary) ---

    def test_download_summary_happy(self, test_client, test_db, admin_token, research_factory):
        r = research_factory()
        r.summary = "# Summary\n\nContent."
        test_db.commit()
        response = test_client.get(
            f"/api/v1/admin/dashboard/research/{r.id}/download-summary?token={admin_token}"
        )
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert "_summary.md" in response.headers["content-disposition"]
        assert "# Summary" in response.text

    def test_download_summary_research_404(self, test_client, admin_token):
        response = test_client.get(
            f"/api/v1/admin/dashboard/research/9999999/download-summary?token={admin_token}"
        )
        assert response.status_code == 404

    def test_download_summary_no_summary_404(self, test_client, admin_token, research_factory):
        r = research_factory()
        response = test_client.get(
            f"/api/v1/admin/dashboard/research/{r.id}/download-summary?token={admin_token}"
        )
        assert response.status_code == 404
        assert "summary" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------


def _seed_research_for_article(test_db, suggestion_factory) -> Research:
    """Create a real Research+Suggestion chain so list_articles joins resolve."""
    s = suggestion_factory()
    r = Research(suggestion_id=s.id, content="x" * 100, status="APPROVED")
    test_db.add(r)
    test_db.commit()
    test_db.refresh(r)
    return r


class TestDashboardArticles:
    """Article endpoints not already exercised by test_article_generation.py."""

    LIST_URL = "/api/v1/admin/dashboard/articles"
    AVAIL_WRITING_URL = "/api/v1/admin/dashboard/articles/available-for-writing"

    # --- list ---

    def test_list_empty(self, test_client, admin_token):
        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_computes_has_flags(
        self,
        test_client,
        test_db,
        admin_token,
        category_factory,
        suggestion_factory,
        article_factory,
    ):
        category = category_factory()
        r = _seed_research_for_article(test_db, suggestion_factory)
        a = article_factory(
            title="Full Article",
            content="some content",
            category=category,
            research_id=r.id,
            outline="## outline",
        )
        a.content_es = "spanish version"
        # add a tag so has_tags=True
        tag = _seed_tag(test_db, name="A Tag")
        a.tags.append(tag)
        test_db.commit()

        response = test_client.get(f"{self.LIST_URL}?token={admin_token}")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        item = items[0]
        assert item["title"] == "Full Article"
        assert item["has_content"] is True
        assert item["has_outline"] is True
        assert item["has_spanish"] is True
        assert item["has_tags"] is True
        assert item["has_feature_image"] is False
        assert item["is_series"] is False

    # --- available-for-writing ---

    def test_available_for_writing(
        self,
        test_client,
        test_db,
        admin_token,
        category_factory,
        suggestion_factory,
        article_factory,
    ):
        category = category_factory()
        r = _seed_research_for_article(test_db, suggestion_factory)
        # Eligible: outline_status=APPROVED + empty content
        eligible = article_factory(title="Ready", category=category, research_id=r.id, content="")
        eligible.outline_status = "APPROVED"
        # Ineligible: not approved outline status
        article_factory(
            title="NotApproved",
            category=category,
            research_id=r.id,
            content="",
        )
        # Ineligible: has content
        article_factory(title="HasContent", category=category, research_id=r.id, content="written")
        test_db.commit()
        response = test_client.get(f"{self.AVAIL_WRITING_URL}?token={admin_token}")
        assert response.status_code == 200
        ids = [a["id"] for a in response.json()]
        assert ids == [eligible.id]

    # --- outline-status (PATCH) ---

    def test_update_outline_status_happy(self, test_client, test_db, admin_token, article_factory):
        a = article_factory(outline="## outline")
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/outline-status?token={admin_token}",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"
        test_db.refresh(a)
        assert a.outline_status == "APPROVED"

    def test_update_outline_status_404(self, test_client, admin_token):
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/9999999/outline-status?token={admin_token}",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 404

    def test_update_outline_status_400_no_outline(self, test_client, admin_token, article_factory):
        a = article_factory(outline=None)
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/outline-status?token={admin_token}",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 400
        assert "outline" in response.json()["detail"].lower()

    # --- translate (POST) ---

    @patch("app.services.translation.get_translation_service")
    def test_translate_happy(
        self,
        mock_get_service,
        test_client,
        test_db,
        admin_token,
        article_factory,
    ):
        service = MagicMock()
        service.translate_text = AsyncMock(side_effect=lambda s: f"{s} ES")
        service.translate_markdown = AsyncMock(side_effect=lambda s: f"{s} ES")
        mock_get_service.return_value = service
        a = article_factory(content="English body content", title="Title Here")
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/translate?token={admin_token}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["article_id"] == a.id
        assert body["title_es"] == "Title Here ES"
        assert body["word_count"] > 0
        test_db.refresh(a)
        assert a.content_es == "English body content ES"

    def test_translate_404(self, test_client, admin_token):
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/9999999/translate?token={admin_token}"
        )
        assert response.status_code == 404

    def test_translate_no_content_400(self, test_client, admin_token, article_factory):
        a = article_factory(content="")
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/translate?token={admin_token}"
        )
        assert response.status_code == 400
        assert "no content" in response.json()["detail"].lower()

    def test_translate_already_translated_400(
        self, test_client, test_db, admin_token, article_factory
    ):
        a = article_factory(content="english")
        a.content_es = "already translated"
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/translate?token={admin_token}"
        )
        assert response.status_code == 400
        assert "already has Spanish" in response.json()["detail"]

    @patch("app.services.translation.get_translation_service")
    def test_translate_service_unavailable_500(
        self, mock_get_service, test_client, admin_token, article_factory
    ):
        mock_get_service.return_value = None
        a = article_factory(content="english body")
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/translate?token={admin_token}"
        )
        assert response.status_code == 500
        assert "unavailable" in response.json()["detail"].lower()

    # --- publish (PATCH) ---

    def _ready_to_publish_article(
        self, test_db, category_factory, article_factory, with_image=True
    ):
        category = category_factory()
        a = article_factory(category=category, content="english")
        a.content_es = "spanish"
        if with_image:
            media = _seed_media(test_db)
            a.feature_image_id = media.id
        test_db.commit()
        test_db.refresh(a)
        return a

    def test_publish_happy(
        self, test_client, test_db, admin_token, category_factory, article_factory
    ):
        a = self._ready_to_publish_article(test_db, category_factory, article_factory)
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/publish?token={admin_token}",
            json={"published_at": "2026-01-01T00:00:00"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == a.id
        assert body["status"] == "APPROVED"

    def test_publish_404(self, test_client, admin_token):
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/9999999/publish?token={admin_token}",
            json={"published_at": "2026-01-01T00:00:00"},
        )
        assert response.status_code == 404

    def test_publish_400_not_approved(
        self,
        test_client,
        test_db,
        admin_token,
        category_factory,
        article_factory,
    ):
        a = self._ready_to_publish_article(test_db, category_factory, article_factory)
        a.status = "PENDING"
        test_db.commit()
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/publish?token={admin_token}",
            json={"published_at": "2026-01-01T00:00:00"},
        )
        assert response.status_code == 400
        assert "APPROVED" in response.json()["detail"]

    def test_publish_400_missing_spanish(
        self,
        test_client,
        test_db,
        admin_token,
        category_factory,
        article_factory,
    ):
        a = self._ready_to_publish_article(test_db, category_factory, article_factory)
        a.content_es = ""
        test_db.commit()
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/publish?token={admin_token}",
            json={"published_at": "2026-01-01T00:00:00"},
        )
        assert response.status_code == 400
        assert "Spanish" in response.json()["detail"]

    def test_publish_400_missing_feature_image(
        self,
        test_client,
        test_db,
        admin_token,
        category_factory,
        article_factory,
    ):
        a = self._ready_to_publish_article(
            test_db, category_factory, article_factory, with_image=False
        )
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/publish?token={admin_token}",
            json={"published_at": "2026-01-01T00:00:00"},
        )
        assert response.status_code == 400
        assert "feature image" in response.json()["detail"]

    def test_publish_400_series_missing_sections(
        self,
        test_client,
        test_db,
        admin_token,
        category_factory,
        article_factory,
    ):
        category = category_factory()
        # parent has no children for this test, so we use the parent_id trick:
        parent = article_factory(title="Parent", category=category, content="parent")
        child = article_factory(title="Child", category=category, content="english body")
        child.series_parent_id = parent.id
        child.content_es = "spanish body"
        media = _seed_media(test_db)
        child.feature_image_id = media.id
        test_db.commit()
        # child is a series article without ABOUT_MARKER_EN in content
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{child.id}/publish?token={admin_token}",
            json={"published_at": "2026-01-01T00:00:00"},
        )
        assert response.status_code == 400
        assert "series sections" in response.json()["detail"]

    # --- unpublish (PATCH) ---

    def test_unpublish_happy(self, test_client, test_db, admin_token, article_factory):
        a = article_factory()
        assert a.published_at is not None
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/{a.id}/unpublish?token={admin_token}"
        )
        assert response.status_code == 200
        test_db.refresh(a)
        assert a.published_at is None

    def test_unpublish_404(self, test_client, admin_token):
        response = test_client.patch(
            f"/api/v1/admin/dashboard/articles/9999999/unpublish?token={admin_token}"
        )
        assert response.status_code == 404

    # --- detail (GET /{id}) ---

    def test_detail_happy(self, test_client, admin_token, article_factory):
        a = article_factory(content="# Heading\n\nbody")
        response = test_client.get(f"/api/v1/admin/dashboard/articles/{a.id}?token={admin_token}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == a.id
        assert "<h1" in body["content_html"]
        assert "Heading" in body["content_html"]

    def test_detail_404(self, test_client, admin_token):
        response = test_client.get(f"/api/v1/admin/dashboard/articles/9999999?token={admin_token}")
        assert response.status_code == 404

    # --- update (PUT /{id}) ---

    def test_update_happy_clears_tags(self, test_client, test_db, admin_token, article_factory):
        a = article_factory()
        tag = _seed_tag(test_db, name="Pre-existing")
        a.tags.append(tag)
        test_db.commit()

        response = test_client.put(
            f"/api/v1/admin/dashboard/articles/{a.id}?token={admin_token}",
            json={
                "title": "Updated Title",
                "excerpt": "new excerpt",
                "content_html": "<p>updated body</p>",
                "tag_ids": [],
            },
        )
        assert response.status_code == 200
        test_db.refresh(a)
        assert a.title == "Updated Title"
        assert a.tags == []

    def test_update_404(self, test_client, admin_token):
        response = test_client.put(
            f"/api/v1/admin/dashboard/articles/9999999?token={admin_token}",
            json={"title": "x", "content_html": "<p>x</p>"},
        )
        assert response.status_code == 404

    # --- download (GET /{id}/download) ---

    def test_download_en_happy(self, test_client, admin_token, article_factory):
        a = article_factory(content="# English\n\nbody", title="English Title")
        response = test_client.get(
            f"/api/v1/admin/dashboard/articles/{a.id}/download?token={admin_token}&lang=en"
        )
        assert response.status_code == 200
        assert "_article.md" in response.headers["content-disposition"]
        assert "# English" in response.text

    def test_download_es_404_without_spanish(self, test_client, admin_token, article_factory):
        a = article_factory(content="english only")
        response = test_client.get(
            f"/api/v1/admin/dashboard/articles/{a.id}/download?token={admin_token}&lang=es"
        )
        assert response.status_code == 404
        assert "Spanish" in response.json()["detail"]

    def test_download_es_happy(self, test_client, test_db, admin_token, article_factory):
        a = article_factory(content="english")
        a.content_es = "spanish body"
        a.title_es = "Título"
        test_db.commit()
        response = test_client.get(
            f"/api/v1/admin/dashboard/articles/{a.id}/download?token={admin_token}&lang=es"
        )
        assert response.status_code == 200
        assert "spanish body" in response.text

    def test_download_404(self, test_client, admin_token):
        response = test_client.get(
            f"/api/v1/admin/dashboard/articles/9999999/download?token={admin_token}"
        )
        assert response.status_code == 404

    # --- image-prompt (POST) ---

    @patch("app.services.image_prompt.generate_image_prompt")
    def test_image_prompt_happy(self, mock_gen, test_client, admin_token, article_factory):
        mock_gen.return_value = "A watercolor depicting…"
        a = article_factory(content="some content here", title="Pic Article")
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/image-prompt?token={admin_token}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["article_id"] == a.id
        assert body["title"] == "Pic Article"
        assert body["prompt_text"] == "A watercolor depicting…"

    def test_image_prompt_404(self, test_client, admin_token):
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/9999999/image-prompt?token={admin_token}"
        )
        assert response.status_code == 404

    def test_image_prompt_400_no_content(self, test_client, admin_token, article_factory):
        a = article_factory(content="")
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/image-prompt?token={admin_token}"
        )
        assert response.status_code == 400
        assert "no content" in response.json()["detail"].lower()

    # --- upload-image (POST) ---

    def test_upload_image_happy(
        self,
        test_client,
        test_db,
        admin_token,
        article_factory,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setattr("app.api.dashboard.IMAGES_DIR", tmp_path)
        a = article_factory()
        # 1x1 PNG (smallest valid png header)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/upload-image?token={admin_token}",
            files={"file": ("photo.png", png_bytes, "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["article_id"] == a.id
        assert body["media_id"] > 0
        assert body["file_path"].endswith(".png")
        # file was actually written
        assert (tmp_path / f"article_{a.id}.png").exists()

    def test_upload_image_404(self, test_client, admin_token, monkeypatch, tmp_path):
        monkeypatch.setattr("app.api.dashboard.IMAGES_DIR", tmp_path)
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/9999999/upload-image?token={admin_token}",
            files={"file": ("photo.png", b"\x89PNG\r\n", "image/png")},
        )
        assert response.status_code == 404

    def test_upload_image_400_invalid_content_type(
        self, test_client, admin_token, article_factory, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("app.api.dashboard.IMAGES_DIR", tmp_path)
        a = article_factory()
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/upload-image?token={admin_token}",
            files={"file": ("bad.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 400
        assert "Invalid file type" in response.json()["detail"]

    # --- assign-media (POST) ---

    def test_assign_media_happy(self, test_client, test_db, admin_token, article_factory):
        a = article_factory()
        m = _seed_media(test_db)
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/assign-media?token={admin_token}",
            json={"media_id": m.id},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["article_id"] == a.id
        assert body["media_id"] == m.id
        test_db.refresh(a)
        assert a.feature_image_id == m.id

    def test_assign_media_article_404(self, test_client, test_db, admin_token):
        m = _seed_media(test_db)
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/9999999/assign-media?token={admin_token}",
            json={"media_id": m.id},
        )
        assert response.status_code == 404

    def test_assign_media_media_404(self, test_client, admin_token, article_factory):
        a = article_factory()
        response = test_client.post(
            f"/api/v1/admin/dashboard/articles/{a.id}/assign-media?token={admin_token}",
            json={"media_id": 9_999_999},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Generic status transitions
# ---------------------------------------------------------------------------


class TestDashboardStatusTransitions:
    """PATCH /admin/dashboard/{entity}/{id}/status."""

    def _url(self, entity, entity_id):
        return f"/api/v1/admin/dashboard/{entity}/{entity_id}/status"

    def test_invalid_entity_400(self, test_client, admin_token):
        response = test_client.patch(
            f"{self._url('nope', 1)}?token={admin_token}",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 400
        assert "Invalid entity" in response.json()["detail"]

    def test_entity_not_found_404(self, test_client, admin_token):
        response = test_client.patch(
            f"{self._url('suggestions', 9999999)}?token={admin_token}",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 404

    def test_invalid_transition_400(self, test_client, admin_token, suggestion_factory):
        s = suggestion_factory(status="PENDING")
        # PENDING → PENDING is not allowed
        response = test_client.patch(
            f"{self._url('suggestions', s.id)}?token={admin_token}",
            json={"status": "PENDING"},
        )
        assert response.status_code == 400
        assert "Invalid transition" in response.json()["detail"]

    def test_approve_suggestion_sets_approved_at(
        self, test_client, test_db, admin_token, suggestion_factory
    ):
        s = suggestion_factory(status="PENDING")
        response = test_client.patch(
            f"{self._url('suggestions', s.id)}?token={admin_token}",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 200
        test_db.refresh(s)
        assert s.status == "APPROVED"
        assert s.approved_at is not None

    def test_research_status_transition(self, test_client, admin_token, research_factory):
        # research_factory defaults to APPROVED; transition to REJECTED is valid
        r = research_factory()
        response = test_client.patch(
            f"{self._url('research', r.id)}?token={admin_token}",
            json={"status": "REJECTED"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"

    def test_article_status_transition(self, test_client, admin_token, article_factory):
        a = article_factory(status="APPROVED")
        response = test_client.patch(
            f"{self._url('articles', a.id)}?token={admin_token}",
            json={"status": "PENDING"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"

    def test_approved_to_rejected_clears_approved_at(
        self, test_client, test_db, admin_token, suggestion_factory
    ):
        s = suggestion_factory(status="APPROVED")
        s.approved_at = datetime.now(UTC)
        test_db.commit()
        response = test_client.patch(
            f"{self._url('suggestions', s.id)}?token={admin_token}",
            json={"status": "REJECTED"},
        )
        assert response.status_code == 200
        test_db.refresh(s)
        assert s.status == "REJECTED"
        assert s.approved_at is None
