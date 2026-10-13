"""Tests for the Instagram Graph API publisher (mocked transport)."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app.models.instagram_post import InstagramPost
from app.services import instagram as instagram_module
from app.services.instagram import (
    InstagramNotConfiguredError,
    InstagramPublisher,
    InstagramPublishError,
    publish_post,
)

BASE = "https://graph.test/v23.0"


def _settings(token="tok-1", account="17840000"):
    return SimpleNamespace(
        instagram_access_token=token,
        instagram_account_id=account,
        instagram_graph_api_url=BASE,
        media_http_timeout=5.0,
        media_http_max_retries=1,
    )


def _make_publisher(handler, **settings_kwargs):
    """Publisher whose httpx client routes through a MockTransport."""
    with (
        patch.object(instagram_module, "get_settings", return_value=_settings(**settings_kwargs)),
        patch.object(
            instagram_module,
            "make_hardened_client",
            return_value=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
    ):
        return InstagramPublisher()


def _happy_handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "CONTAINER1"})
        if path.endswith("/CONTAINER1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "MEDIA9"})
        if path.endswith("/MEDIA9"):
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/xyz/"})
        return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})

    return handler


class TestPublishImage:
    def test_happy_path_container_publish_permalink(self):
        calls = []
        publisher = _make_publisher(_happy_handler(calls))

        result = publisher.publish_image("https://cdn.example.com/i.jpg", "Hola #PanamaInContext")

        assert result.ig_media_id == "MEDIA9"
        assert result.permalink == "https://www.instagram.com/p/xyz/"
        # container -> status poll -> publish -> permalink
        assert [c.url.path.rsplit("/", 1)[-1] for c in calls] == [
            "media",
            "CONTAINER1",
            "media_publish",
            "MEDIA9",
        ]
        body = calls[0].read().decode()
        assert "image_url=https%3A%2F%2Fcdn.example.com%2Fi.jpg" in body
        assert "access_token=tok-1" in body

    def test_graph_error_is_mapped_with_message_and_code(self):
        def handler(request):
            return httpx.Response(
                400, json={"error": {"message": "Invalid image URL", "code": 9004}}
            )

        publisher = _make_publisher(handler)
        with pytest.raises(InstagramPublishError, match=r"Invalid image URL \(code 9004\)"):
            publisher.publish_image("https://cdn.example.com/i.jpg", "x")

    def test_container_error_status_fails(self):
        def handler(request):
            if request.url.path.endswith("/media"):
                return httpx.Response(200, json={"id": "C1"})
            return httpx.Response(200, json={"status_code": "ERROR"})

        publisher = _make_publisher(handler)
        with pytest.raises(InstagramPublishError, match="status ERROR"):
            publisher.publish_image("https://cdn.example.com/i.jpg", "x")

    def test_permalink_failure_is_non_fatal(self):
        def handler(request):
            path = request.url.path
            if path.endswith("/media"):
                return httpx.Response(200, json={"id": "C1"})
            if path.endswith("/C1"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if path.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "M1"})
            return httpx.Response(500, json={"error": {"message": "boom"}})

        publisher = _make_publisher(handler)
        result = publisher.publish_image("https://cdn.example.com/i.jpg", "x")
        assert result.ig_media_id == "M1"
        assert result.permalink is None

    def test_disabled_without_credentials(self):
        publisher = _make_publisher(_happy_handler([]), token="", account="")
        assert publisher.enabled is False
        with pytest.raises(InstagramNotConfiguredError):
            publisher.publish_image("https://cdn.example.com/i.jpg", "x")


def _seed_post(test_db, **overrides):
    fields = {
        "post_type": "SABIAS_QUE",
        "status": "APPROVED",
        "title": "Post",
        "caption": "hola",
        "image_url": "https://cdn.example.com/i.jpg",
    }
    fields.update(overrides)
    post = InstagramPost(**fields)
    test_db.add(post)
    test_db.commit()
    test_db.refresh(post)
    return post


class TestPublishPost:
    def test_success_records_media_id_and_permalink(self, test_db):
        post = _seed_post(test_db, publishing_started_at=datetime.now(UTC))
        publisher = _make_publisher(_happy_handler([]))

        assert publish_post(test_db, post, publisher) is True
        test_db.refresh(post)
        assert post.published_at is not None
        assert post.ig_media_id == "MEDIA9"
        assert post.ig_permalink == "https://www.instagram.com/p/xyz/"
        assert post.publish_error is None
        assert post.publishing_started_at is None
        assert post.publish_attempts == 1
        assert post.derived_state == "PUBLISHED"

    def test_failure_parks_the_row(self, test_db):
        post = _seed_post(test_db, publishing_started_at=datetime.now(UTC))

        def handler(request):
            return httpx.Response(400, json={"error": {"message": "nope", "code": 1}})

        publisher = _make_publisher(handler)
        assert publish_post(test_db, post, publisher) is False
        test_db.refresh(post)
        assert post.published_at is None
        assert "nope" in post.publish_error
        assert post.publishing_started_at is None
        assert post.publish_attempts == 1
        assert post.derived_state == "FAILED"


class TestPublishNowEndpoint:
    BASE_URL = "/api/v1/admin/dashboard/instagram"

    def test_503_when_not_configured(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)
        resp = test_client.post(f"{self.BASE_URL}/posts/{post.id}/publish-now")
        assert resp.status_code == 503

    def test_publish_now_success(self, test_db, test_client, admin_token):
        post = _seed_post(test_db, publish_error="old failure")
        publisher = _make_publisher(_happy_handler([]))
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(f"{self.BASE_URL}/posts/{post.id}/publish-now")
        assert resp.status_code == 200
        body = resp.json()
        assert body["derived_state"] == "PUBLISHED"
        assert body["ig_permalink"] == "https://www.instagram.com/p/xyz/"
        assert body["publish_error"] is None

    def test_publish_now_failure_surfaces_parked_state(self, test_db, test_client, admin_token):
        post = _seed_post(test_db)

        def handler(request):
            return httpx.Response(
                400, json={"error": {"message": "token expired", "code": 190}}
            )

        publisher = _make_publisher(handler)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(f"{self.BASE_URL}/posts/{post.id}/publish-now")
        assert resp.status_code == 200
        body = resp.json()
        assert body["derived_state"] == "FAILED"
        assert "token expired" in body["publish_error"]


def test_graph_error_detail_handles_non_json():
    resp = httpx.Response(500, text="<html>bad gateway</html>", request=httpx.Request("GET", "x"))
    detail = instagram_module._graph_error_detail(resp)
    assert "500" in detail or "bad gateway" in detail


def test_error_payload_shape_used_by_meta():
    # Sanity: our parser reads the documented {"error": {"message", "code"}} shape.
    payload = {"error": {"message": "m", "code": 4, "error_subcode": 2207051}}
    resp = httpx.Response(400, text=json.dumps(payload), request=httpx.Request("GET", "x"))
    assert instagram_module._graph_error_detail(resp) == "m (code 4)"
