"""Tests for the Instagram admin API endpoints."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models import ArticleSuggestion, Research
from app.models.instagram_post import InstagramPost

BASE = "/api/v1/admin/dashboard/instagram"


def _seed_post(test_db, **overrides):
    fields = {
        "post_type": "SABIAS_QUE",
        "template_variant": "AZUL",
        "status": "PENDING",
        "title": "Arco Chato",
        "fact": "el <b>Arco Chato</b> resistió?",
        "caption": "Un dato. #PanamaInContext",
        "image_query": "Arco Chato ruins",
    }
    fields.update(overrides)
    post = InstagramPost(**fields)
    test_db.add(post)
    test_db.commit()
    test_db.refresh(post)
    return post


class TestCrud:
    def test_list_newest_first_with_derived_state(self, test_db, test_client, admin_token):
        first = _seed_post(test_db)
        second = _seed_post(test_db, status="APPROVED", title="Second")

        resp = test_client.get(f"{BASE}/posts")
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["id"] for r in rows] == [second.id, first.id]
        assert rows[0]["derived_state"] == "APPROVED"
        assert rows[1]["derived_state"] == "DRAFT"
        assert rows[0]["has_image"] is False

    def test_detail(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.get(f"{BASE}/posts/{post.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["fact"] == "el <b>Arco Chato</b> resistió?"
        assert body["derived_state"] == "DRAFT"

    def test_detail_404(self, test_client, admin_token):
        assert test_client.get(f"{BASE}/posts/99999").status_code == 404

    def test_requires_admin_token(self, test_db, test_client):
        assert test_client.get(f"{BASE}/posts").status_code == 401

    def test_patch_content_sanitizes_fact(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.patch(
            f"{BASE}/posts/{post.id}",
            json={"fact": "<script>x</script> el <b>dato</b>?", "caption": "Nueva caption"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "<script>" not in body["fact"]
        assert "<b>dato</b>" in body["fact"]
        assert body["caption"] == "Nueva caption"

    def test_patch_media_pick_and_clear(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.patch(
            f"{BASE}/posts/{post.id}",
            json={"media_path": "https://cdn.example.com/x.jpg"},
        )
        assert resp.json()["media_path"] == "https://cdn.example.com/x.jpg"

        resp = test_client.patch(f"{BASE}/posts/{post.id}", json={"clear_media": True})
        assert resp.json()["media_path"] is None
        assert resp.json()["media_id"] is None

    def test_patch_caption_over_ig_limit_rejected(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.patch(f"{BASE}/posts/{post.id}", json={"caption": "x" * 2201})
        assert resp.status_code == 422

    def test_patch_unknown_variant_rejected(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.patch(f"{BASE}/posts/{post.id}", json={"template_variant": "VERDE"})
        assert resp.status_code == 400

    def test_patch_published_post_blocked(self, test_db, test_client, admin_token):
        post = _seed_post(test_db, published_at=datetime.now(UTC), ig_media_id="123")
        resp = test_client.patch(f"{BASE}/posts/{post.id}", json={"title": "New"})
        assert resp.status_code == 400

    def test_delete_draft_ok_published_blocked(self, test_db, test_client, admin_token):
        draft = _seed_post(test_db)
        live = _seed_post(test_db, published_at=datetime.now(UTC))

        assert test_client.delete(f"{BASE}/posts/{draft.id}").status_code == 200
        assert test_client.get(f"{BASE}/posts/{draft.id}").status_code == 404
        assert test_client.delete(f"{BASE}/posts/{live.id}").status_code == 400


class TestStatusTransitions:
    def test_valid_transition(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.patch(f"{BASE}/posts/{post.id}/status", json={"status": "APPROVED"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"

    def test_invalid_transition(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.patch(f"{BASE}/posts/{post.id}/status", json={"status": "PENDING"})
        assert resp.status_code == 400

    def test_leaving_approved_clears_schedule(self, test_db, test_client, admin_token):
        post = _seed_post(
            test_db,
            status="APPROVED",
            image_url="https://cdn.example.com/instagram/1/render-1.jpg",
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        resp = test_client.patch(f"{BASE}/posts/{post.id}/status", json={"status": "PENDING"})
        assert resp.status_code == 200
        test_db.refresh(post)
        assert post.scheduled_at is None


class TestScheduling:
    def test_schedule_requires_approved(self, test_db, test_client, admin_token):
        post = _seed_post(test_db, image_url="https://cdn.example.com/i.jpg")
        resp = test_client.patch(
            f"{BASE}/posts/{post.id}/schedule",
            json={"scheduled_at": "2027-01-01T12:00:00Z"},
        )
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()

    def test_schedule_requires_rendered_image(self, test_db, test_client, admin_token):
        post = _seed_post(test_db, status="APPROVED")
        resp = test_client.patch(
            f"{BASE}/posts/{post.id}/schedule",
            json={"scheduled_at": "2027-01-01T12:00:00Z"},
        )
        assert resp.status_code == 400
        assert "render" in resp.json()["detail"].lower()

    def test_schedule_sets_timestamp_and_clears_error(self, test_db, test_client, admin_token):
        post = _seed_post(
            test_db,
            status="APPROVED",
            image_url="https://cdn.example.com/i.jpg",
            publish_error="Graph API said no",
        )
        resp = test_client.patch(
            f"{BASE}/posts/{post.id}/schedule",
            json={"scheduled_at": "2027-01-01T12:00:00Z"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["derived_state"] == "SCHEDULED"
        assert body["publish_error"] is None
        assert body["scheduled_at"].startswith("2027-01-01T12:00:00")

    def test_unschedule(self, test_db, test_client, admin_token):
        post = _seed_post(
            test_db,
            status="APPROVED",
            image_url="https://cdn.example.com/i.jpg",
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )
        resp = test_client.patch(f"{BASE}/posts/{post.id}/unschedule")
        assert resp.status_code == 200
        assert resp.json()["derived_state"] == "APPROVED"
        assert resp.json()["scheduled_at"] is None

    def test_publish_now_503_when_unconfigured(self, test_db, test_client, admin_token):
        post = _seed_post(
            test_db, status="APPROVED", image_url="https://cdn.example.com/i.jpg"
        )
        resp = test_client.post(f"{BASE}/posts/{post.id}/publish-now")
        assert resp.status_code == 503

    def test_schedule_published_post_blocked(self, test_db, test_client, admin_token):
        post = _seed_post(
            test_db,
            status="APPROVED",
            image_url="https://cdn.example.com/i.jpg",
            published_at=datetime.now(UTC),
        )
        resp = test_client.patch(
            f"{BASE}/posts/{post.id}/schedule",
            json={"scheduled_at": "2027-01-01T12:00:00Z"},
        )
        assert resp.status_code == 400


class TestRenderEndpoint:
    @patch("app.api.instagram.render_instagram_post")
    def test_render_returns_url_and_photo_warning(
        self, mock_render, test_db, test_client, admin_token
    ):
        post = _seed_post(test_db)  # sabías-que without a picked photo

        def _fake_render(p):
            p.image_url = "https://cdn.example.com/instagram/1/render-9.jpg"
            return p.image_url

        mock_render.side_effect = _fake_render

        resp = test_client.post(f"{BASE}/posts/{post.id}/render")
        assert resp.status_code == 200
        body = resp.json()
        assert body["image_url"].endswith("render-9.jpg")
        assert body["photo_missing"] is True

    @patch("app.api.instagram.render_instagram_post")
    def test_render_published_blocked(self, mock_render, test_db, test_client, admin_token):
        post = _seed_post(test_db, published_at=datetime.now(UTC))
        assert test_client.post(f"{BASE}/posts/{post.id}/render").status_code == 400
        mock_render.assert_not_called()


class TestGenerateEndpoints:
    @patch("app.api.instagram.generate_sabias_que_posts")
    def test_generate_sabias_que_passes_through(
        self, mock_gen, test_db, test_client, admin_token, category_factory
    ):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="T",
            category_id=category.id,
            status="APPROVED",
            main_topic="T",
            sub_topics=[],
            point_of_view="narrative",
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="x", status="APPROVED")
        test_db.add(research)
        test_db.commit()

        mock_gen.return_value = [_seed_post(test_db, research_id=research.id)]
        resp = test_client.post(f"{BASE}/generate/sabias-que", json={"research_id": research.id})
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        mock_gen.assert_called_once()

    def test_promotable_articles_empty(self, test_db, test_client, admin_token):
        resp = test_client.get(f"{BASE}/promotable-articles")
        assert resp.status_code == 200
        assert resp.json() == []


class TestModelConstraints:
    def test_bad_post_type_rejected(self, test_db):
        import pytest
        from sqlalchemy.exc import IntegrityError

        test_db.add(InstagramPost(post_type="STORY", title="x"))
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()

    def test_bad_template_variant_rejected(self, test_db):
        import pytest
        from sqlalchemy.exc import IntegrityError

        test_db.add(InstagramPost(post_type="SABIAS_QUE", template_variant="VERDE", title="x"))
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()

    def test_null_variant_allowed_for_promo(self, test_db):
        test_db.add(InstagramPost(post_type="ARTICLE_PROMO", title="x"))
        test_db.flush()
