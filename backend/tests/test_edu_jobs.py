"""Tests for the edu pipeline background jobs (#346).

The long edu steps (guide generation, render-slides, generate-audio,
assemble-video) validate synchronously and then run as ``crawl_jobs`` rows; the
per-endpoint 404/400/422 tests live beside each service's own tests. This file
covers what is new: the in-flight rule (join an identical job, 409 a different
one, ignore a stale one), per-slide durability through the progress commits,
and guide generation as a job.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.services.crawl_jobs import STALE_AFTER
from app.services.edu_jobs import run_edu_job

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")

RENDER_URL = "/api/v1/admin/dashboard/edu/materials/{id}/render-slides"
VIDEO_URL = "/api/v1/admin/dashboard/edu/materials/{id}/assemble-video"
GENERATE_URL = "/api/v1/admin/dashboard/edu/materials/generate"


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Override the router-level admin gate for every test in this module (#54)."""
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


def _research(db, *, status="APPROVED", bands=("3rd-6th", "7th-8th")):
    s = EduSuggestion(title="Panamá Viejo", grade_bands=list(bands))
    db.add(s)
    db.flush()
    r = EduResearch(suggestion_id=s.id, content="research body", status=status)
    db.add(r)
    db.commit()
    return r


def _deck(db, n=3, *, rendered=False):
    r = _research(db)
    m = EduMaterial(
        research_id=r.id,
        material_type="slides",
        grade_band="3rd-6th",
        title="Deck",
        content="preview",
        status="APPROVED",
    )
    db.add(m)
    db.flush()
    for i in range(1, n + 1):
        m.slides.append(
            EduSlide(
                position=i,
                template="bullets",
                title=f"Slide {i}",
                body="Uno",
                narration="Hola.",
                image_url=f"https://cdn.example/f/{i}.png" if rendered else None,
                audio_url=f"https://cdn.example/a/{i}.mp3" if rendered else None,
                duration_ms=1000 if rendered else None,
            )
        )
    db.commit()
    db.refresh(m)
    return m


def _in_flight(db, job_type, params, *, age=timedelta(0)):
    job = CrawlJob(job_type=job_type.value, params=params, status=CrawlJobStatus.RUNNING.value)
    db.add(job)
    db.commit()
    if age:
        job.created_at = datetime.now(UTC) - age
        db.commit()
    return job


def _get_job(test_client, job_id):
    return test_client.get(f"/api/v1/media-library/jobs/{job_id}").json()


# --- in-flight rule ----------------------------------------------------------


class TestInFlight:
    def test_identical_request_joins_the_running_job(self, test_client, test_db):
        m = _deck(test_db, rendered=True)
        running = _in_flight(test_db, CrawlJobType.EDU_ASSEMBLE_VIDEO, {"material_id": m.id})

        with patch("app.api.edu.run_edu_job") as runner:
            resp = test_client.post(VIDEO_URL.format(id=m.id))

        assert resp.status_code == 202
        assert resp.json()["id"] == running.id
        runner.assert_not_called()  # joined, not re-run
        assert test_db.query(CrawlJob).count() == 1

    def test_same_type_different_positions_is_409(self, test_client, test_db):
        m = _deck(test_db)
        running = _in_flight(
            test_db, CrawlJobType.EDU_RENDER_SLIDES, {"material_id": m.id, "positions": [1]}
        )

        with patch("app.api.edu.run_edu_job") as runner:
            resp = test_client.post(RENDER_URL.format(id=m.id), json={"positions": [2]})

        assert resp.status_code == 409
        assert f"Job {running.id}" in resp.json()["detail"]
        runner.assert_not_called()

    def test_other_step_on_same_deck_is_409(self, test_client, test_db):
        m = _deck(test_db, rendered=True)
        _in_flight(
            test_db, CrawlJobType.EDU_RENDER_SLIDES, {"material_id": m.id, "positions": None}
        )

        with patch("app.api.edu.run_edu_job"):
            resp = test_client.post(VIDEO_URL.format(id=m.id))

        assert resp.status_code == 409
        assert "EDU_RENDER_SLIDES" in resp.json()["detail"]

    def test_job_on_another_deck_does_not_block(self, test_client, test_db):
        other = _deck(test_db, rendered=True)
        m = _deck(test_db, rendered=True)
        _in_flight(test_db, CrawlJobType.EDU_ASSEMBLE_VIDEO, {"material_id": other.id})

        with patch("app.api.edu.run_edu_job") as runner:
            resp = test_client.post(VIDEO_URL.format(id=m.id))

        assert resp.status_code == 202
        runner.assert_called_once_with(resp.json()["id"])

    def test_stale_job_does_not_block(self, test_client, test_db):
        m = _deck(test_db, rendered=True)
        _in_flight(
            test_db,
            CrawlJobType.EDU_RENDER_SLIDES,
            {"material_id": m.id, "positions": None},
            age=STALE_AFTER + timedelta(minutes=5),
        )

        with patch("app.api.edu.run_edu_job") as runner:
            resp = test_client.post(VIDEO_URL.format(id=m.id))

        assert resp.status_code == 202
        runner.assert_called_once()

    def test_finished_job_does_not_block(self, test_client, test_db):
        m = _deck(test_db, rendered=True)
        done = _in_flight(test_db, CrawlJobType.EDU_ASSEMBLE_VIDEO, {"material_id": m.id})
        done.status = CrawlJobStatus.SUCCEEDED.value
        test_db.commit()

        with patch("app.api.edu.run_edu_job"):
            resp = test_client.post(VIDEO_URL.format(id=m.id))

        assert resp.status_code == 202
        assert resp.json()["id"] != done.id


# --- runner ------------------------------------------------------------------


@pytest.mark.usefixtures("edu_job_session")
class TestRunner:
    def test_frames_persist_per_slide_when_a_later_slide_fails(self, test_client, test_db):
        """A mid-deck failure keeps the frames already uploaded (progress commits)."""
        m = _deck(test_db, n=3)

        def fake(material, db, positions=None, progress=None):
            first = material.slides[0]
            first.image_url = "https://cdn.example/frames/1.png"
            progress(1, 3, "slide 1")
            raise RuntimeError("chromium crashed on slide 2")

        with patch("app.services.edu_jobs.render_material_slides", side_effect=fake):
            resp = test_client.post(RENDER_URL.format(id=m.id))

        job = _get_job(test_client, resp.json()["id"])
        assert job["status"] == "FAILED"
        assert "slide 2" in job["error"]
        assert job["units_done"] == 1
        test_db.expire_all()
        slides = test_db.get(EduMaterial, m.id).slides
        assert slides[0].image_url == "https://cdn.example/frames/1.png"
        assert slides[1].image_url is None

    def test_deleted_material_fails_the_job_without_raising(self, test_db):
        job = CrawlJob(
            job_type=CrawlJobType.EDU_ASSEMBLE_VIDEO.value, params={"material_id": 999999}
        )
        test_db.add(job)
        test_db.commit()

        run_edu_job(job.id)  # must not raise

        test_db.expire_all()
        refreshed = test_db.get(CrawlJob, job.id)
        assert refreshed.status == CrawlJobStatus.FAILED.value
        assert "no longer exists" in refreshed.error
        assert refreshed.finished_at is not None

    def test_missing_job_row_is_a_noop(self):
        run_edu_job(123456)  # logs and returns


# --- guide generation --------------------------------------------------------


@pytest.mark.usefixtures("edu_job_session")
class TestGenerateGuides:
    def test_missing_research_404(self, test_client, test_db):
        resp = test_client.post(GENERATE_URL, json={"research_id": 999999})
        assert resp.status_code == 404

    def test_unapproved_research_400(self, test_client, test_db):
        r = _research(test_db, status="PENDING")
        with patch("app.services.edu_jobs.generate_edu_materials") as svc:
            resp = test_client.post(GENERATE_URL, json={"research_id": r.id})
        assert resp.status_code == 400
        svc.assert_not_called()

    def test_success_job_carries_generate_response(self, test_client, test_db):
        r = _research(test_db)

        def fake(research, suggestion, db, force=False, progress=None):
            assert force is True
            progress(0, 2, "band 3rd-6th")
            material = EduMaterial(
                research_id=research.id,
                material_type="guide",
                grade_band="3rd-6th",
                title="Guide",
                content="x",
                status="PENDING",
            )
            db.add(material)
            db.commit()
            progress(2, 2, "done")
            return [material], [], ["7th-8th"]

        with patch("app.services.edu_jobs.generate_edu_materials", side_effect=fake):
            resp = test_client.post(GENERATE_URL, json={"research_id": r.id, "force": True})

        job = _get_job(test_client, resp.json()["id"])
        assert job["job_type"] == "EDU_GENERATE_GUIDES"
        assert job["status"] == "SUCCEEDED"
        assert job["current_unit"] == "done"
        stats = job["stats"]
        assert stats["research_id"] == r.id
        assert stats["suggestion_title"] == "Panamá Viejo"
        assert stats["materials_created"] == 1
        assert stats["grade_bands"] == ["3rd-6th"]
        assert stats["failed_bands"] == ["7th-8th"]

    def test_generation_in_flight_for_same_research_joins(self, test_client, test_db):
        r = _research(test_db)
        running = _in_flight(
            test_db, CrawlJobType.EDU_GENERATE_GUIDES, {"research_id": r.id, "force": False}
        )
        with patch("app.api.edu.run_edu_job") as runner:
            resp = test_client.post(GENERATE_URL, json={"research_id": r.id})
        assert resp.status_code == 202
        assert resp.json()["id"] == running.id
        runner.assert_not_called()
