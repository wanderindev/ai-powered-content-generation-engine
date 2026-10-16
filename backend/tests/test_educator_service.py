"""
Tests for the educator access-gate service (low-friction, token-based).

Covers the reworked gate (#50/#51/#52/#53): being in the table is the
entitlement; an opaque server token + 7-day window carry access; there is no
email-confirmation step and no 6-digit verification code.

ORACLE NOTE (per docs/pilot/agent-friendly-criteria.md): assertions marked
`# ORACLE:` encode access-control *policy*, not mechanical behavior. They were
authored to be reviewed by a human — a green run is not proof on its own.

Uses the test_db fixture (Postgres testcontainer with per-test rollback).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.models.educator import Educator
from app.services import educator_service
from app.services.educator_service import ACCESS_DURATION_DAYS


def _make_educator(
    db,
    *,
    email: str = "edu@example.com",
    status: str = "PENDING",
    language: str = "es",
    confirm_token: str | None = None,
    access_token: str | None = None,
    access_expires_at: datetime | None = None,
    confirmed_at: datetime | None = None,
    last_access_at: datetime | None = None,
    unsubscribed_at: datetime | None = None,
    name: str | None = None,
    institution: str | None = None,
    source: str = "media_library",
    mailing_list: bool = False,
) -> Educator:
    educator = Educator(
        email=email,
        status=status,
        language=language,
        confirm_token=confirm_token,
        access_token=access_token,
        access_expires_at=access_expires_at,
        confirmed_at=confirmed_at,
        last_access_at=last_access_at,
        unsubscribed_at=unsubscribed_at,
        name=name,
        institution=institution,
        source=source,
        mailing_list=mailing_list,
    )
    db.add(educator)
    db.commit()
    db.refresh(educator)
    return educator


# ---------------------------------------------------------------------------
# access (returning educator enters email)
# ---------------------------------------------------------------------------


class TestAccess:
    def test_unknown_email_not_granted(self, test_db):
        result = educator_service.access(test_db, email="nobody@example.com")
        # ORACLE: an unknown email must not be granted access.
        assert result["granted"] is False
        assert result["status"] == "not_registered"
        assert result["token"] is None

    def test_unsubscribed_not_granted(self, test_db):
        _make_educator(
            test_db,
            email="unsub@example.com",
            status="UNSUBSCRIBED",
            unsubscribed_at=datetime(2026, 4, 1),
        )
        result = educator_service.access(test_db, email="unsub@example.com")
        # ORACLE: an unsubscribed account is treated as not registered — it must
        # re-signup to regain access, never be silently re-granted.
        assert result["granted"] is False
        assert result["status"] == "not_registered"

    def test_registered_email_granted_token_and_window(self, test_db):
        edu = _make_educator(test_db, email="in@example.com", status="PENDING")
        before = datetime.now(UTC)
        result = educator_service.access(test_db, email="in@example.com")
        # ORACLE: any registered, non-unsubscribed email gets access — no email
        # verification, no confirmation status required (low-friction gate).
        assert result["granted"] is True
        assert result["token"] is not None
        test_db.refresh(edu)
        assert edu.access_token == result["token"]
        # ORACLE: access grants exactly ACCESS_DURATION_DAYS.
        assert edu.access_expires_at > before + timedelta(days=ACCESS_DURATION_DAYS - 1)
        assert edu.access_expires_at < before + timedelta(days=ACCESS_DURATION_DAYS + 1)
        assert edu.last_access_at is not None

    def test_reaccess_rotates_token_and_extends_window(self, test_db):
        edu = _make_educator(
            test_db,
            email="again@example.com",
            access_token="old-token",
            access_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        result = educator_service.access(test_db, email="again@example.com")
        assert result["granted"] is True
        # ORACLE: a fresh login issues a new token (old one stops working) and a
        # full new window.
        assert result["token"] != "old-token"
        test_db.refresh(edu)
        assert edu.access_token == result["token"]

    def test_case_insensitive(self, test_db):
        _make_educator(test_db, email="case@example.com", status="PENDING")
        result = educator_service.access(test_db, email="CASE@Example.COM")
        # ORACLE: a case-variant login resolves the same row.
        assert result["granted"] is True


# ---------------------------------------------------------------------------
# signup (new educator — instant access, optional mailing-list double opt-in)
# ---------------------------------------------------------------------------


class TestSignup:
    def test_new_email_creates_row_and_grants_access(self, test_db):
        result = educator_service.signup(
            test_db,
            email="new@example.com",
            language="en",
            name="Ada",
            institution="Panama School",
        )
        assert result["granted"] is True
        assert result["token"] is not None
        row = test_db.query(Educator).filter(Educator.email == "new@example.com").one()
        # ORACLE: a gate-only signup is NOT confirmed (email never proven) and is
        # NOT on the mailing list.
        assert row.status == "PENDING"
        assert row.mailing_list is False
        assert row.access_token == result["token"]
        assert row.access_expires_at is not None
        assert row.name == "Ada"
        assert row.institution == "Panama School"

    def test_signup_without_mailing_list_sends_no_email(self, test_db):
        with patch("app.services.mailing_list.subscribe") as ml:
            educator_service.signup(test_db, email="nolist@example.com")
        # ORACLE: opting out of the list must not trigger the subscribe flow.
        ml.assert_not_called()

    def test_signup_with_mailing_list_delegates_double_optin(self, test_db):
        # mailing_list.subscribe owns the row + confirmation email; the gate
        # layers access on top. Patch the email sender so no SMTP is attempted.
        with patch("app.services.mailing_list._send_confirmation_email") as send:
            result = educator_service.signup(
                test_db,
                email="list@example.com",
                mailing_list_optin=True,
                name="Grace",
            )
        assert result["granted"] is True
        assert result["mailing_list_pending"] is True
        send.assert_called_once()  # double opt-in confirmation email sent
        row = test_db.query(Educator).filter(Educator.email == "list@example.com").one()
        # ORACLE: list membership is PENDING until the educator confirms; access
        # is already granted regardless.
        assert row.status == "PENDING"
        assert row.mailing_list is True
        assert row.confirm_token is not None  # the subscription confirm token
        assert row.access_token is not None  # gate access granted immediately
        assert row.name == "Grace"

    def test_existing_user_resignup_regrants_access(self, test_db):
        edu = _make_educator(
            test_db,
            email="existing@example.com",
            status="PENDING",
            access_token="stale",
            access_expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        result = educator_service.signup(test_db, email="existing@example.com")
        assert result["granted"] is True
        test_db.refresh(edu)
        # ORACLE: re-signing-up an existing row refreshes its access window.
        assert edu.access_token == result["token"]
        assert edu.access_expires_at > datetime.now(UTC)

    def test_unsubscribed_resignup_reactivates(self, test_db):
        edu = _make_educator(
            test_db,
            email="comeback@example.com",
            status="UNSUBSCRIBED",
            unsubscribed_at=datetime(2026, 4, 1),
        )
        result = educator_service.signup(test_db, email="comeback@example.com")
        assert result["granted"] is True
        test_db.refresh(edu)
        # ORACLE: an unsubscribed educator who re-signs-up is reactivated.
        assert edu.status == "PENDING"
        assert edu.unsubscribed_at is None
        assert edu.access_token is not None

    def test_does_not_overwrite_name_with_none(self, test_db):
        edu = _make_educator(
            test_db, email="keep@example.com", status="PENDING", name="KeepMe"
        )
        educator_service.signup(test_db, email="keep@example.com")  # no name
        test_db.refresh(edu)
        assert edu.name == "KeepMe"

    def test_email_normalized(self, test_db):
        educator_service.signup(test_db, email="  New.Norm@Example.COM ")
        row = test_db.query(Educator).filter(Educator.email == "new.norm@example.com").one()
        assert row.email == "new.norm@example.com"


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


class TestValidateToken:
    def test_none_token_rejected(self, test_db):
        assert educator_service.validate_token(test_db, None) is None
        assert educator_service.validate_token(test_db, "") is None

    def test_unknown_token_rejected(self, test_db):
        assert educator_service.validate_token(test_db, "no-such-token") is None

    def test_valid_token_resolves_educator(self, test_db):
        _make_educator(
            test_db,
            email="tok@example.com",
            access_token="good-token",
            access_expires_at=datetime.now(UTC) + timedelta(days=3),
        )
        edu = educator_service.validate_token(test_db, "good-token")
        assert edu is not None
        assert edu.email == "tok@example.com"

    def test_expired_window_rejected(self, test_db):
        _make_educator(
            test_db,
            email="exp@example.com",
            access_token="expired-token",
            access_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        # ORACLE: a token past its window grants nothing.
        assert educator_service.validate_token(test_db, "expired-token") is None

    def test_null_window_rejected(self, test_db):
        _make_educator(
            test_db, email="nowin@example.com", access_token="t", access_expires_at=None
        )
        assert educator_service.validate_token(test_db, "t") is None

    def test_unsubscribed_token_rejected(self, test_db):
        _make_educator(
            test_db,
            email="unsubtok@example.com",
            status="UNSUBSCRIBED",
            access_token="zombie",
            access_expires_at=datetime.now(UTC) + timedelta(days=3),
        )
        # ORACLE: unsubscribing revokes the token even if the window hasn't lapsed.
        assert educator_service.validate_token(test_db, "zombie") is None


# ---------------------------------------------------------------------------
# session (frontend mount re-validation)
# ---------------------------------------------------------------------------


class TestSession:
    def test_invalid_token_not_valid(self, test_db):
        assert educator_service.session(test_db, "nope") == {
            "valid": False,
            "expires_at": None,
        }

    def test_valid_token_returns_expiry_and_bumps_last_access(self, test_db):
        future = datetime.now(UTC) + timedelta(days=2)
        edu = _make_educator(
            test_db,
            email="sess@example.com",
            access_token="sess-token",
            access_expires_at=future,
            last_access_at=None,
        )
        result = educator_service.session(test_db, "sess-token")
        assert result["valid"] is True
        assert result["expires_at"] == future.isoformat()
        test_db.refresh(edu)
        assert edu.last_access_at is not None


# ---------------------------------------------------------------------------
# unsubscribe (token-authenticated — #51)
# ---------------------------------------------------------------------------


class TestUnsubscribe:
    def test_unknown_token_returns_failure(self, test_db):
        result = educator_service.unsubscribe(test_db, token="ghost-token")
        # ORACLE: unsubscribe is authenticated by the token; an unknown token
        # mutates nothing. (Closes the #51 unauthenticated-revocation hole.)
        assert result["success"] is False

    def test_valid_token_revokes_access_and_optout(self, test_db):
        edu = _make_educator(
            test_db,
            email="bye@example.com",
            status="PENDING",
            access_token="my-token",
            access_expires_at=datetime.now(UTC) + timedelta(days=3),
            mailing_list=True,
        )
        result = educator_service.unsubscribe(test_db, token="my-token")
        assert result["success"] is True
        test_db.refresh(edu)
        # ORACLE: unsubscribe revokes the session and opts the row out.
        assert edu.status == "UNSUBSCRIBED"
        assert edu.access_token is None
        assert edu.access_expires_at is None
        assert edu.mailing_list is False
        assert edu.unsubscribed_at is not None

    def test_already_unsubscribed_is_idempotent(self, test_db):
        # A token can't actually point at an unsubscribed row (unsubscribe nulls
        # it), but guard the branch anyway via a row that still holds a token.
        _make_educator(
            test_db,
            email="already@example.com",
            status="UNSUBSCRIBED",
            access_token="held",
        )
        result = educator_service.unsubscribe(test_db, token="held")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# auth-failure audit logging (#61)
# ---------------------------------------------------------------------------

_LOGGER = "app.services.educator_service"


class TestAuthFailureLogging:
    def test_access_not_registered_logs(self, test_db, caplog):
        with caplog.at_level("WARNING", logger=_LOGGER):
            educator_service.access(test_db, email="ghost@example.com")
        assert any(
            "educator_access" in r.getMessage() and "not_registered" in r.getMessage()
            for r in caplog.records
        )

    def test_unsubscribe_invalid_token_logs(self, test_db, caplog):
        with caplog.at_level("WARNING", logger=_LOGGER):
            educator_service.unsubscribe(test_db, token="bad")
        assert any(
            "educator_unsubscribe" in r.getMessage() and "invalid_token" in r.getMessage()
            for r in caplog.records
        )
