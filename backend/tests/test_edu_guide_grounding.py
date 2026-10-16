"""Tests for grounding study guides against their edu_research (#461, epic #459).

The load-bearing fact here is that ``EduMaterial.content_blocks`` is the guide's
source of truth — the branded PDF renders from it, and ``content`` is only a
markdown projection. So both the checked text and the correction write-back must
go through the blocks; a correction landing on ``content`` would never reach the
PDF and would be clobbered on the next regeneration.
"""

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_suggestion import EduSuggestion
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType, MaterialType
from app.models.fact_check import FactCheckClaim
from app.services.fact_check import (
    _run_fact_check_for_subject,
    edu_material_source_research,
)
from app.services.fact_check_corrections import (
    CorrectionEdit,
    _content_targets,
    apply_corrections,
    load_correction_subject,
)
from app.utils.guide_blocks import apply_to_blocks, blocks_prose, count_in_blocks

RESEARCH_TEXT = "# Panamá Viejo\n\nThe city was founded in 1519 by Pedrarias Dávila."

# A block tree exercising every prose shape the walker must reach: plain text,
# list items, nested event dicts, and the callout term/definition pairs.
BLOCKS = [
    {"type": "cover", "title": "Panama Viejo", "subtitle": "A study guide"},
    {"type": "section", "title": "Founding"},
    {"type": "paragraph", "text": "The city was founded in 1519."},
    {"type": "bullets", "items": ["Built on the Pacific", "Sacked in 1671"]},
    {
        "type": "timeline",
        "events": [{"label": "1519", "text": "Foundation of the city"}],
    },
    {
        "type": "callout",
        "label": "Key Terms",
        "terms": [{"term": "Audiencia", "definition": "A colonial court"}],
    },
    {"type": "image", "caption": "The cathedral tower", "image_query": "panama viejo tower"},
]

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


_DEFAULT = object()  # distinguishes "not given" from an explicit None


def _make_guide(db, *, blocks=_DEFAULT, blocks_es=None, material_type=MaterialType.GUIDE.value):
    suggestion = EduSuggestion(
        title="Panamá Viejo",
        description="Test description",
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
        title="Panama Viejo guide",
        content="projected markdown",
        content_blocks=BLOCKS if blocks is _DEFAULT else blocks,
        content_blocks_es=blocks_es,
        status="PENDING",
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material, research


# --- Block prose walker -------------------------------------------------------


class TestBlockProse:
    def test_collects_every_prose_shape(self):
        prose = blocks_prose(BLOCKS)

        for expected in [
            "Panama Viejo",
            "A study guide",
            "Founding",
            "The city was founded in 1519.",
            "Built on the Pacific",
            "Sacked in 1671",
            "1519",
            "Foundation of the city",
            "Key Terms",
            "Audiencia",
            "A colonial court",
            "The cathedral tower",
        ]:
            assert expected in prose

    def test_excludes_machine_valued_keys(self):
        """An 'image_query' is a hint for the human picking a photo, not prose —
        correcting it would silently change what the editor is told to find."""
        assert "panama viejo tower" not in blocks_prose(BLOCKS)

    def test_carries_no_markdown_decoration(self):
        """The projection adds '- ', '#', '_' etc. A FIND generated against the
        projection would not match the stored block string."""
        prose = blocks_prose(BLOCKS)
        assert "- Built on the Pacific" not in prose
        assert "# Panama Viejo" not in prose

    def test_handles_empty_and_none(self):
        assert blocks_prose(None) == ""
        assert blocks_prose([]) == ""

    def test_count_matches_across_leaves(self):
        blocks = [
            {"type": "paragraph", "text": "founded in 1519"},
            {"type": "bullets", "items": ["also founded in 1519"]},
        ]
        assert count_in_blocks(blocks, "founded in 1519") == 2
        assert count_in_blocks(blocks, "nope") == 0
        assert count_in_blocks(blocks, "") == 0

    def test_apply_replaces_and_does_not_mutate_the_input(self):
        updated = apply_to_blocks(BLOCKS, "founded in 1519", "founded in 1519 by Pedrarias")

        assert updated[2]["text"] == "The city was founded in 1519 by Pedrarias."
        # original untouched
        assert BLOCKS[2]["text"] == "The city was founded in 1519."

    def test_apply_reaches_nested_and_list_values(self):
        updated = apply_to_blocks(BLOCKS, "Sacked in 1671", "Sacked by Morgan in 1671")
        assert updated[3]["items"][1] == "Sacked by Morgan in 1671"

        updated = apply_to_blocks(BLOCKS, "A colonial court", "A colonial high court")
        assert updated[5]["terms"][0]["definition"] == "A colonial high court"

    def test_apply_leaves_machine_keys_alone(self):
        updated = apply_to_blocks(BLOCKS, "panama viejo tower", "something else")
        assert updated[6]["image_query"] == "panama viejo tower"


# --- Vocabulary ---------------------------------------------------------------


class TestSubjectVocabulary:
    def test_enum_carries_edu_material(self):
        assert "EDU_MATERIAL" in FactCheckSubject.values()

    def test_migration_0027_matches_the_enum(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "0027_edu_material_fact_check_subject.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0027", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert set(module._NEW_VALUES) == set(FactCheckSubject.values())

    def test_single_migration_head(self):
        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        revisions, downs = set(), set()
        for file in versions.glob("*.py"):
            text = file.read_text()
            rev = re.search(r'^revision = "([^"]+)"', text, re.M)
            down = re.search(r'^down_revision = "([^"]+)"', text, re.M)
            if rev:
                revisions.add(rev.group(1))
            if down:
                downs.add(down.group(1))
        assert revisions - downs == {"0051_mcp_approval_requests"}


# --- Source research resolution -----------------------------------------------


class TestSourceResearch:
    def test_resolves_edu_research(self, test_db):
        material, research = _make_guide(test_db)
        assert edu_material_source_research(material, test_db) == research.content

    def test_missing_research_raises(self, test_db):
        material, research = _make_guide(test_db)
        research.content = "  "
        test_db.commit()

        with pytest.raises(ValueError, match="not found or has no content"):
            edu_material_source_research(material, test_db)


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
    def test_grounds_block_prose_against_the_research(self, mock_ground, test_db):
        material, research = _make_guide(test_db)
        mock_ground.return_value = {"claims": []}

        _run_fact_check_for_subject(test_db, _job(material.id), None)

        mock_ground.assert_called_once()
        args = mock_ground.call_args.args
        assert args[0] == FactCheckSubject.EDU_MATERIAL.value
        assert args[1] == material.id
        # the block prose, NOT the stored markdown projection
        assert "The city was founded in 1519." in args[2]
        assert args[2] != material.content
        assert args[3] == RESEARCH_TEXT

    def test_missing_material_raises(self, test_db):
        with pytest.raises(ValueError, match="not found"):
            _run_fact_check_for_subject(test_db, _job(999999), None)

    def test_ungroundable_material_refused(self, test_db):
        """A deck IS groundable as of #464; a video is not — it renders its
        deck's narration, so the deck is where its facts live."""
        material, _ = _make_guide(test_db, material_type=MaterialType.VIDEO.value)

        with pytest.raises(ValueError, match="render of its deck"):
            _run_fact_check_for_subject(test_db, _job(material.id), None)

    def test_legacy_blockless_guide_refused(self, test_db):
        """Grounding the markdown projection would produce claims no correction
        could ever apply to."""
        material, _ = _make_guide(test_db, blocks=None)

        with pytest.raises(ValueError, match="no content_blocks"):
            _run_fact_check_for_subject(test_db, _job(material.id), None)


# --- Corrections --------------------------------------------------------------


class TestCorrections:
    def test_loads_the_material(self, test_db):
        material, _ = _make_guide(test_db)
        loaded = load_correction_subject(
            FactCheckSubject.EDU_MATERIAL.value, material.id, test_db
        )
        assert loaded is not None and loaded.id == material.id

    def test_targets_block_prose_english_only_when_monolingual(self, test_db):
        material, _ = _make_guide(test_db)

        targets = _content_targets(FactCheckSubject.EDU_MATERIAL.value, material)

        assert [lang for lang, _ in targets] == ["en"]
        assert "The city was founded in 1519." in targets[0][1]

    def test_targets_both_languages_when_translated(self, test_db):
        es_blocks = [{"type": "paragraph", "text": "La ciudad fue fundada en 1519."}]
        material, _ = _make_guide(test_db, blocks_es=es_blocks)

        targets = _content_targets(FactCheckSubject.EDU_MATERIAL.value, material)

        assert [lang for lang, _ in targets] == ["en", "es"]
        assert "La ciudad fue fundada en 1519." in targets[1][1]

    def _claim(self, test_db, material):
        claim = FactCheckClaim(
            subject_type=FactCheckSubject.EDU_MATERIAL.value,
            subject_id=material.id,
            claim_text="The city was founded in 1519.",
            claim_type=FactClaimType.DATE.value,
            verdict=FactCheckVerdict.INACCURATE.value,
            correction="Founded in 1519 by Pedrarias Dávila.",
            checked_via="GROUNDING",
        )
        test_db.add(claim)
        test_db.commit()
        test_db.refresh(claim)
        return claim

    def test_apply_edits_the_blocks_and_rederives_content(self, test_db):
        """The whole point: the PDF renders from content_blocks, so the block
        must change and ``content`` must be re-projected from it."""
        material, _ = _make_guide(test_db)
        claim = self._claim(test_db, material)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="en",
                    find="founded in 1519",
                    replace="founded in 1519 by Pedrarias",
                )
            ],
            test_db,
        )

        assert result["failed"] == []
        assert result["corrected_claim_ids"] == [claim.id]
        # the source of truth changed
        paragraph = next(b for b in material.content_blocks if b["type"] == "paragraph")
        assert paragraph["text"] == "The city was founded in 1519 by Pedrarias."
        # and the projection was rebuilt from it, not left stale
        assert "founded in 1519 by Pedrarias" in material.content
        assert material.content != "projected markdown"
        assert claim.verdict == FactCheckVerdict.CORRECTED.value

    def test_apply_updates_spanish_blocks_and_projection(self, test_db):
        es_blocks = [{"type": "paragraph", "text": "La ciudad fue fundada en 1519."}]
        material, _ = _make_guide(test_db, blocks_es=es_blocks)
        claim = self._claim(test_db, material)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="en",
                    find="founded in 1519",
                    replace="founded in 1519 by Pedrarias",
                ),
                CorrectionEdit(
                    claim_id=claim.id,
                    language="es",
                    find="fundada en 1519",
                    replace="fundada en 1519 por Pedrarias",
                ),
            ],
            test_db,
        )

        assert result["failed"] == []
        assert material.content_blocks_es[0]["text"] == "La ciudad fue fundada en 1519 por Pedrarias."
        assert "fundada en 1519 por Pedrarias" in material.content_es

    def test_unmatched_find_fails_the_claim_without_touching_blocks(self, test_db):
        material, _ = _make_guide(test_db)
        claim = self._claim(test_db, material)
        before = blocks_prose(material.content_blocks)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="en",
                    find="text that is not in the guide",
                    replace="whatever",
                )
            ],
            test_db,
        )

        assert result["corrected_claim_ids"] == []
        assert result["failed"]
        assert blocks_prose(material.content_blocks) == before
        assert claim.verdict == FactCheckVerdict.INACCURATE.value

    def test_ambiguous_find_fails_the_claim(self, test_db):
        blocks = [
            {"type": "paragraph", "text": "founded in 1519"},
            {"type": "bullets", "items": ["founded in 1519"]},
        ]
        material, _ = _make_guide(test_db, blocks=blocks)
        claim = self._claim(test_db, material)

        result = apply_corrections(
            FactCheckSubject.EDU_MATERIAL.value,
            material,
            [
                CorrectionEdit(
                    claim_id=claim.id,
                    language="en",
                    find="founded in 1519",
                    replace="founded in 1520",
                )
            ],
            test_db,
        )

        assert result["corrected_claim_ids"] == []
        assert "occurs 2 times" in result["failed"][0][2]


# --- Kickoff endpoint ---------------------------------------------------------


class TestKickoffEndpoint:
    @patch("app.api.edu.run_fact_check_job")
    def test_returns_202(self, mock_run, test_client, test_db):
        material, _ = _make_guide(test_db)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 202
        data = response.json()
        mock_run.assert_called_once_with(data["id"])
        job = test_db.get(CrawlJob, data["id"])
        # grounding takes no mode — it is not an external tier
        assert job.params == {
            "subject_type": "EDU_MATERIAL",
            "subject_id": material.id,
        }

    @patch("app.api.edu.run_fact_check_job")
    def test_video_rejected_with_a_pointer_to_its_deck(self, mock_run, test_client, test_db):
        material, _ = _make_guide(test_db, material_type=MaterialType.VIDEO.value)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 400
        assert "fact-check the deck" in response.json()["detail"]
        mock_run.assert_not_called()

    @patch("app.api.edu.run_fact_check_job")
    def test_blockless_guide_rejected(self, mock_run, test_client, test_db):
        material, _ = _make_guide(test_db, blocks=None)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/fact-check"
        )

        assert response.status_code == 400
        mock_run.assert_not_called()

    @patch("app.api.edu.run_fact_check_job")
    def test_duplicate_run_rejected(self, mock_run, test_client, test_db):
        material, _ = _make_guide(test_db)
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

    def test_not_found(self, test_client):
        response = test_client.post("/api/v1/admin/dashboard/edu/materials/999999/fact-check")
        assert response.status_code == 404
