"""Tests for the Instagram publish scheduler (claiming, skipping, sweeping)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from app.models.instagram_post import InstagramPost
from app.services import instagram_scheduler as scheduler_module
from app.services.http_client import SSRFError
from app.services.instagram import IgTokenInfo, InstagramPublishError
from app.services.instagram_scheduler import run_scheduler_tick
from sqlalchemy.orm import sessionmaker


_HEALTHY_TOKEN = IgTokenInfo(
    is_valid=True,
    expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    scopes=("instagram_content_publish",),
)


@pytest.fixture(autouse=True)
def reset_token_check():
    """The daily token check is per-process state; start every test fresh."""
    scheduler_module._last_token_check_at = None
    yield
    scheduler_module._last_token_check_at = None


@pytest.fixture
def patched_session(test_engine):
    """Point the scheduler's own session factory at the test database."""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with patch.object(scheduler_module, "SessionLocal", factory):
        yield factory


@pytest.fixture
def fake_publisher():
    """Enabled publisher whose publish_post marks the post published.

    Exposes ``published_ids`` (ids in claim order) — the ORM instances passed
    to the mock detach once the tick's session closes, so ids are recorded at
    call time.
    """
    publisher = MagicMock()
    publisher.enabled = True
    publisher.publishing_enabled = True
    publisher.fetch_token_info.return_value = _HEALTHY_TOKEN

    def _publish(db, post, pub):
        mock_publish.published_ids.append(post.id)
        post.published_at = datetime.now(UTC)
        post.ig_media_id = "M1"
        post.publishing_started_at = None
        db.commit()
        return True

    with (
        patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
        patch.object(scheduler_module, "publish_post", side_effect=_publish) as mock_publish,
    ):
        mock_publish.published_ids = []
        yield mock_publish


def _seed(test_db, **overrides):
    fields = {
        "post_type": "SABIAS_QUE",
        "status": "APPROVED",
        "title": "Post",
        "caption": "hola",
        "image_url": "https://cdn.example.com/i.jpg",
        "scheduled_at": datetime.now(UTC) - timedelta(minutes=5),
    }
    fields.update(overrides)
    post = InstagramPost(**fields)
    test_db.add(post)
    test_db.commit()
    test_db.refresh(post)
    return post


class TestSchedulerTick:
    def test_publishes_due_post(self, test_db, patched_session, fake_publisher):
        post = _seed(test_db)

        assert run_scheduler_tick() == 1
        assert fake_publisher.call_count == 1
        test_db.expire_all()
        assert test_db.get(InstagramPost, post.id).published_at is not None

    def test_publishes_multiple_due_posts_oldest_first(
        self, test_db, patched_session, fake_publisher
    ):
        newer = _seed(test_db, scheduled_at=datetime.now(UTC) - timedelta(minutes=1))
        older = _seed(test_db, scheduled_at=datetime.now(UTC) - timedelta(hours=1))

        assert run_scheduler_tick() == 2
        assert fake_publisher.published_ids == [older.id, newer.id]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"scheduled_at": datetime.now(UTC) + timedelta(hours=2)},  # future
            {"scheduled_at": None},  # not scheduled
            {"status": "PENDING"},  # not approved
            {"image_url": None},  # not rendered
            {"publish_error": "previous failure"},  # parked — never auto-retried
            {"published_at": datetime.now(UTC)},  # already live
            {"publishing_started_at": datetime.now(UTC)},  # claimed by another worker
        ],
        ids=["future", "unscheduled", "pending", "unrendered", "failed", "published", "claimed"],
    )
    def test_skips_ineligible_rows(self, test_db, patched_session, fake_publisher, overrides):
        _seed(test_db, **overrides)
        assert run_scheduler_tick() == 0
        fake_publisher.assert_not_called()

    def test_disabled_publisher_leaves_queue_untouched(self, test_db, patched_session):
        post = _seed(test_db)
        publisher = MagicMock()
        publisher.enabled = False
        with (
            patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
            patch.object(scheduler_module, "publish_post") as mock_publish,
        ):
            assert run_scheduler_tick() == 0
        mock_publish.assert_not_called()
        test_db.expire_all()
        refreshed = test_db.get(InstagramPost, post.id)
        assert refreshed.published_at is None
        assert refreshed.publishing_started_at is None  # still queued, unclaimed

    def test_stale_claim_is_swept_to_failed(self, test_db, patched_session, fake_publisher):
        post = _seed(
            test_db,
            scheduled_at=datetime.now(UTC) - timedelta(hours=2),
            publishing_started_at=datetime.now(UTC) - timedelta(minutes=30),
        )

        run_scheduler_tick()

        test_db.expire_all()
        refreshed = test_db.get(InstagramPost, post.id)
        assert refreshed.publishing_started_at is None
        assert "interrupted" in refreshed.publish_error
        # Swept row is parked, not retried in the same tick.
        fake_publisher.assert_not_called()

    def test_fresh_claim_is_not_swept(self, test_db, patched_session, fake_publisher):
        post = _seed(test_db, publishing_started_at=datetime.now(UTC) - timedelta(minutes=2))
        run_scheduler_tick()
        test_db.expire_all()
        refreshed = test_db.get(InstagramPost, post.id)
        assert refreshed.publish_error is None  # in-flight publish left alone

    def test_row_locked_by_concurrent_worker_is_skipped(
        self, test_db, patched_session, fake_publisher
    ):
        """SKIP LOCKED: a row locked in another session is invisible to a tick."""
        post = _seed(test_db)

        other = patched_session()
        try:
            # Simulate a concurrent ticker holding the row lock mid-claim.
            locked = (
                other.query(InstagramPost)
                .filter(InstagramPost.id == post.id)
                .with_for_update()
                .one()
            )
            assert locked.id == post.id

            assert run_scheduler_tick() == 0
            fake_publisher.assert_not_called()
        finally:
            other.rollback()
            other.close()


class TestPublishingKillSwitch:
    """#576: credentials set, switch off -> nothing is claimed or published."""

    def _publisher(self, token_info=_HEALTHY_TOKEN):
        publisher = MagicMock()
        publisher.enabled = True
        publisher.publishing_enabled = False
        publisher.fetch_token_info.return_value = token_info
        return publisher

    def test_due_rows_stay_scheduled(self, test_db, patched_session):
        post = _seed(test_db)
        publisher = self._publisher()
        with (
            patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
            patch.object(scheduler_module, "publish_post") as mock_publish,
        ):
            assert run_scheduler_tick() == 0
        mock_publish.assert_not_called()
        publisher.publish_image.assert_not_called()
        test_db.expire_all()
        refreshed = test_db.get(InstagramPost, post.id)
        assert refreshed.published_at is None
        assert refreshed.publish_error is None  # not FAILED
        assert refreshed.publishing_started_at is None  # never claimed
        assert refreshed.derived_state == "SCHEDULED"

    def test_token_check_runs_once_a_day_even_with_publishing_off(self, test_db, patched_session):
        publisher = self._publisher()
        with patch.object(scheduler_module, "InstagramPublisher", return_value=publisher):
            run_scheduler_tick()
            run_scheduler_tick()
        assert publisher.fetch_token_info.call_count == 1

        scheduler_module._last_token_check_at = datetime.now(UTC) - timedelta(days=1, minutes=1)
        with patch.object(scheduler_module, "InstagramPublisher", return_value=publisher):
            run_scheduler_tick()
        assert publisher.fetch_token_info.call_count == 2

    def test_expiring_token_warns_sentry(self, test_db, patched_session):
        expiring = IgTokenInfo(
            is_valid=True, expires_at=datetime.now(UTC) + timedelta(days=2), scopes=()
        )
        publisher = self._publisher(expiring)
        with (
            patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
            patch("app.services.instagram.sentry_sdk.capture_message") as capture,
        ):
            run_scheduler_tick()
        capture.assert_called_once()
        assert "expires soon" in capture.call_args.args[0]

    def test_token_check_failure_does_not_break_the_tick(self, test_db, patched_session):
        publisher = self._publisher()
        publisher.fetch_token_info.side_effect = InstagramPublishError("boom")
        publisher.fetch_granted_permissions.side_effect = InstagramPublishError("boom")
        with (
            patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
            patch.object(scheduler_module.sentry_sdk, "capture_message") as capture,
        ):
            assert run_scheduler_tick() == 0
        capture.assert_called_once()
        # Fixed message: neither the error text nor a token goes to Sentry.
        assert capture.call_args.args[0] == "Instagram token check failed"

    def test_non_graph_errors_in_the_check_are_swallowed(self, test_db, patched_session):
        """#576 review: an SSRFError (ValueError) or any other exception from the
        check must not abort the tick (the loop would capture_exception it,
        frame locals and all)."""
        post = _seed(test_db)
        publisher = self._publisher()
        publisher.fetch_token_info.side_effect = SSRFError("Could not resolve host")
        publisher.fetch_granted_permissions.side_effect = RuntimeError("boom")
        with (
            patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
            patch.object(scheduler_module.sentry_sdk, "capture_message"),
        ):
            assert run_scheduler_tick() == 0
        test_db.expire_all()
        assert test_db.get(InstagramPost, post.id).derived_state == "SCHEDULED"

    def test_debug_token_refused_falls_back_to_permissions_without_sentry(
        self, test_db, patched_session
    ):
        publisher = self._publisher()
        publisher.fetch_token_info.side_effect = InstagramPublishError(
            "(#100) You must provide an app access token (code 100)"
        )
        publisher.fetch_granted_permissions.return_value = {"instagram_content_publish"}
        with (
            patch.object(scheduler_module, "InstagramPublisher", return_value=publisher),
            patch.object(scheduler_module.sentry_sdk, "capture_message") as capture,
        ):
            assert run_scheduler_tick() == 0
        capture.assert_not_called()
        publisher.fetch_granted_permissions.assert_called_once()

    @pytest.mark.parametrize("failure", ["ssrf", "non_json"])
    def test_real_publisher_failure_in_check_keeps_the_tick_alive(
        self, test_db, patched_session, failure
    ):
        """End to end through the real client: the transport's SSRFError and a
        non-JSON 2xx body both surface as a handled failure, never a raise."""
        import httpx
        from app.services import instagram as instagram_module
        from types import SimpleNamespace

        def handler(request):
            if failure == "ssrf":
                raise SSRFError("Could not resolve host graph.test")
            return httpx.Response(200, text="<html>not json</html>")

        settings = SimpleNamespace(
            instagram_access_token="EAAGsecret",
            instagram_account_id="17840000",
            instagram_publishing_enabled=False,
            instagram_graph_api_url="https://graph.test/v23.0",
        )
        with (
            patch.object(instagram_module, "get_settings", return_value=settings),
            patch.object(
                instagram_module,
                "make_hardened_client",
                return_value=httpx.Client(transport=httpx.MockTransport(handler)),
            ),
            patch.object(scheduler_module.sentry_sdk, "capture_message") as capture,
        ):
            assert run_scheduler_tick() == 0
        capture.assert_called_once_with("Instagram token check failed", level="warning")

    def test_real_publisher_from_settings_claims_nothing_and_makes_no_graph_call(
        self, test_db, patched_session
    ):
        """#588: the real InstagramPublisher and publish_post, built from real
        Settings with credentials set and the switch off — the tick claims
        nothing and never reaches the Graph API."""
        import httpx
        from app.core.config import Settings
        from app.services import instagram as instagram_module

        post = _seed(test_db)
        settings = Settings(
            _env_file=None,
            instagram_access_token="EAAGsecret",
            instagram_account_id="17840000",
            instagram_publishing_enabled=False,
            instagram_graph_api_url="https://graph.test/v23.0",
        )
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={})

        # With credentials set, the tick otherwise runs the once-a-day
        # debug_token check even with the switch off — a read, not a publish.
        scheduler_module._last_token_check_at = datetime.now(UTC)
        with (
            patch.object(instagram_module, "get_settings", return_value=settings),
            patch.object(
                instagram_module,
                "make_hardened_client",
                return_value=httpx.Client(transport=httpx.MockTransport(handler)),
            ),
        ):
            assert run_scheduler_tick() == 0
        assert requests == []
        test_db.expire_all()
        refreshed = test_db.get(InstagramPost, post.id)
        assert refreshed.publishing_started_at is None
        assert refreshed.publish_error is None
        assert refreshed.published_at is None
        assert refreshed.publish_attempts == 0
        assert refreshed.derived_state == "SCHEDULED"

    def test_no_token_check_without_credentials(self, test_db, patched_session):
        publisher = self._publisher()
        publisher.enabled = False
        with patch.object(scheduler_module, "InstagramPublisher", return_value=publisher):
            run_scheduler_tick()
        publisher.fetch_token_info.assert_not_called()
