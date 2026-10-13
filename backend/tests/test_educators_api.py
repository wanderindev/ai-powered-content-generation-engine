"""
Tests for the educator API endpoints and the public-media access gate.

The state-machine logic lives in test_educator_service. Here we cover the thin
API layer (status mapping, 502 error branches) plus the end-to-end gate: that
``require_educator_access`` actually blocks unauthenticated public-media calls
(#50) and that a real issued token unlocks them.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.models.educator import Educator


def _make_educator(db, **kwargs) -> Educator:
    defaults = dict(status="PENDING", language="es", source="media_library")
    defaults.update(kwargs)
    educator = Educator(**defaults)
    db.add(educator)
    db.commit()
    db.refresh(educator)
    return educator


# ---------------------------------------------------------------------------
# /educators/access
# ---------------------------------------------------------------------------


class TestAccessEndpoint:
    def test_unknown_email_not_granted(self, test_client):
        resp = test_client.post("/api/v1/educators/access", json={"email": "ghost@example.com"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is False
        assert body["status"] == "not_registered"
        assert body["token"] is None

    def test_registered_email_granted(self, test_client, test_db):
        _make_educator(test_db, email="known@example.com", status="PENDING")
        resp = test_client.post("/api/v1/educators/access", json={"email": "known@example.com"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is True
        assert body["token"]
        assert body["expires_at"]

    def test_unexpected_error_returns_502(self, test_client):
        with patch(
            "app.services.educator_service.access", side_effect=RuntimeError("boom")
        ):
            resp = test_client.post("/api/v1/educators/access", json={"email": "x@example.com"})
        assert resp.status_code == 502

    def test_invalid_email_rejected_by_schema(self, test_client):
        resp = test_client.post("/api/v1/educators/access", json={"email": "not-an-email"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /educators/signup
# ---------------------------------------------------------------------------


class TestSignupEndpoint:
    def test_new_signup_grants_access(self, test_client):
        resp = test_client.post(
            "/api/v1/educators/signup",
            json={"email": "api-new@example.com", "language": "en"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted"] is True
        assert body["token"]

    def test_signup_with_mailing_list_sends_confirmation(self, test_client):
        with patch("app.services.mailing_list._send_confirmation_email") as send:
            resp = test_client.post(
                "/api/v1/educators/signup",
                json={"email": "api-list@example.com", "mailing_list": True},
            )
        assert resp.status_code == 200
        assert resp.json()["mailing_list_pending"] is True
        send.assert_called_once()

    def test_stores_normalized_email(self, test_db, test_client):
        resp = test_client.post(
            "/api/v1/educators/signup",
            json={"email": "  Api.Norm@Example.COM ", "language": "en"},
        )
        assert resp.status_code == 200
        row = test_db.query(Educator).filter(Educator.email == "api.norm@example.com").one()
        assert row.email == "api.norm@example.com"

    def test_unexpected_error_returns_502(self, test_client):
        with patch(
            "app.services.educator_service.signup", side_effect=RuntimeError("boom")
        ):
            resp = test_client.post("/api/v1/educators/signup", json={"email": "b@example.com"})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# /educators/session
# ---------------------------------------------------------------------------


class TestSessionEndpoint:
    def test_valid_token(self, test_client, test_db):
        _make_educator(
            test_db,
            email="sess@example.com",
            access_token="sess-tok",
            access_expires_at=datetime.now(UTC) + timedelta(days=2),
        )
        resp = test_client.post("/api/v1/educators/session", json={"token": "sess-tok"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_invalid_token(self, test_client):
        resp = test_client.post("/api/v1/educators/session", json={"token": "nope"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is False


# ---------------------------------------------------------------------------
# /educators/unsubscribe (token-authenticated — #51)
# ---------------------------------------------------------------------------


class TestUnsubscribeEndpoint:
    def test_valid_token_unsubscribes(self, test_client, test_db):
        _make_educator(
            test_db,
            email="unsub@example.com",
            access_token="unsub-tok",
            access_expires_at=datetime.now(UTC) + timedelta(days=2),
        )
        resp = test_client.post("/api/v1/educators/unsubscribe", json={"token": "unsub-tok"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_unknown_token_returns_failure_not_error(self, test_client):
        resp = test_client.post("/api/v1/educators/unsubscribe", json={"token": "ghost"})
        # ORACLE: an unknown token is a normal "no" (200 + success=False), and
        # mutates nothing — there is no email parameter to abuse (#51 closed).
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_email_payload_rejected_by_schema(self, test_client):
        # The old forgeable shape { email } must no longer be accepted.
        resp = test_client.post(
            "/api/v1/educators/unsubscribe", json={"email": "victim@example.com"}
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Public-media access gate (#50) — end-to-end through require_educator_access
# ---------------------------------------------------------------------------


class TestPublicMediaGate:
    """These use the default test_client (no dependency override), so the real
    ``require_educator_access`` runs."""

    def test_list_without_token_is_401(self, test_client):
        resp = test_client.get("/api/v1/public-media")
        # ORACLE: the gate must reject anonymous access (the #50 gap).
        assert resp.status_code == 401

    def test_stats_without_token_is_401(self, test_client):
        assert test_client.get("/api/v1/public-media/stats").status_code == 401

    def test_detail_without_token_is_401(self, test_client):
        assert test_client.get("/api/v1/public-media/1").status_code == 401

    def test_download_without_token_is_401(self, test_client):
        assert test_client.get("/api/v1/public-media/1/download").status_code == 401

    def test_malformed_authorization_header_is_401(self, test_client):
        resp = test_client.get(
            "/api/v1/public-media", headers={"Authorization": "Basic abc"}
        )
        assert resp.status_code == 401

    def test_invalid_bearer_token_is_401(self, test_client):
        resp = test_client.get(
            "/api/v1/public-media", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert resp.status_code == 401

    def test_expired_token_is_401(self, test_client, test_db):
        _make_educator(
            test_db,
            email="gate-exp@example.com",
            access_token="gate-expired",
            access_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        resp = test_client.get(
            "/api/v1/public-media", headers={"Authorization": "Bearer gate-expired"}
        )
        assert resp.status_code == 401

    def test_valid_token_unlocks_list(self, test_client, test_db):
        _make_educator(
            test_db,
            email="gate-ok@example.com",
            access_token="gate-valid",
            access_expires_at=datetime.now(UTC) + timedelta(days=3),
        )
        resp = test_client.get(
            "/api/v1/public-media", headers={"Authorization": "Bearer gate-valid"}
        )
        # ORACLE: a valid issued token unlocks the gated route.
        assert resp.status_code == 200
        assert "items" in resp.json()
