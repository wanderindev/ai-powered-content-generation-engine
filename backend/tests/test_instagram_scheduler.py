"""Tests for the Instagram publish scheduler (claiming, skipping, sweeping)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from app.models.instagram_post import InstagramPost
from app.services import instagram_scheduler as scheduler_module
from app.services.instagram_scheduler import run_scheduler_tick
from sqlalchemy.orm import sessionmaker


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
