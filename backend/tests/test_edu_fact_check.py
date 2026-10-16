"""Tests for the EDU_RESEARCH fact-check subject (#460, epic #459).

Edu research is the root of every excursion deliverable, so it runs the same
external tier as blog research. These tests cover the four seams the new
subject touches: the enum/CHECK vocabulary, the job dispatch, the corrections
target shape, and the kickoff endpoint.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_research import EduResearch
from app.models.edu_suggestion import EduSuggestion
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType
from app.models.fact_check import FactCheckClaim
from app.services.fact_check import (
    FactCheckJobInFlightError,
    _run_fact_check_for_subject,
    create_fact_check_job,
)
from app.services.fact_check_corrections import (
    CorrectionEdit,
    _content_targets,
    apply_corrections,
    load_correction_subject,
)
from sqlalchemy.exc import IntegrityError

RESEARCH_TEXT = "# Panamá Viejo\n\nThe city was founded in 1519 by Pedro Arias Dávila."

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Bypass admin token validation for this module (#54).

    The edu router gates auth via ``dependencies=[Depends(validate_admin_token)]``,
    so the dependency must be overridden rather than patched.
    """
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


def _make_research(db, content: str = RESEARCH_TEXT, status: str = "APPROVED") -> EduResearch:
    suggestion = EduSuggestion(
        title="Panamá Viejo",
        description="Test description",
        excursion_key="panama-viejo",
        grade_bands=["3rd-6th"],
        sub_topics=["A", "B"],
        status="APPROVED",
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)

    research = EduResearch(suggestion_id=suggestion.id, content=content, status=status)
    db.add(research)
    db.commit()
    db.refresh(research)
    return research


# --- Vocabulary ---------------------------------------------------------------


class TestSubjectVocabulary:
    def test_enum_carries_edu_research(self):
        assert FactCheckSubject.EDU_RESEARCH.value == "EDU_RESEARCH"
        assert "EDU_RESEARCH" in FactCheckSubject.values()

    def test_migration_0026_widened_the_constraint_to_edu_research(self):
        """A historical assertion about what 0026 did — it added EDU_RESEARCH to
        the three blog subjects.

        Deliberately NOT "equals the whole enum": later migrations widen the same
        constraint (0027 adds EDU_MATERIAL), so that phrasing would fail on every
        subsequent subject. The live model↔enum↔latest-migration agreement is
        asserted by the newest migration's own test.
        """
        path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "0026_edu_research_fact_check_subject.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0026", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert set(module._OLD_VALUES) == {"SUGGESTION", "RESEARCH", "ARTICLE"}
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {"EDU_RESEARCH"}
        assert set(module._NEW_VALUES) <= set(FactCheckSubject.values())

    def test_edu_research_claim_row_accepted(self, test_db):
        research = _make_research(test_db)
        test_db.add(
            FactCheckClaim(
                subject_type=FactCheckSubject.EDU_RESEARCH.value,
                subject_id=research.id,
                claim_text="The city was founded in 1519.",
                claim_type=FactClaimType.DATE.value,
                verdict=FactCheckVerdict.PENDING.value,
            )
        )
        test_db.commit()

        assert test_db.query(FactCheckClaim).count() == 1

    def test_off_vocabulary_subject_still_rejected(self, test_db):
        test_db.add(
            FactCheckClaim(
                subject_type="EDU_BANANA",
                subject_id=1,
                claim_text="x",
                claim_type=FactClaimType.DATE.value,
                verdict=FactCheckVerdict.PENDING.value,
            )
        )
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()


# --- Job dispatch -------------------------------------------------------------


class TestDispatch:
    @patch("app.services.fact_check._external_tier_check")
    def test_dispatches_to_the_external_tier_with_the_doc_content(self, mock_tier, test_db):
        research = _make_research(test_db)
        mock_tier.return_value = {"claims": []}
        job = CrawlJob(
            job_type=CrawlJobType.FACT_CHECK.value,
            params={
                "subject_type": FactCheckSubject.EDU_RESEARCH.value,
                "subject_id": research.id,
            },
        )

        stats = _run_fact_check_for_subject(test_db, job, None)

        mock_tier.assert_called_once()
        args = mock_tier.call_args.args
        assert args[0] == FactCheckSubject.EDU_RESEARCH.value
        assert args[1] == research.id
        assert args[2] == RESEARCH_TEXT
        assert stats["subject_type"] == FactCheckSubject.EDU_RESEARCH.value
        assert stats["subject_id"] == research.id

    def test_missing_research_raises(self, test_db):
        job = CrawlJob(
            job_type=CrawlJobType.FACT_CHECK.value,
            params={
                "subject_type": FactCheckSubject.EDU_RESEARCH.value,
                "subject_id": 999999,
            },
        )
        with pytest.raises(ValueError, match="not found or has no content"):
            _run_fact_check_for_subject(test_db, job, None)

    def test_empty_content_raises_rather_than_checking_nothing(self, test_db):
        research = _make_research(test_db, content="   ")
        job = CrawlJob(
            job_type=CrawlJobType.FACT_CHECK.value,
            params={
                "subject_type": FactCheckSubject.EDU_RESEARCH.value,
                "subject_id": research.id,
            },
        )
        with pytest.raises(ValueError, match="not found or has no content"):
            _run_fact_check_for_subject(test_db, job, None)


# --- Corrections --------------------------------------------------------------


class TestCorrections:
    def test_loads_the_edu_research_row(self, test_db):
        research = _make_research(test_db)

        loaded = load_correction_subject(
            FactCheckSubject.EDU_RESEARCH.value, research.id, test_db
        )

        assert loaded is not None
        assert loaded.id == research.id

    def test_targets_the_english_content(self, test_db):
        """Edu research is a single English markdown column — it must NOT fall
        through to the suggestion field-joining shape."""
        research = _make_research(test_db)

        targets = _content_targets(FactCheckSubject.EDU_RESEARCH.value, research)

        assert targets == [("en", RESEARCH_TEXT)]

    def test_apply_writes_back_to_content(self, test_db):
        """The write-back path is shared with blog research, which also touches
        ``content_es``. EduResearch has no such column, so this guards against an
        AttributeError that would only ever surface at runtime."""
        research = _make_research(test_db)
        claim = FactCheckClaim(
            subject_type=FactCheckSubject.EDU_RESEARCH.value,
            subject_id=research.id,
            claim_text="The city was founded in 1519.",
            claim_type=FactClaimType.DATE.value,
            verdict=FactCheckVerdict.INACCURATE.value,
            correction="Founded in 1519 by Pedrarias.",
            checked_via="WEB_SEARCH",
        )
        test_db.add(claim)
        test_db.commit()
        test_db.refresh(claim)

        result = apply_corrections(
            FactCheckSubject.EDU_RESEARCH.value,
            research,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="en",
                    find="Pedro Arias Dávila",
                    replace="Pedrarias Dávila",
                )
            ],
            test_db,
        )

        assert result["failed"] == []
        assert result["corrected_claim_ids"] == [claim.id]
        assert "Pedrarias Dávila" in research.content
        assert claim.verdict == FactCheckVerdict.CORRECTED.value


# --- Kickoff endpoint ---------------------------------------------------------


class TestKickoffEndpoint:
    @patch("app.api.edu.run_fact_check_job")
    def test_returns_202_and_schedules_the_job(self, mock_run, test_client, test_db):
        research = _make_research(test_db)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/research/{research.id}/fact-check"
        )

        assert response.status_code == 202
        data = response.json()
        assert data["job_type"] == "FACT_CHECK"
        assert data["status"] == "PENDING"
        mock_run.assert_called_once_with(data["id"])
        job = test_db.get(CrawlJob, data["id"])
        assert job.params == {"subject_type": "EDU_RESEARCH", "subject_id": research.id}

    @patch("app.api.edu.run_fact_check_job")
    def test_stray_mode_param_is_ignored(self, mock_run, test_client, test_db):
        # #751 removed the mode query param; an old ?mode=web_search still 202s
        # and runs the extract-only tier.
        research = _make_research(test_db)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/research/{research.id}/fact-check",
            params={"mode": "web_search"},
        )

        assert response.status_code == 202
        job = test_db.get(CrawlJob, response.json()["id"])
        assert "mode" not in job.params

    @patch("app.api.edu.run_fact_check_job")
    def test_duplicate_run_rejected(self, mock_run, test_client, test_db):
        research = _make_research(test_db)
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.FACT_CHECK.value,
                status=CrawlJobStatus.RUNNING.value,
                params={"subject_type": "EDU_RESEARCH", "subject_id": research.id},
            )
        )
        test_db.commit()

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/research/{research.id}/fact-check"
        )

        assert response.status_code == 409
        mock_run.assert_not_called()

    @patch("app.api.edu.run_fact_check_job")
    def test_blog_research_with_the_same_id_does_not_block(self, mock_run, test_client, test_db):
        """subject_id spaces are per-subject-type; a blog research job must not
        shadow the edu doc that happens to share its id."""
        research = _make_research(test_db)
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.FACT_CHECK.value,
                status=CrawlJobStatus.RUNNING.value,
                params={"subject_type": "RESEARCH", "subject_id": research.id},
            )
        )
        test_db.commit()

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/research/{research.id}/fact-check"
        )

        assert response.status_code == 202

    def test_not_found(self, test_client):
        response = test_client.post("/api/v1/admin/dashboard/edu/research/999999/fact-check")
        assert response.status_code == 404

    def test_empty_content_rejected(self, test_client, test_db):
        research = _make_research(test_db, content="   ")

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/research/{research.id}/fact-check"
        )

        assert response.status_code == 400


# --- Shared job-creation core -------------------------------------------------


class TestCreateFactCheckJob:
    def test_raises_in_flight_error_carrying_the_running_job(self, test_db):
        research = _make_research(test_db)
        running = CrawlJob(
            job_type=CrawlJobType.FACT_CHECK.value,
            status=CrawlJobStatus.RUNNING.value,
            params={"subject_type": "EDU_RESEARCH", "subject_id": research.id},
        )
        test_db.add(running)
        test_db.commit()

        with pytest.raises(FactCheckJobInFlightError) as excinfo:
            create_fact_check_job(test_db, FactCheckSubject.EDU_RESEARCH.value, research.id)

        assert excinfo.value.job.id == running.id
        assert str(running.id) in str(excinfo.value)

    def test_params_carry_only_the_subject(self, test_db):
        research = _make_research(test_db)

        job = create_fact_check_job(test_db, FactCheckSubject.EDU_RESEARCH.value, research.id)

        assert job.params == {
            "subject_type": "EDU_RESEARCH",
            "subject_id": research.id,
        }
