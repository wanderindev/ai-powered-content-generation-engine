"""Tests for the edu pipeline's fact-check gates (#462, epic #459).

The policy, decided with the operator:

* INACCURATE blocks every gated step, internal or public.
* NEEDS_DEEP_RESEARCH / DISPUTED block only the public steps — they are open
  questions, and hard-blocking generation behind a queue that takes hours would
  stall the pipeline.
* CORRECTED and VERIFIED never block.
* Never-fact-checked never blocks; the admin UI warns on publish instead.

Plus the invariant that makes any of it meaningful: replacing a material's text
invalidates its claims, so the gate can never read verdicts about sentences that
no longer exist.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType, MaterialType
from app.models.fact_check import FactCheckClaim
from app.services.fact_check_gate import (
    STEP_INTERNAL,
    STEP_PUBLIC,
    FactCheckGateError,
    blocking_verdicts,
    enforce_material_gate,
    invalidate_material_claims,
    material_verdict_counts,
)

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


def _make_material(db, *, material_type=MaterialType.GUIDE.value, status="APPROVED"):
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

    research = EduResearch(suggestion_id=suggestion.id, content="Research text", status="APPROVED")
    db.add(research)
    db.commit()
    db.refresh(research)

    material = EduMaterial(
        research_id=research.id,
        material_type=material_type,
        grade_band="3rd-6th",
        title="Material",
        content="Some content",
        content_blocks=[{"type": "paragraph", "text": "The city was founded in 1519."}],
        content_es="Contenido",
        content_blocks_es=[{"type": "paragraph", "text": "Fundada en 1519."}],
        status=status,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


def _add_claim(db, material_id, verdict):
    db.add(
        FactCheckClaim(
            subject_type=FactCheckSubject.EDU_MATERIAL.value,
            subject_id=material_id,
            claim_text="A claim",
            claim_type=FactClaimType.DATE.value,
            verdict=verdict,
            checked_via="GROUNDING",
        )
    )
    db.commit()


# --- Policy -------------------------------------------------------------------


class TestPolicy:
    def test_internal_blocks_only_known_errors(self):
        assert blocking_verdicts(STEP_INTERNAL) == (FactCheckVerdict.INACCURATE.value,)

    def test_public_also_blocks_open_questions(self):
        blocking = set(blocking_verdicts(STEP_PUBLIC))
        assert blocking == {
            FactCheckVerdict.INACCURATE.value,
            FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
            FactCheckVerdict.DISPUTED.value,
        }

    def test_corrected_and_verified_never_block(self):
        for step in (STEP_INTERNAL, STEP_PUBLIC):
            assert FactCheckVerdict.CORRECTED.value not in blocking_verdicts(step)
            assert FactCheckVerdict.VERIFIED.value not in blocking_verdicts(step)


class TestEnforce:
    def test_unchecked_material_passes(self, test_db):
        material = _make_material(test_db)
        # no claims at all
        enforce_material_gate(test_db, material.id, STEP_PUBLIC, action="publish")

    def test_inaccurate_blocks_internal(self, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.INACCURATE.value)

        with pytest.raises(FactCheckGateError) as excinfo:
            enforce_material_gate(test_db, material.id, STEP_INTERNAL, action="do the thing")

        assert "do the thing" in str(excinfo.value)
        assert "1 INACCURATE" in str(excinfo.value)
        assert excinfo.value.counts == {FactCheckVerdict.INACCURATE.value: 1}

    def test_unresolved_passes_internal_but_blocks_public(self, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.NEEDS_DEEP_RESEARCH.value)

        # internal proceeds — the deep-research queue must not stall generation
        enforce_material_gate(test_db, material.id, STEP_INTERNAL, action="generate audio")

        with pytest.raises(FactCheckGateError, match="NEEDS_DEEP_RESEARCH"):
            enforce_material_gate(test_db, material.id, STEP_PUBLIC, action="publish")

    def test_disputed_blocks_public_only(self, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.DISPUTED.value)

        enforce_material_gate(test_db, material.id, STEP_INTERNAL, action="generate audio")
        with pytest.raises(FactCheckGateError, match="DISPUTED"):
            enforce_material_gate(test_db, material.id, STEP_PUBLIC, action="publish")

    def test_corrected_does_not_block(self, test_db):
        """CORRECTED is the terminal green state: the claim is still wrong in the
        world, but the content no longer asserts it (#387)."""
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.CORRECTED.value)
        _add_claim(test_db, material.id, FactCheckVerdict.VERIFIED.value)

        enforce_material_gate(test_db, material.id, STEP_PUBLIC, action="publish")

    def test_message_names_every_offending_verdict(self, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.INACCURATE.value)
        _add_claim(test_db, material.id, FactCheckVerdict.DISPUTED.value)
        _add_claim(test_db, material.id, FactCheckVerdict.VERIFIED.value)

        with pytest.raises(FactCheckGateError) as excinfo:
            enforce_material_gate(test_db, material.id, STEP_PUBLIC, action="publish")

        message = str(excinfo.value)
        assert "1 DISPUTED" in message and "1 INACCURATE" in message
        assert "VERIFIED" not in message  # not an offender

    def test_counts_are_scoped_to_the_material(self, test_db):
        one = _make_material(test_db)
        two = _make_material(test_db)
        _add_claim(test_db, one.id, FactCheckVerdict.INACCURATE.value)

        assert material_verdict_counts(test_db, two.id) == {}
        enforce_material_gate(test_db, two.id, STEP_PUBLIC, action="publish")


# --- Claim invalidation -------------------------------------------------------


class TestInvalidation:
    def test_removes_only_this_material_s_claims(self, test_db):
        one = _make_material(test_db)
        two = _make_material(test_db)
        _add_claim(test_db, one.id, FactCheckVerdict.VERIFIED.value)
        _add_claim(test_db, two.id, FactCheckVerdict.VERIFIED.value)

        removed = invalidate_material_claims(test_db, one.id)

        assert removed == 1
        assert material_verdict_counts(test_db, one.id) == {}
        assert material_verdict_counts(test_db, two.id) == {
            FactCheckVerdict.VERIFIED.value: 1
        }

    def test_noop_when_never_checked(self, test_db):
        material = _make_material(test_db)
        assert invalidate_material_claims(test_db, material.id) == 0


# --- Endpoint wiring ----------------------------------------------------------


class TestGatedEndpoints:
    def test_publish_refused_on_unresolved_claims(self, test_client, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.NEEDS_DEEP_RESEARCH.value)

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/publish"
        )

        assert response.status_code == 409
        assert "NEEDS_DEEP_RESEARCH" in response.json()["detail"]
        test_db.refresh(material)
        assert material.published_at is None

    def test_publish_allowed_when_unchecked(self, test_client, test_db):
        material = _make_material(test_db)

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/publish"
        )

        assert response.status_code == 200
        test_db.refresh(material)
        assert material.published_at is not None

    def test_publish_allowed_when_all_corrected(self, test_client, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.CORRECTED.value)

        response = test_client.patch(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/publish"
        )

        assert response.status_code == 200

    def test_pdf_refused_on_inaccurate(self, test_client, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.INACCURATE.value)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/generate-pdf?language=es"
        )

        assert response.status_code == 409
        assert "generate the PDF" in response.json()["detail"]

    def test_pdf_allowed_on_unresolved(self, test_client, test_db):
        """An internal step must not stall behind the deep-research queue."""
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.NEEDS_DEEP_RESEARCH.value)

        with patch("app.api.edu.generate_guide_pdf", return_value="https://spaces/x.pdf"):
            response = test_client.post(
                f"/api/v1/admin/dashboard/edu/materials/{material.id}/generate-pdf?language=es"
            )

        assert response.status_code != 409

    def test_translate_refused_on_inaccurate(self, test_client, test_db):
        material = _make_material(test_db)
        material.content_es = None
        material.content_blocks_es = None
        test_db.commit()
        _add_claim(test_db, material.id, FactCheckVerdict.INACCURATE.value)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/translate"
        )

        assert response.status_code == 409
        assert "translate the guide" in response.json()["detail"]

    def test_audio_refused_on_inaccurate(self, test_client, test_db):
        material = _make_material(test_db, material_type=MaterialType.SLIDES.value)
        test_db.add(
            EduSlide(
                material_id=material.id,
                position=1,
                template="bullets",
                title="Slide",
                body="Body",
                narration="Narración de prueba.",
            )
        )
        test_db.commit()
        _add_claim(test_db, material.id, FactCheckVerdict.INACCURATE.value)

        response = test_client.post(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/generate-audio"
        )

        assert response.status_code == 409
        assert "generate narration audio" in response.json()["detail"]


class TestInvalidationWiring:
    def test_editing_guide_content_clears_its_claims(self, test_client, test_db):
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.VERIFIED.value)

        response = test_client.put(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/content",
            json={
                "content_blocks": [
                    {"type": "paragraph", "text": "Completely rewritten paragraph."}
                ]
            },
        )

        assert response.status_code == 200
        assert material_verdict_counts(test_db, material.id) == {}

    def test_stale_verified_claims_cannot_wave_edited_content_through(
        self, test_client, test_db
    ):
        """The failure this guards: edit a guide that passed its check, then
        publish on the strength of verdicts about text that no longer exists."""
        material = _make_material(test_db)
        _add_claim(test_db, material.id, FactCheckVerdict.VERIFIED.value)

        test_client.put(
            f"/api/v1/admin/dashboard/edu/materials/{material.id}/content",
            json={"content_blocks": [{"type": "paragraph", "text": "New text entirely."}]},
        )

        # The gate now sees an unchecked material, not a verified one — publish
        # still succeeds (unchecked is a UI warning, not a block) but nothing
        # claims the new text was verified.
        assert material_verdict_counts(test_db, material.id) == {}
