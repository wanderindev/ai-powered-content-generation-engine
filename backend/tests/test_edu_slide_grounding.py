"""Tests for grounding a slide deck and its narration (#464, epic #459).

Two facts drive every test here:

1. ``edu_slides`` rows are the deck's source of truth — the renderer draws
   ``title``/``body`` and the TTS speaks ``narration``. ``EduMaterial.content``
   is a derived preview, so a correction landing there would never reach the
   audio or the video.
2. The deck is Spanish-native (#320). There is no English twin, so Spanish is
   both what gets grounded and what a FIND snippet anchors to.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType, MaterialType
from app.models.fact_check import FactCheckClaim
from app.services.fact_check import _run_fact_check_for_subject, edu_material_prose
from app.services.fact_check_corrections import (
    CorrectionEdit,
    _content_targets,
    _language_note,
    apply_corrections,
    is_spoken_subject,
)
from app.utils.slide_text import (
    apply_to_segments,
    count_in_segments,
    segments_prose,
    slide_segments,
    slides_prose,
    write_segments,
)

RESEARCH_TEXT = "# Panamá Viejo\n\nThe city was founded in 1519 by Pedrarias Dávila."

SLIDES = [
    {
        "position": 1,
        "title": "Panamá Viejo: la primera ciudad",
        "body": "Fundada en 1519",
        "narration": "Hoy visitamos Panamá Viejo, fundada en 1519 en la costa del Pacífico.",
    },
    {
        "position": 2,
        "title": "El saqueo",
        "body": "Morgan llegó en 1671",
        "narration": "En 1671 el pirata Henry Morgan saqueó la ciudad.",
    },
]

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


def _make_deck(db, *, slides=SLIDES, material_type=MaterialType.SLIDES.value):
    suggestion = EduSuggestion(
        title="Panamá Viejo",
        description="Test",
        excursion_key="panama-viejo",
        grade_bands=["3rd-6th"],
        sub_topics=["A"],
        status="APPROVED",
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)

    research = EduResearch(suggestion_id=suggestion.id, content=RESEARCH_TEXT, status="APPROVED")
    db.add(research)
    db.commit()
    db.refresh(research)

    material = EduMaterial(
        research_id=research.id,
        material_type=material_type,
        grade_band="3rd-6th",
        title="Panamá Viejo deck",
        content="derived preview",
        status="PENDING",
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    for spec in slides:
        db.add(
            EduSlide(
                material_id=material.id,
                position=spec["position"],
                template="bullets",
                title=spec["title"],
                body=spec["body"],
                narration=spec["narration"],
                image_query="panama viejo ruins",
            )
        )
    db.commit()
    db.refresh(material)
    return material, research


# --- Segment walker -----------------------------------------------------------


class TestSlideSegments:
    def test_segments_carry_slide_id_and_field_in_reading_order(self, test_db):
        material, _ = _make_deck(test_db)

        segments = slide_segments(material.slides)

        assert [field for _, field, _ in segments] == [
            "title",
            "body",
            "narration",
            "title",
            "body",
            "narration",
        ]
        # slide order follows position, which the relationship guarantees
        assert segments[0][2] == "Panamá Viejo: la primera ciudad"
        assert segments[3][2] == "El saqueo"

    def test_skips_empty_fields(self, test_db):
        material, _ = _make_deck(
            test_db,
            slides=[{"position": 1, "title": "Solo título", "body": "", "narration": None}],
        )

        segments = slide_segments(material.slides)

        assert [field for _, field, _ in segments] == ["title"]

    def test_prose_excludes_machine_values(self, test_db):
        material, _ = _make_deck(test_db)

        prose = slides_prose(material.slides)

        assert "Henry Morgan" in prose
        assert "panama viejo ruins" not in prose  # image_query is a hint, not prose

    def test_count_sums_across_segments(self, test_db):
        material, _ = _make_deck(test_db)
        segments = slide_segments(material.slides)

        # "1519" appears in slide 1's body and narration
        assert count_in_segments(segments, "1519") == 2
        assert count_in_segments(segments, "Henry Morgan") == 1
        assert count_in_segments(segments, "nope") == 0
        assert count_in_segments(segments, "") == 0

    def test_apply_returns_new_segments_without_touching_rows(self, test_db):
        material, _ = _make_deck(test_db)
        segments = slide_segments(material.slides)

        updated = apply_to_segments(segments, "Henry Morgan", "el capitán Henry Morgan")

        assert "el capitán Henry Morgan" in segments_prose(updated)
        # rows untouched until write_segments
        assert "el capitán" not in slides_prose(material.slides)

    def test_write_segments_lands_on_the_right_row_and_field(self, test_db):
        material, _ = _make_deck(test_db)
        slides = list(material.slides)
        segments = apply_to_segments(
            slide_segments(slides), "Henry Morgan", "el capitán Henry Morgan"
        )

        write_segments(slides, segments)

        assert slides[1].narration == "En 1671 el pirata el capitán Henry Morgan saqueó la ciudad."
        # the other slide is untouched
        assert slides[0].narration == SLIDES[0]["narration"]


# --- Prose selection by material type ----------------------------------------


class TestMaterialProse:
    def test_deck_prose_comes_from_slides_not_content(self, test_db):
        material, _ = _make_deck(test_db)

        prose = edu_material_prose(material)

        assert "Henry Morgan" in prose
        assert prose != material.content


# --- Grounding dispatch -------------------------------------------------------


def _job(subject_id):
    return CrawlJob(
        job_type=CrawlJobType.FACT_CHECK.value,
        params={
            "subject_type": FactCheckSubject.EDU_MATERIAL.value,
            "subject_id": subject_id,
        },
    )


class TestGroundingDispatch:
    @patch("app.services.fact_check.ground_subject")
    def test_grounds_slide_prose_against_the_full_research(self, mock_ground, test_db):
        material, _ = _make_deck(test_db)
        mock_ground.return_value = {"claims": []}

        _run_fact_check_for_subject(test_db, _job(material.id), None)

        args = mock_ground.call_args.args
        assert args[0] == FactCheckSubject.EDU_MATERIAL.value
        assert args[1] == material.id
        assert "Henry Morgan" in args[2]
        assert args[2] != material.content
        assert args[3] == RESEARCH_TEXT

    @patch("app.services.fact_check.ground_subject")
    def test_ignores_the_condensed_source_briefing(self, mock_ground, test_db):
        """The briefing is what the deck was generated from; grounding against it
        would ratify anything the condense pass dropped or distorted."""
        material, _ = _make_deck(test_db)
        material.source_briefing = "A condensed briefing that says the city fell in 1670."
        test_db.commit()
        mock_ground.return_value = {"claims": []}

        _run_fact_check_for_subject(test_db, _job(material.id), None)

        assert mock_ground.call_args.args[3] == RESEARCH_TEXT

    @patch("app.services.fact_check.ground_subject")
    def test_uses_the_slides_extraction_feature_tag(self, mock_ground, test_db):
        material, _ = _make_deck(test_db)
        mock_ground.return_value = {"claims": []}

        _run_fact_check_for_subject(test_db, _job(material.id), None)

        assert mock_ground.call_args.kwargs["feature"] == "fact_check_extract_edu_slides"

    def test_deck_with_no_slides_raises(self, test_db):
        material, _ = _make_deck(test_db, slides=[])

        with pytest.raises(ValueError, match="no slide rows"):
            _run_fact_check_for_subject(test_db, _job(material.id), None)

    def test_video_refused(self, test_db):
        material, _ = _make_deck(test_db, material_type=MaterialType.VIDEO.value)

        with pytest.raises(ValueError, match="render of its deck"):
            _run_fact_check_for_subject(test_db, _job(material.id), None)


# --- Prompt context -----------------------------------------------------------


class TestPromptContext:
    def test_deck_is_a_spoken_subject(self, test_db):
        material, _ = _make_deck(test_db)
        assert is_spoken_subject(FactCheckSubject.EDU_MATERIAL.value, material) is True

    def test_guide_is_not_spoken(self, test_db):
        material, _ = _make_deck(test_db, material_type=MaterialType.GUIDE.value)
        assert is_spoken_subject(FactCheckSubject.EDU_MATERIAL.value, material) is False

    def test_article_is_not_spoken(self):
        assert is_spoken_subject(FactCheckSubject.ARTICLE.value, SimpleNamespace()) is False

    def test_spanish_note_no_longer_claims_the_text_is_a_translation(self):
        """A deck is authored in Spanish, so telling the model to find the
        translation of an English original is a false premise."""
        note = _language_note("es")
        assert "SPANISH" in note
        assert "translation" not in note.lower()

    def test_spoken_note_added_only_for_decks(self):
        assert "spoken aloud" in _language_note("es", spoken=True)
        assert "spoken aloud" not in _language_note("es", spoken=False)
        # and it applies regardless of language
        assert "spoken aloud" in _language_note("en", spoken=True)


# --- Corrections --------------------------------------------------------------


class TestCorrections:
    def test_targets_spanish_slide_prose_only(self, test_db):
        material, _ = _make_deck(test_db)

        targets = _content_targets(FactCheckSubject.EDU_MATERIAL.value, material)

        assert [lang for lang, _ in targets] == ["es"]
        assert "Henry Morgan" in targets[0][1]

    def _claim(self, test_db, material):
        claim = FactCheckClaim(
            subject_type=FactCheckSubject.EDU_MATERIAL.value,
            subject_id=material.id,
            claim_text="Henry Morgan sacked Panamá Viejo.",
            claim_type=FactClaimType.EVENT.value,
            verdict=FactCheckVerdict.INACCURATE.value,
            correction="It was Henry Morgan, a privateer rather than a pirate.",
            checked_via="GROUNDING",
        )
        test_db.add(claim)
        test_db.commit()
        test_db.refresh(claim)
        return claim

    def test_apply_writes_to_the_slide_row_and_rederives_the_preview(self, test_db):
        """The whole point: the TTS reads the row, so the row must change — and
        the derived preview must be rebuilt rather than left stale."""
        material, _ = _make_deck(test_db)
        claim = self._claim(test_db, material)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="es",
                    find="el pirata Henry Morgan",
                    replace="el corsario Henry Morgan",
                )
            ],
            test_db,
        )

        assert result["failed"] == []
        assert result["corrected_claim_ids"] == [claim.id]
        slides = list(material.slides)
        assert slides[1].narration == "En 1671 el corsario Henry Morgan saqueó la ciudad."
        assert "el corsario Henry Morgan" in material.content
        assert material.content != "derived preview"
        assert claim.verdict == FactCheckVerdict.CORRECTED.value

    def test_apply_leaves_other_slides_untouched(self, test_db):
        material, _ = _make_deck(test_db)
        claim = self._claim(test_db, material)

        apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="es",
                    find="el pirata Henry Morgan",
                    replace="el corsario Henry Morgan",
                )
            ],
            test_db,
        )

        assert list(material.slides)[0].narration == SLIDES[0]["narration"]

    def test_phrase_in_both_body_and_narration_is_ambiguous(self, test_db):
        """'1519' is in slide 1's body and narration. Rewriting both on one edit
        would be a silent double change, so the claim fails instead."""
        material, _ = _make_deck(test_db)
        claim = self._claim(test_db, material)
        before = slides_prose(material.slides)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id, language="es", find="1519", replace="1519 exactamente"
                )
            ],
            test_db,
        )

        assert result["corrected_claim_ids"] == []
        assert "occurs 2 times" in result["failed"][0][2]
        assert slides_prose(material.slides) == before

    def test_unmatched_find_fails_without_touching_the_deck(self, test_db):
        material, _ = _make_deck(test_db)
        claim = self._claim(test_db, material)
        before = slides_prose(material.slides)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id, language="es", find="no está aquí", replace="x"
                )
            ],
            test_db,
        )

        assert result["corrected_claim_ids"] == []
        assert slides_prose(material.slides) == before
        assert claim.verdict == FactCheckVerdict.INACCURATE.value


# --- Endpoint -----------------------------------------------------------------


class TestKickoffEndpoint:
    @patch("app.api.edu.run_fact_check_job")
    def test_deck_returns_202(self, mock_run, test_client, test_db):
        material, _ = _make_deck(test_db)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 202
        mock_run.assert_called_once_with(response.json()["id"])

    @patch("app.api.edu.run_fact_check_job")
    def test_video_rejected(self, mock_run, test_client, test_db):
        material, _ = _make_deck(test_db, material_type=MaterialType.VIDEO.value)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 400
        assert "fact-check the deck" in response.json()["detail"]
        mock_run.assert_not_called()

    @patch("app.api.edu.run_fact_check_job")
    def test_empty_deck_rejected(self, mock_run, test_client, test_db):
        material, _ = _make_deck(test_db, slides=[])

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 400
        mock_run.assert_not_called()

    @patch("app.api.edu.run_fact_check_job")
    def test_duplicate_run_rejected(self, mock_run, test_client, test_db):
        material, _ = _make_deck(test_db)
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.FACT_CHECK.value,
                status=CrawlJobStatus.RUNNING.value,
                params={"subject_type": "EDU_MATERIAL", "subject_id": material.id},
            )
        )
        test_db.commit()

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 409
        mock_run.assert_not_called()


# --- List flag ----------------------------------------------------------------


class TestListFlag:
    def test_has_slides_reported_for_the_deck(self, test_client, test_db):
        material, _ = _make_deck(test_db)

        rows = test_client.get("/api/v1/admin/dashboard/edu/materials").json()

        row = next(r for r in rows if r["id"] == material.id)
        assert row["has_slides"] is True
        assert row["has_blocks"] is False

    def test_has_slides_false_for_an_empty_deck(self, test_client, test_db):
        material, _ = _make_deck(test_db, slides=[])

        rows = test_client.get("/api/v1/admin/dashboard/edu/materials").json()

        row = next(r for r in rows if r["id"] == material.id)
        assert row["has_slides"] is False
