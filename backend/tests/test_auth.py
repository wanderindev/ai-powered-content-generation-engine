"""
Tests for the admin auth / magic-link endpoints (#191).

Covers `app.api.auth`:
- /auth/verify-token: validity, expiry, single-use replay protection,
  reference resolution (order/booking/admin).
- /auth/admin/request-access: email-enumeration protection (unknown and
  inactive admins look identical to a real one), link creation, and the
  swallowed-email-failure branch.

The notification service is patched so no email is actually sent.

ORACLE NOTE: assertions marked `# ORACLE:` encode auth policy and were
written for human review (docs/pilot/agent-friendly-criteria.md).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.api.dependencies import validate_admin_token
from app.models import AdminEmail, MagicLink, MagicLinkAction
from fastapi import HTTPException

# VerifyTokenRequest enforces 32–64 char tokens (matches secrets.token_urlsafe(32)),
# so test tokens must clear that floor or the request 422s before the handler runs.
TOK_UNKNOWN = "unknown-token-padding-0000000000000000"
TOK_EXPIRED = "expired-token-padding-0000000000000000"
TOK_USED_CANCEL = "used-cancel-token-padding-00000000000000"
TOK_USED_ADMIN = "used-admin-token-padding-000000000000000"
TOK_FRESH_ADMIN = "fresh-admin-token-padding-00000000000000"


@pytest.fixture
def patched_notifications():
    """Patch the notification service used by request_admin_access."""
    with patch("app.api.auth.get_notification_service") as factory:
        yield factory.return_value


def _make_link(db, **kwargs) -> MagicLink:
    defaults = dict(
        token="tok-default",
        action=MagicLinkAction.ADMIN_ACCESS.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    defaults.update(kwargs)
    link = MagicLink(**defaults)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def _make_admin(db, *, email="admin@example.com", is_active=True) -> AdminEmail:
    admin = AdminEmail(email=email, is_active=is_active)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


# ---------------------------------------------------------------------------
# /auth/verify-token
# ---------------------------------------------------------------------------


class TestVerifyToken:
    def test_unknown_token_invalid(self, test_client):
        resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_UNKNOWN})
        assert resp.status_code == 200
        # ORACLE: an unknown token must be reported invalid.
        assert resp.json()["valid"] is False

    def test_expired_token_invalid(self, test_client, test_db):
        _make_link(
            test_db,
            token=TOK_EXPIRED,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_EXPIRED})
        # ORACLE: an expired token must be reported invalid.
        assert resp.json()["valid"] is False

    def test_used_single_use_action_invalid(self, test_client, test_db):
        _make_link(
            test_db,
            token=TOK_USED_CANCEL,
            action=MagicLinkAction.CANCEL_BOOKING.value,
            used_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_USED_CANCEL})
        # ORACLE: a consumed single-use token (CANCEL_BOOKING / RENEW_PAYMENT)
        # must be rejected on replay.
        assert resp.json()["valid"] is False

    def test_used_admin_action_still_valid(self, test_client, test_db):
        # ADMIN_ACCESS is NOT in the single-use set, so a used_at timestamp
        # does not invalidate it.
        _make_link(
            test_db,
            token=TOK_USED_ADMIN,
            action=MagicLinkAction.ADMIN_ACCESS.value,
            admin_email="admin@example.com",
            used_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_USED_ADMIN})
        body = resp.json()
        # ORACLE: confirm intended policy — ADMIN_ACCESS links are reusable
        # within their validity window even after first use.
        assert body["valid"] is True
        assert body["admin_email"] == "admin@example.com"

    def test_valid_admin_token_returns_admin_email(self, test_client, test_db):
        _make_link(
            test_db,
            token=TOK_FRESH_ADMIN,
            action=MagicLinkAction.ADMIN_ACCESS.value,
            admin_email="boss@example.com",
        )
        resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_FRESH_ADMIN})
        body = resp.json()
        assert body["valid"] is True
        assert body["admin_email"] == "boss@example.com"
        assert body["action"] == MagicLinkAction.ADMIN_ACCESS.value


# ---------------------------------------------------------------------------
# /auth/admin/request-access
# ---------------------------------------------------------------------------


class TestRequestAdminAccess:
    def test_unknown_email_no_link_created_generic_message(
        self, test_client, test_db, patched_notifications
    ):
        resp = test_client.post(
            "/api/v1/auth/admin/request-access", json={"email": "stranger@example.com"}
        )
        assert resp.status_code == 200
        body = resp.json()
        # ORACLE: enumeration protection — unknown email returns the same
        # generic message and creates NO magic link.
        assert "you will receive a login link" in body["message"]
        count = (
            test_db.query(MagicLink).filter(MagicLink.admin_email == "stranger@example.com").count()
        )
        assert count == 0
        patched_notifications.send_admin_magic_link.assert_not_called()

    def test_inactive_admin_treated_as_unknown(self, test_client, test_db, patched_notifications):
        _make_admin(test_db, email="inactive@example.com", is_active=False)
        resp = test_client.post(
            "/api/v1/auth/admin/request-access", json={"email": "inactive@example.com"}
        )
        assert resp.status_code == 200
        # ORACLE: an inactive admin must be indistinguishable from unknown and
        # must NOT receive a link.
        count = (
            test_db.query(MagicLink).filter(MagicLink.admin_email == "inactive@example.com").count()
        )
        assert count == 0
        patched_notifications.send_admin_magic_link.assert_not_called()

    def test_active_admin_gets_link_and_email(self, test_client, test_db, patched_notifications):
        _make_admin(test_db, email="real@example.com", is_active=True)
        resp = test_client.post(
            "/api/v1/auth/admin/request-access", json={"email": "real@example.com"}
        )
        assert resp.status_code == 200
        assert resp.json()["email_sent"] is True
        link = test_db.query(MagicLink).filter(MagicLink.admin_email == "real@example.com").one()
        # ORACLE: link is for ADMIN_ACCESS and time-limited (7 days).
        assert link.action == MagicLinkAction.ADMIN_ACCESS.value
        assert link.expires_at > datetime.now(UTC) + timedelta(days=7, hours=-1)
        assert link.expires_at < datetime.now(UTC) + timedelta(days=7, hours=1)
        patched_notifications.send_admin_magic_link.assert_called_once()

    def test_email_delivery_failure_is_swallowed(self, test_client, test_db):
        from app.services.email_sender import EmailDeliveryError

        _make_admin(test_db, email="real2@example.com", is_active=True)
        with patch("app.api.auth.get_notification_service") as factory:
            factory.return_value.send_admin_magic_link.side_effect = EmailDeliveryError(
                "relay down"
            )
            resp = test_client.post(
                "/api/v1/auth/admin/request-access", json={"email": "real2@example.com"}
            )
        # Current behavior: send failure is logged and swallowed; the link is
        # still created and the endpoint reports email_sent=True.
        assert resp.status_code == 200
        assert resp.json()["email_sent"] is True
        link = test_db.query(MagicLink).filter(MagicLink.admin_email == "real2@example.com").one()
        assert link is not None


# ---------------------------------------------------------------------------
# auth-failure audit logging (#61, layer 1)
# ---------------------------------------------------------------------------


class TestAuthFailureLogging:
    """Auth-failure sites must emit a structured WARNING log (and a Sentry
    breadcrumb) so brute-force / enumeration attempts are visible to ops.
    These assert observability only — the auth decisions are unchanged and
    are covered by the ORACLE tests above."""

    def test_unknown_magic_link_token_logs_warning(self, test_client, caplog):
        with caplog.at_level("WARNING", logger="app.api.auth"):
            resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_UNKNOWN})
        assert resp.json()["valid"] is False
        assert any(
            "magic_link_verify" in r.getMessage() and "token_not_found" in r.getMessage()
            for r in caplog.records
        )

    def test_expired_magic_link_token_logs_warning(self, test_client, test_db, caplog):
        _make_link(
            test_db,
            token=TOK_EXPIRED,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        with caplog.at_level("WARNING", logger="app.api.auth"):
            resp = test_client.post("/api/v1/auth/verify-token", json={"token": TOK_EXPIRED})
        assert resp.json()["valid"] is False
        assert any(
            "magic_link_verify" in r.getMessage() and "token_expired" in r.getMessage()
            for r in caplog.records
        )

    def test_unknown_admin_email_logs_warning(self, test_client, caplog, patched_notifications):
        with caplog.at_level("WARNING", logger="app.api.auth"):
            resp = test_client.post(
                "/api/v1/auth/admin/request-access", json={"email": "ghost@example.com"}
            )
        assert resp.status_code == 200
        assert any(
            "admin_login_request" in r.getMessage() and "email_not_in_allowlist" in r.getMessage()
            for r in caplog.records
        )

    def test_invalid_admin_token_logs_warning(self, test_db, caplog):
        with caplog.at_level("WARNING", logger="app.api.dependencies"):
            with pytest.raises(HTTPException):
                validate_admin_token(request=None, authorization=f"Bearer {TOK_UNKNOWN}", db=test_db)
        assert any(
            "admin_token_validate" in r.getMessage()
            and "invalid_or_expired_token" in r.getMessage()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# dev-token admin bypass gate (#55)
# ---------------------------------------------------------------------------


class TestDevTokenBypassGate:
    """The "dev-token" bypass in `validate_admin_token` must require BOTH
    `settings.debug` AND `settings.environment == "development"` — a single
    stray DEBUG=true in prod must not open admin access (#55)."""

    def _patch_settings(self, *, debug, environment):
        """Patch get_settings() to return a stub with the given flags."""
        from types import SimpleNamespace

        stub = SimpleNamespace(debug=debug, environment=environment)
        return patch("app.api.dependencies.get_settings", return_value=stub)

    def test_dev_token_rejected_when_debug_only(self, test_db):
        # ORACLE: debug=True alone (prod-style environment) must NOT activate
        # the bypass — the dev-token is treated as an ordinary invalid token.
        from app.api.dependencies import validate_admin_token

        with self._patch_settings(debug=True, environment="production"):
            with pytest.raises(HTTPException) as exc_info:
                validate_admin_token(request=None, authorization="Bearer dev-token", db=test_db)
        assert exc_info.value.status_code == 401

    def test_dev_token_rejected_when_environment_only(self, test_db):
        # ORACLE: environment=development without debug must NOT activate the
        # bypass either — both flags must align.
        from app.api.dependencies import validate_admin_token

        with self._patch_settings(debug=False, environment="development"):
            with pytest.raises(HTTPException) as exc_info:
                validate_admin_token(request=None, authorization="Bearer dev-token", db=test_db)
        assert exc_info.value.status_code == 401

    def test_dev_token_accepted_when_both_flags_align(self, test_db):
        # ORACLE: only when debug=True AND environment=="development" does the
        # dev-token bypass return the synthetic dev magic link.
        from app.api.dependencies import _DEV_MAGIC_LINK, validate_admin_token

        with self._patch_settings(debug=True, environment="development"):
            result = validate_admin_token(request=None, authorization="Bearer dev-token", db=test_db)
        assert result is _DEV_MAGIC_LINK
        assert result.admin_email == "dev@localhost"


# ---------------------------------------------------------------------------
# validate_admin_token allowlist re-check (#56)
# ---------------------------------------------------------------------------


class TestValidateAdminTokenAllowlist:
    TOK = "recheck-admin-token-padding-0000000000000"

    def test_allowlist_recheck_revokes_on_deactivation(self, test_db):
        # Active admin + fresh ADMIN_ACCESS link for that email.
        _make_admin(test_db, email="revoke@example.com", is_active=True)
        _make_link(
            test_db,
            token=self.TOK,
            action=MagicLinkAction.ADMIN_ACCESS.value,
            admin_email="revoke@example.com",
        )

        # While the admin is active, the token validates and returns the link.
        link = validate_admin_token(request=None, authorization=f"Bearer {self.TOK}", db=test_db)
        # ORACLE: a fresh ADMIN_ACCESS token for an active allowlist entry is valid.
        assert link.admin_email == "revoke@example.com"

        # Deactivate the admin (mirrors offboarding / incident-response revocation).
        admin = (
            test_db.query(AdminEmail)
            .filter(AdminEmail.email == "revoke@example.com")
            .one()
        )
        admin.is_active = False
        test_db.commit()

        # ORACLE: revocation is effective immediately — the previously-valid
        # token must be rejected (401) on the very next validation.
        with pytest.raises(HTTPException) as exc_info:
            validate_admin_token(request=None, authorization=f"Bearer {self.TOK}", db=test_db)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Admin token transport — header (preferred) vs. query (transitional) (#54/#57)
# ---------------------------------------------------------------------------


class TestAdminTokenTransport:
    """The router-level ``validate_admin_token`` dependency reads the token from
    the ``Authorization: Bearer`` header (preferred) and, transitionally, from
    the ``token`` query parameter. Exercised end-to-end through a real admin
    route so the wiring (not just the function) is covered.
    """

    URL = "/api/v1/admin/dashboard/stats"
    TOK = "transport-admin-token-padding-00000000000"

    def _seed(self, db):
        _make_admin(db, email="transport@example.com", is_active=True)
        _make_link(
            db,
            token=self.TOK,
            action=MagicLinkAction.ADMIN_ACCESS.value,
            admin_email="transport@example.com",
        )

    def test_bearer_header_authenticates(self, test_client, test_db):
        self._seed(test_db)
        resp = test_client.get(self.URL, headers={"Authorization": f"Bearer {self.TOK}"})
        assert resp.status_code == 200

    def test_query_token_rejected(self, test_client, test_db):
        # The transitional ?token= query fallback was removed (epic #263): a
        # token supplied only via the query string no longer authenticates.
        self._seed(test_db)
        resp = test_client.get(f"{self.URL}?token={self.TOK}")
        assert resp.status_code == 401

    def test_query_token_ignored_when_header_present(self, test_client, test_db):
        # The query string is no longer read at all — a valid header authenticates
        # even when a garbage token rides along in the (ignored) query.
        self._seed(test_db)
        resp = test_client.get(
            f"{self.URL}?token=bogus",
            headers={"Authorization": f"Bearer {self.TOK}"},
        )
        assert resp.status_code == 200

    def test_bogus_bearer_rejected(self, test_client, test_db):
        self._seed(test_db)
        resp = test_client.get(self.URL, headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_no_credential_rejected(self, test_client, test_db):
        self._seed(test_db)
        resp = test_client.get(self.URL)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Admin logout — server-side token revocation (#57)
# ---------------------------------------------------------------------------


class TestAdminLogout:
    URL = "/api/v1/auth/admin/logout"
    TOK = "logout-admin-token-padding-000000000000000"

    def test_logout_revokes_token(self, test_client, test_db):
        _make_admin(test_db, email="logout@example.com", is_active=True)
        _make_link(
            test_db,
            token=self.TOK,
            action=MagicLinkAction.ADMIN_ACCESS.value,
            admin_email="logout@example.com",
        )

        # Logout via Bearer header succeeds with 204.
        resp = test_client.post(self.URL, headers={"Authorization": f"Bearer {self.TOK}"})
        assert resp.status_code == 204

        # ORACLE: the magic link is gone — replaying the token is now rejected.
        assert test_db.query(MagicLink).filter(MagicLink.token == self.TOK).first() is None
        replay = test_client.get(
            "/api/v1/admin/dashboard/stats",
            headers={"Authorization": f"Bearer {self.TOK}"},
        )
        assert replay.status_code == 401

    def test_logout_requires_auth(self, test_client):
        assert test_client.post(self.URL).status_code == 401
