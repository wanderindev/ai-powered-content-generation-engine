"""Tests for the Instagram Graph API publisher (mocked transport)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app.models.instagram_post import InstagramPost
from app.services.http_client import SSRFError
from app.services import instagram as instagram_module
from app.services.instagram import (
    IgTokenInfo,
    InstagramNotConfiguredError,
    InstagramPublisher,
    InstagramPublishError,
    InstagramPublishingDisabledError,
    publish_post,
    warn_if_token_expiring,
)

BASE = "https://graph.test/v23.0"


def _settings(token="tok-1", account="17840000", publishing=True):
    return SimpleNamespace(
        instagram_access_token=token,
        instagram_account_id=account,
        instagram_publishing_enabled=publishing,
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
        # #576: the token rides in the Authorization header, never the URL or
        # body (Sentry's httpx breadcrumbs record the query string).
        for call in calls:
            assert call.headers["Authorization"] == "Bearer tok-1"
            assert "tok-1" not in str(call.url)
            assert "tok-1" not in call.read().decode()

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
            return httpx.Response(400, json={"error": {"message": "token expired", "code": 190}})

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


# --- Publishing kill switch (#576) -------------------------------------------


class TestPublishingKillSwitch:
    BASE_URL = "/api/v1/admin/dashboard/instagram"

    def test_switch_defaults_off(self):
        from app.core.config import Settings

        assert Settings.model_fields["instagram_publishing_enabled"].default is False

    def test_publish_image_refuses_before_any_http_call(self):
        calls = []
        publisher = _make_publisher(_happy_handler(calls), publishing=False)
        assert publisher.enabled is True
        assert publisher.publishing_enabled is False
        with pytest.raises(InstagramPublishingDisabledError):
            publisher.publish_image("https://cdn.example.com/i.jpg", "x")
        assert calls == []

    def test_publish_now_409_with_credentials_and_switch_off(
        self, test_db, test_client, admin_token
    ):
        scheduled = datetime(2027, 1, 1, 17, tzinfo=UTC)
        post = _seed_post(test_db, scheduled_at=scheduled)
        calls = []
        publisher = _make_publisher(_happy_handler(calls), publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(f"{self.BASE_URL}/posts/{post.id}/publish-now")
        assert resp.status_code == 409
        assert "turned off" in resp.json()["detail"]
        assert calls == []  # nothing reached the Graph API
        test_db.refresh(post)
        assert post.published_at is None
        assert post.publish_error is None
        assert post.publishing_started_at is None
        assert post.derived_state == "SCHEDULED"

    def test_publishing_status_endpoint(self, test_client, admin_token):
        publisher = _make_publisher(_happy_handler([]), publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.get(f"{self.BASE_URL}/publishing-status")
        assert resp.status_code == 200
        assert resp.json() == {"configured": True, "publishing_enabled": False}


# --- Verify: exercises the live integration, never posts (#576) --------------

_FAR_EXPIRY = int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())


def _verify_handler(
    calls,
    *,
    username="panamaincontextbydiego",
    expires_at=_FAR_EXPIRY,
    scopes=("instagram_basic", "instagram_content_publish"),
    valid=True,
):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/debug_token"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "is_valid": valid,
                        "expires_at": expires_at,
                        "scopes": list(scopes),
                    }
                },
            )
        if path.endswith("/17840000") and request.method == "GET":
            return httpx.Response(200, json={"username": username, "id": "17840000"})
        if path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"id": "CONTAINER1"})
        if path.endswith("/CONTAINER1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/media_publish"):
            raise AssertionError("verify must never call media_publish")
        return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})

    return handler


class TestVerifyEndpoint:
    URL = "/api/v1/admin/dashboard/instagram/verify"

    def test_with_post_creates_container_and_never_publishes(
        self, test_db, test_client, admin_token
    ):
        post = _seed_post(test_db, caption="Hola")
        calls = []
        publisher = _make_publisher(_verify_handler(calls), publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(self.URL, json={"post_id": post.id})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["published"] is False
        assert body["publishing_enabled"] is False
        steps = {s["name"]: s for s in body["steps"]}
        assert steps["account"]["data"]["username"] == "panamaincontextbydiego"
        assert steps["token"]["ok"] is True
        assert steps["token"]["data"]["expires_at"].startswith("2099-01-01")
        assert steps["token"]["data"]["has_publish_scope"] is True
        assert steps["container"]["ok"] is True
        assert steps["container"]["data"]["container_id"] == "CONTAINER1"

        paths = [c.url.path for c in calls]
        assert not any(p.endswith("/media_publish") for p in paths)
        # Only debug_token's required input_token puts the token in a URL
        # (scrubbed from Sentry breadcrumbs — see test_observability).
        for call in calls:
            assert call.headers["Authorization"] == "Bearer tok-1"
            if not call.url.path.endswith("/debug_token"):
                assert "tok-1" not in str(call.url)
        assert any(p.endswith("/media") for p in paths)
        container_body = next(c for c in calls if c.url.path.endswith("/media")).read().decode()
        assert "image_url=https%3A%2F%2Fcdn.example.com%2Fi.jpg" in container_body
        # The row is untouched: verify is not a publish. (The endpoint closed
        # the shared test session to release its connection — re-query.)
        fresh = test_db.get(InstagramPost, post.id)
        assert fresh.published_at is None
        assert fresh.publish_attempts in (0, None)

    def test_without_post_skips_the_container(self, test_client, admin_token):
        calls = []
        publisher = _make_publisher(_verify_handler(calls), publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(self.URL)
        assert resp.status_code == 200
        assert [s["name"] for s in resp.json()["steps"]] == ["account", "token"]
        assert not any(c.method == "POST" for c in calls)

    def test_wrong_account_and_missing_scope_fail_their_steps(self, test_client, admin_token):
        publisher = _make_publisher(
            _verify_handler([], username="someoneelse", scopes=("instagram_basic",)),
            publishing=False,
        )
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            body = test_client.post(self.URL).json()
        steps = {s["name"]: s for s in body["steps"]}
        assert body["ok"] is False
        assert steps["account"]["ok"] is False
        assert "expected @panamaincontextbydiego" in steps["account"]["detail"]
        assert steps["token"]["ok"] is False
        assert "instagram_content_publish" in steps["token"]["detail"]

    def test_expiring_token_fails_the_step_and_warns_sentry(self, test_client, admin_token):
        soon = int((datetime.now(UTC) + timedelta(days=3)).timestamp())
        publisher = _make_publisher(_verify_handler([], expires_at=soon), publishing=False)
        with (
            patch("app.api.instagram.InstagramPublisher", return_value=publisher),
            patch.object(instagram_module.sentry_sdk, "capture_message") as capture,
        ):
            body = test_client.post(self.URL).json()
        token = {s["name"]: s for s in body["steps"]}["token"]
        assert token["ok"] is False
        assert 2.5 < token["data"]["days_remaining"] < 3.5
        capture.assert_called_once()
        assert "expires soon" in capture.call_args.args[0]

    def test_unrendered_post_is_400(self, test_db, test_client, admin_token):
        post = _seed_post(test_db, image_url=None)
        publisher = _make_publisher(_verify_handler([]), publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(self.URL, json={"post_id": post.id})
        assert resp.status_code == 400

    def test_503_without_credentials(self, test_client, admin_token):
        resp = test_client.post(self.URL)
        assert resp.status_code == 503


class TestTokenExpiryWarning:
    def _info(self, days, valid=True):
        expires = None if days is None else datetime.now(UTC) + timedelta(days=days)
        return IgTokenInfo(is_valid=valid, expires_at=expires, scopes=())

    @pytest.mark.parametrize(
        ("days", "valid", "warned"),
        [
            (3, True, True),
            (6.9, True, True),
            (30, True, False),
            (None, True, False),
            (30, False, True),
        ],
        ids=["3d", "just-under", "30d", "never-expires", "invalid"],
    )
    def test_warns_below_threshold_or_invalid(self, days, valid, warned):
        with patch.object(instagram_module.sentry_sdk, "capture_message") as capture:
            assert warn_if_token_expiring(self._info(days, valid), 7) is warned
        assert capture.called is warned

    def test_debug_token_parsing(self):
        calls = []
        publisher = _make_publisher(_verify_handler(calls, expires_at=0))
        info = publisher.fetch_token_info()
        assert info.is_valid is True
        assert info.expires_at is None  # 0 = never expires
        assert "instagram_content_publish" in info.scopes
        assert calls[0].url.params["input_token"] == "tok-1"



class TestNonGraphFailures:
    """#576 review: every failure becomes an InstagramPublishError without the
    token in its message or a chained token-bearing exception."""

    URL = "/api/v1/admin/dashboard/instagram/verify"

    def test_ssrf_error_is_wrapped_without_chaining(self):
        def handler(request):
            raise SSRFError(f"Could not resolve host for {request.url}")

        publisher = _make_publisher(handler)
        with pytest.raises(InstagramPublishError) as excinfo:
            publisher.fetch_token_info()
        assert "tok-1" not in str(excinfo.value)
        assert "SSRFError" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None

    def test_non_json_body_is_wrapped(self):
        publisher = _make_publisher(lambda request: httpx.Response(200, text="<html/>"))
        with pytest.raises(InstagramPublishError, match="non-JSON"):
            publisher.fetch_username()

    @pytest.mark.parametrize("failure", ["ssrf", "non_json", "runtime"])
    def test_verify_reports_failed_steps_instead_of_500(
        self, test_db, test_client, admin_token, failure
    ):
        post = _seed_post(test_db)

        def handler(request):
            if failure == "ssrf":
                raise SSRFError("Could not resolve host")
            if failure == "runtime":
                raise RuntimeError("tok-1 leaked?")
            return httpx.Response(200, text="<html>nope</html>")

        publisher = _make_publisher(handler, publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            resp = test_client.post(self.URL, json={"post_id": post.id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert [s["name"] for s in body["steps"]] == ["account", "token", "container"]
        assert all(not s["ok"] for s in body["steps"])
        assert "tok-1" not in resp.text

    def test_debug_token_refused_falls_back_to_permissions(self, test_client, admin_token):
        def handler(request):
            path = request.url.path
            if path.endswith("/debug_token"):
                return httpx.Response(
                    400,
                    json={"error": {"message": "You must provide an app access token", "code": 100}},
                )
            if path.endswith("/me/permissions"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"permission": "instagram_content_publish", "status": "granted"},
                            {"permission": "pages_show_list", "status": "declined"},
                        ]
                    },
                )
            return httpx.Response(200, json={"username": "panamaincontextbydiego"})

        publisher = _make_publisher(handler, publishing=False)
        with patch("app.api.instagram.InstagramPublisher", return_value=publisher):
            body = test_client.post(self.URL).json()
        token = {s["name"]: s for s in body["steps"]}["token"]
        assert token["ok"] is True
        assert token["data"]["expiry_known"] is False
        assert token["data"]["has_publish_scope"] is True
        assert "expiry unknown" in token["detail"]
