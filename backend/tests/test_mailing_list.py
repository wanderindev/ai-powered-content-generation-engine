"""
Tests for the ORM-based mailing_list service.

Closes the test debt for #75 (concurrent-subscribe race) — exercised via
the deterministic IntegrityError branch in `subscribe`, which is the path
the UNIQUE(email) constraint will trigger under genuine concurrency.

Uses the existing test_db fixture (Postgres testcontainer with per-test
transaction rollback). _send_confirmation_email is patched throughout —
the wrapper's SMTP behavior is tested separately in test_email_sender.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.models.educator import Educator
from app.services import mailing_list
from app.services.email_sender import EmailDeliveryError


@pytest.fixture
def patched_send_email():
    """Patch _send_confirmation_email at the module attribute level."""
    with patch("app.services.mailing_list._send_confirmation_email") as m:
        yield m


@pytest.fixture
def patched_send_email_fails():
    """Patch _send_confirmation_email to raise EmailDeliveryError."""
    with patch(
        "app.services.mailing_list._send_confirmation_email",
        side_effect=EmailDeliveryError("smtp relay rejected"),
    ) as m:
        yield m


def _make_existing(
    db,
    *,
    email: str = "existing@example.com",
    status: str = "PENDING",
    mailing_list_flag: bool = True,
    confirm_token: str | None = "preexisting-token",
    source: str = "MailingListBlog",
    language: str = "en",
    access_expires_at: datetime | None = None,
    confirmed_at: datetime | None = None,
    unsubscribed_at: datetime | None = None,
) -> Educator:
    educator = Educator(
        email=email,
        status=status,
        mailing_list=mailing_list_flag,
        confirm_token=confirm_token,
        source=source,
        language=language,
        access_expires_at=access_expires_at,
        confirmed_at=confirmed_at,
        unsubscribed_at=unsubscribed_at,
    )
    db.add(educator)
    db.commit()
    db.refresh(educator)
    return educator


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


class TestSubscribeNew:
    def test_inserts_row_and_sends_email(self, test_db, patched_send_email):
        result = mailing_list.subscribe(
            email="new@example.com", source="MailingListBlog", language="en", db=test_db
        )
        assert result["success"] is True
        assert result["status"] == "pending"

        row = test_db.query(Educator).filter(Educator.email == "new@example.com").one()
        assert row.status == "PENDING"
        assert row.mailing_list is True
        assert row.confirm_token is not None
        assert row.source == "MailingListBlog"

        patched_send_email.assert_called_once()
        # The sent token must match the row's token (so the confirmation link works)
        assert patched_send_email.call_args.args[1] == row.confirm_token

    def test_send_email_failure_returns_failure_but_keeps_row(
        self, test_db, patched_send_email_fails
    ):
        result = mailing_list.subscribe(
            email="new@example.com", source="MailingListBlog", language="en", db=test_db
        )
        assert result["success"] is False
        assert "couldn't send" in result["message"]
        # Row persists so the user can re-trigger via the existing-pending branch.
        row = test_db.query(Educator).filter(Educator.email == "new@example.com").one()
        assert row.status == "PENDING"


class TestSubscribeExistingPending:
    def test_reuses_token_and_resends(self, test_db, patched_send_email):
        existing = _make_existing(test_db, confirm_token="abc-token", status="PENDING")
        result = mailing_list.subscribe(
            email=existing.email, source="MailingListBlog", language="en", db=test_db
        )
        assert result["success"] is True
        assert result["status"] == "pending"

        # Token unchanged
        test_db.refresh(existing)
        assert existing.confirm_token == "abc-token"
        # Email sent with the existing token
        patched_send_email.assert_called_once()
        assert patched_send_email.call_args.args[1] == "abc-token"

    def test_flips_mailing_list_flag_on_gate_pending_row(self, test_db, patched_send_email):
        # Existing gate-flow row: mailing_list=False, PENDING
        existing = _make_existing(
            test_db,
            email="gate@example.com",
            status="PENDING",
            mailing_list_flag=False,
            confirm_token="gate-token",
            source="media_library",
        )
        result = mailing_list.subscribe(
            email="gate@example.com", source="MailingListBlog", language="en", db=test_db
        )
        assert result["success"] is True
        test_db.refresh(existing)
        assert existing.mailing_list is True
        assert existing.source == "MailingListBlog"  # source updated to mailing-list CTA
        assert existing.confirm_token == "gate-token"  # token preserved


class TestSubscribeExistingConfirmed:
    def test_returns_already_subscribed_no_email(self, test_db, patched_send_email):
        _make_existing(
            test_db,
            email="confirmed@example.com",
            status="CONFIRMED",
            mailing_list_flag=True,
            confirm_token=None,
            confirmed_at=datetime(2026, 5, 1),
        )
        result = mailing_list.subscribe(
            email="confirmed@example.com",
            source="MailingListBlog",
            language="en",
            db=test_db,
        )
        assert result["success"] is True
        assert result["status"] == "already_subscribed"
        patched_send_email.assert_not_called()

    def test_flips_mailing_list_flag_on_gate_confirmed_row(self, test_db, patched_send_email):
        # Gate user (mailing_list=False, status=CONFIRMED) subscribes to mailing list.
        # Should flip the flag, no new email.
        existing = _make_existing(
            test_db,
            email="gate-confirmed@example.com",
            status="CONFIRMED",
            mailing_list_flag=False,
            confirm_token=None,
            source="media_library",
            confirmed_at=datetime(2026, 5, 1, tzinfo=UTC),
            access_expires_at=datetime(2026, 5, 8, tzinfo=UTC),
        )
        result = mailing_list.subscribe(
            email="gate-confirmed@example.com",
            source="MailingListBlog",
            language="en",
            db=test_db,
        )
        assert result["success"] is True
        assert result["status"] == "already_subscribed"
        patched_send_email.assert_not_called()
        test_db.refresh(existing)
        assert existing.mailing_list is True
        assert existing.source == "MailingListBlog"
        # Gate access untouched.
        assert existing.access_expires_at == datetime(2026, 5, 8, tzinfo=UTC)


class TestSubscribeExistingUnsubscribed:
    def test_resurrects_with_new_token_and_sends_email(self, test_db, patched_send_email):
        existing = _make_existing(
            test_db,
            email="unsub@example.com",
            status="UNSUBSCRIBED",
            mailing_list_flag=False,
            confirm_token=None,
            unsubscribed_at=datetime(2026, 4, 1),
        )
        result = mailing_list.subscribe(
            email="unsub@example.com", source="MailingListBlog", language="en", db=test_db
        )
        assert result["success"] is True
        assert result["status"] == "pending"

        test_db.refresh(existing)
        assert existing.status == "PENDING"
        assert existing.mailing_list is True
        assert existing.confirm_token is not None
        assert existing.unsubscribed_at is None

        patched_send_email.assert_called_once()
        assert patched_send_email.call_args.args[1] == existing.confirm_token


class TestSubscribeRaceClosure:
    """The #75 closer — the IntegrityError branch is what UNIQUE(email) triggers
    under genuine concurrency. Exercise it deterministically: pre-create the
    row, then call subscribe with the same email and verify the result routes
    through the existing-row branch (no duplicate row, no exception)."""

    def test_unique_collision_routes_through_existing_branch(self, test_db, patched_send_email):
        _make_existing(test_db, email="race@example.com", status="PENDING")
        result = mailing_list.subscribe(
            email="race@example.com", source="MailingListBlog", language="en", db=test_db
        )
        # Branch routes to the pending-resend path, not a new insert.
        assert result["success"] is True
        assert result["status"] == "pending"
        # Exactly one row in the table for this email — the constraint did its job.
        count = test_db.query(Educator).filter(Educator.email == "race@example.com").count()
        assert count == 1


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


class TestConfirm:
    def test_valid_token_marks_confirmed_and_clears_token(self, test_db):
        existing = _make_existing(test_db, confirm_token="valid-token", status="PENDING")
        result = mailing_list.confirm(token="valid-token", language="es", db=test_db)
        assert result["success"] is True
        assert result["confirmed"] is True
        assert result["email"] == existing.email
        test_db.refresh(existing)
        assert existing.status == "CONFIRMED"
        assert existing.confirm_token is None
        assert existing.confirmed_at is not None
        assert existing.language == "es"  # language refresh

    def test_invalid_token_returns_failure(self, test_db):
        result = mailing_list.confirm(token="nonexistent", language="en", db=test_db)
        assert result["success"] is False
        assert result["confirmed"] is False
        assert result["email"] is None


# ---------------------------------------------------------------------------
# unsubscribe
# ---------------------------------------------------------------------------


class TestUnsubscribe:
    def test_confirmed_mailing_only_flips_to_unsubscribed(self, test_db):
        existing = _make_existing(
            test_db,
            email="sub@example.com",
            status="CONFIRMED",
            mailing_list_flag=True,
            confirm_token=None,
            confirmed_at=datetime(2026, 5, 1),
            # access_expires_at intentionally None — no gate access
        )
        result = mailing_list.unsubscribe(email="sub@example.com", db=test_db)
        assert result["success"] is True
        assert result["unsubscribed"] is True
        test_db.refresh(existing)
        assert existing.status == "UNSUBSCRIBED"
        assert existing.mailing_list is False
        assert existing.unsubscribed_at is not None

    def test_confirmed_with_gate_access_preserves_gate(self, test_db):
        """Sub-4 (A): unsubscribing from mailing list must not revoke gate access."""
        future_expiry = datetime.now(UTC) + timedelta(days=3)
        existing = _make_existing(
            test_db,
            email="hybrid@example.com",
            status="CONFIRMED",
            mailing_list_flag=True,
            confirm_token=None,
            confirmed_at=datetime(2026, 5, 1),
            access_expires_at=future_expiry,
        )
        result = mailing_list.unsubscribe(email="hybrid@example.com", db=test_db)
        assert result["success"] is True
        assert result["unsubscribed"] is True
        test_db.refresh(existing)
        # mailing_list flipped off, but status stays CONFIRMED for gate purposes.
        assert existing.mailing_list is False
        assert existing.status == "CONFIRMED"
        assert existing.access_expires_at == future_expiry

    def test_not_found_returns_failure(self, test_db):
        result = mailing_list.unsubscribe(email="nobody@example.com", db=test_db)
        assert result["success"] is False
        assert "not subscribed" in result["message"]

    def test_existing_gate_row_without_mailing_list_returns_not_subscribed(self, test_db):
        # User has a gate-only row; mailing-list unsubscribe should not touch it.
        existing = _make_existing(
            test_db,
            email="gate-only@example.com",
            status="CONFIRMED",
            mailing_list_flag=False,
            confirm_token=None,
        )
        result = mailing_list.unsubscribe(email="gate-only@example.com", db=test_db)
        assert result["success"] is False
        assert "not subscribed" in result["message"]
        test_db.refresh(existing)
        assert existing.status == "CONFIRMED"  # untouched

    def test_pending_returns_not_confirmed_message(self, test_db):
        _make_existing(test_db, status="PENDING", mailing_list_flag=True)
        result = mailing_list.unsubscribe(email="existing@example.com", db=test_db)
        assert result["success"] is False
        assert "not been confirmed yet" in result["message"]


# email normalization — case-insensitive lookups (issue #63)
# ---------------------------------------------------------------------------


class TestEmailNormalization:
    def test_subscribe_stores_normalized_email(self, test_db, patched_send_email):
        result = mailing_list.subscribe(
            email="  Norm.Sub@Case.COM ",
            source="MailingListBlog",
            language="en",
            db=test_db,
        )
        assert result["success"] is True
        row = test_db.query(Educator).filter(Educator.email == "norm.sub@case.com").one()
        assert row.email == "norm.sub@case.com"

    def test_subscribe_case_variant_hits_existing_row(self, test_db, patched_send_email):
        # Subscribing with one casing then a case variant reuses the same row
        # (existing-row branch) rather than inserting a duplicate.
        mailing_list.subscribe(
            email="Variant@Case.com",
            source="MailingListBlog",
            language="en",
            db=test_db,
        )
        mailing_list.subscribe(
            email="variant@case.com",
            source="MailingListBlog",
            language="en",
            db=test_db,
        )
        rows = test_db.query(Educator).filter(Educator.email == "variant@case.com").all()
        assert len(rows) == 1

    def test_unsubscribe_case_insensitive(self, test_db):
        educator = Educator(
            email="bye@example.com",
            status="CONFIRMED",
            source="MailingListBlog",
            language="en",
            mailing_list=True,
        )
        test_db.add(educator)
        test_db.commit()

        result = mailing_list.unsubscribe(email="BYE@Example.COM", db=test_db)
        assert result["success"] is True
        refreshed = test_db.query(Educator).filter(Educator.email == "bye@example.com").one()
        assert refreshed.mailing_list is False
