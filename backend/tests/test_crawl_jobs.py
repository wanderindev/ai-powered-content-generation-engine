"""Tests for async crawl/score jobs (#277).

Covers the kick-off endpoints (202 + job id, single-active-job 409 guard), the
status-poll endpoints, the background runner's state transitions and progress
persistence, the stale-job reclaim, and the status/type CHECK constraints.
"""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from app.core.database import Base
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.services import crawl_jobs as crawl_jobs_module
from app.services.crawl_jobs import STALE_AFTER, active_job, run_crawl_job
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


def _running_job(job_type=CrawlJobType.WIKIMEDIA, **kwargs):
    return CrawlJob(
        job_type=job_type.value,
        status=CrawlJobStatus.RUNNING.value,
        **kwargs,
    )


# ── Kick-off endpoints ───────────────────────────────────────────────────


class TestKickoffEndpoints:
    @patch("app.api.media_library.run_crawl_job")
    def test_score_kickoff_returns_202(self, mock_run, test_client, admin_token):
        response = test_client.post(
            "/api/v1/media-library/score", json={"batch_size": 10}
        )
        assert response.status_code == 202
        data = response.json()
        assert data["job_type"] == "SCORE"
        assert data["status"] == "PENDING"
        assert data["units_done"] == 0
        mock_run.assert_called_once_with(data["id"])

    @patch("app.api.media_library.run_crawl_job")
    def test_second_job_of_same_type_rejected(
        self, mock_run, test_client, test_db, admin_token
    ):
        # An in-flight Wikimedia job already exists.
        test_db.add(_running_job(CrawlJobType.WIKIMEDIA))
        test_db.commit()

        response = test_client.post("/api/v1/media-library/crawl", json={})
        assert response.status_code == 409
        assert "already running" in response.json()["detail"]
        mock_run.assert_not_called()

    @patch("app.api.media_library.run_crawl_job")
    def test_other_type_not_blocked(self, mock_run, test_client, test_db, admin_token):
        # A running Wikimedia job must not block an LOC kick-off.
        test_db.add(_running_job(CrawlJobType.WIKIMEDIA))
        test_db.commit()

        response = test_client.post("/api/v1/media-library/crawl-loc", json={})
        assert response.status_code == 202
        mock_run.assert_called_once()


# ── Status-poll endpoints ────────────────────────────────────────────────


class TestPollEndpoints:
    def test_get_job(self, test_client, test_db, admin_token):
        job = CrawlJob(
            job_type=CrawlJobType.LOC.value,
            status=CrawlJobStatus.SUCCEEDED.value,
            units_total=14,
            units_done=14,
            stats={"queries_executed": 14, "images_new": 5},
        )
        test_db.add(job)
        test_db.commit()

        response = test_client.get(f"/api/v1/media-library/jobs/{job.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "SUCCEEDED"
        assert data["units_total"] == 14
        assert data["stats"]["images_new"] == 5

    def test_get_missing_job_404(self, test_client, admin_token):
        response = test_client.get("/api/v1/media-library/jobs/999999")
        assert response.status_code == 404

    def test_list_jobs_filtered_newest_first(self, test_client, test_db, admin_token):
        test_db.add_all(
            [
                _running_job(CrawlJobType.WIKIMEDIA),
                _running_job(CrawlJobType.LOC),
                CrawlJob(
                    job_type=CrawlJobType.LOC.value,
                    status=CrawlJobStatus.SUCCEEDED.value,
                ),
            ]
        )
        test_db.commit()

        response = test_client.get("/api/v1/media-library/jobs?job_type=LOC")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 2
        assert all(i["job_type"] == "LOC" for i in items)
        # Newest first.
        assert items[0]["id"] > items[1]["id"]


# ── active_job / staleness ───────────────────────────────────────────────


class TestActiveJob:
    def test_running_job_is_active(self, test_db):
        test_db.add(_running_job(CrawlJobType.WIKIMEDIA))
        test_db.commit()
        assert active_job(test_db, CrawlJobType.WIKIMEDIA.value) is not None

    def test_terminal_job_not_active(self, test_db):
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.WIKIMEDIA.value,
                status=CrawlJobStatus.SUCCEEDED.value,
            )
        )
        test_db.commit()
        assert active_job(test_db, CrawlJobType.WIKIMEDIA.value) is None

    def test_stale_running_job_ignored(self, test_db):
        # A job that has been RUNNING longer than STALE_AFTER is presumed dead.
        stale = _running_job(CrawlJobType.WIKIMEDIA)
        stale.created_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=5)
        test_db.add(stale)
        test_db.commit()
        assert active_job(test_db, CrawlJobType.WIKIMEDIA.value) is None


# ── Background runner ────────────────────────────────────────────────────


class TestRunCrawlJob:
    @pytest.fixture
    def patched_session(self, test_engine):
        """Point the runner's own session factory at the test database."""
        factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        with patch.object(crawl_jobs_module, "SessionLocal", factory):
            yield

    def test_success_persists_stats_and_progress(
        self, test_db, patched_session
    ):
        job = CrawlJob(job_type=CrawlJobType.WIKIMEDIA.value, params={"max_depth": 1})
        test_db.add(job)
        test_db.commit()
        job_id = job.id

        stats = {
            "categories_crawled": 2,
            "images_found": 10,
            "images_new": 8,
            "images_skipped": 2,
            "errors": [],
        }

        def fake_crawl(**kwargs):
            # Drive the progress callback so we exercise the counter persistence.
            kwargs["progress_callback"](3, 5, "History of Panama")
            return stats

        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.crawl_categories.side_effect = fake_crawl

        with patch.object(
            crawl_jobs_module, "WikimediaService", return_value=mock_instance
        ):
            run_crawl_job(job_id)

        test_db.expire_all()
        refreshed = test_db.get(CrawlJob, job_id)
        assert refreshed.status == CrawlJobStatus.SUCCEEDED.value
        assert refreshed.stats == stats
        assert refreshed.units_done == 3
        assert refreshed.units_total == 5
        assert refreshed.current_unit == "History of Panama"
        assert refreshed.started_at is not None
        assert refreshed.finished_at is not None

    def test_failure_records_error(self, test_db, patched_session):
        job = CrawlJob(job_type=CrawlJobType.LOC.value)
        test_db.add(job)
        test_db.commit()
        job_id = job.id

        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.crawl_panama.side_effect = RuntimeError("LOC exploded")

        with patch.object(
            crawl_jobs_module, "LOCService", return_value=mock_instance
        ):
            run_crawl_job(job_id)

        test_db.expire_all()
        refreshed = test_db.get(CrawlJob, job_id)
        assert refreshed.status == CrawlJobStatus.FAILED.value
        assert "LOC exploded" in refreshed.error
        assert refreshed.finished_at is not None

    def test_score_job_dispatch(self, test_db, patched_session):
        job = CrawlJob(job_type=CrawlJobType.SCORE.value, params={"batch_size": 5})
        test_db.add(job)
        test_db.commit()
        job_id = job.id

        score_stats = {
            "candidates_scored": 5,
            "batches_processed": 1,
            "batches_skipped": 0,
        }
        with patch.object(
            crawl_jobs_module, "score_candidates", return_value=score_stats
        ) as mock_score:
            run_crawl_job(job_id)

        mock_score.assert_called_once()
        test_db.expire_all()
        refreshed = test_db.get(CrawlJob, job_id)
        assert refreshed.status == CrawlJobStatus.SUCCEEDED.value
        assert refreshed.stats == score_stats


# ── Status/type CHECK constraints (issue #24) ────────────────────────────


def _model_allowed(column):
    name = f"ck_crawl_jobs_{column}"
    for constraint in Base.metadata.tables["crawl_jobs"].constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == name:
            return set(re.findall(r"'([^']*)'", str(constraint.sqltext)))
    raise AssertionError(f"missing CHECK constraint {name}")


def test_status_check_matches_enum():
    assert _model_allowed("status") == set(CrawlJobStatus.values())


def test_job_type_check_matches_enum():
    assert _model_allowed("job_type") == set(CrawlJobType.values())


def test_invalid_status_rejected(test_db):
    test_db.add(CrawlJob(job_type=CrawlJobType.LOC.value, status="BOGUS"))
    with pytest.raises(IntegrityError):
        test_db.commit()


def test_invalid_job_type_rejected(test_db):
    test_db.add(CrawlJob(job_type="FLICKR"))
    with pytest.raises(IntegrityError):
        test_db.commit()
