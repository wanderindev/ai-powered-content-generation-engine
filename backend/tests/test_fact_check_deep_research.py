"""Tests for the deep-research fact-check loop API (#377).

These endpoints are the HTTP surface of the /fact-check-prompt and
/fact-check-process skills: compile NEEDS_DEEP_RESEARCH claims into a
verification prompt, and write a downloaded verdict document back. Pure DB
logic — no LLM calls to mock.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType
from app.models.fact_check import FactCheckClaim
from app.models.verified_claim import VerifiedClaim
from app.services.fact_check import (
    _DEEP_RESEARCH_PROMPT,
    DEEP_RESEARCH_BATCH_LIMIT,
    generate_deep_research_prompt,
    process_deep_research_verdicts,
)

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Bypass admin token validation via dependency override (#54)."""
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


def _pending_claim(
    test_db,
    *,
    subject_type=FactCheckSubject.SUGGESTION.value,
    subject_id=1,
    text="Panama Viejo was founded in 1519",
    verdict=FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
):
    claim = FactCheckClaim(
        subject_type=subject_type,
        subject_id=subject_id,
        claim_text=text,
        claim_type=FactClaimType.DATE.value,
        verdict=verdict,
        checked_via="WEB_SEARCH" if verdict != FactCheckVerdict.PENDING.value else None,
    )
    test_db.add(claim)
    test_db.flush()
    return claim


# --- generate_deep_research_prompt (service) ---------------------------------


class TestGeneratePrompt:
    def test_returns_none_when_nothing_pending(self, test_db):
        assert generate_deep_research_prompt(test_db) is None

    def test_compiles_pending_claims_by_db_id(self, test_db):
        c1 = _pending_claim(test_db, subject_id=1, text="Claim one")
        c2 = _pending_claim(test_db, subject_id=2, text="Claim two")
        # Non-pending verdicts must not ride along.
        _pending_claim(test_db, verdict=FactCheckVerdict.VERIFIED.value)
        _pending_claim(test_db, verdict=FactCheckVerdict.PENDING.value)

        batch = generate_deep_research_prompt(test_db)
        assert batch["claim_ids"] == [c1.id, c2.id]
        assert batch["pending_total"] == 2
        assert f"CLAIM {c1.id} (DATE): Claim one" in batch["prompt_text"]
        assert f"CLAIM {c2.id} (DATE): Claim two" in batch["prompt_text"]
        assert f"REF: fact_check_{batch['batch_id']}" in batch["prompt_text"]
        assert "## Verdicts" in batch["prompt_text"]

    def test_batch_capped_but_pending_total_full(self, test_db):
        for i in range(DEEP_RESEARCH_BATCH_LIMIT + 5):
            _pending_claim(test_db, subject_id=i, text=f"Claim {i}")
        batch = generate_deep_research_prompt(test_db)
        assert len(batch["claim_ids"]) == DEEP_RESEARCH_BATCH_LIMIT
        assert batch["pending_total"] == DEEP_RESEARCH_BATCH_LIMIT + 5

    def test_scope_filters_to_one_subject(self, test_db):
        keep = _pending_claim(
            test_db, subject_type=FactCheckSubject.RESEARCH.value, subject_id=7
        )
        _pending_claim(test_db, subject_type=FactCheckSubject.SUGGESTION.value, subject_id=7)
        batch = generate_deep_research_prompt(
            test_db, FactCheckSubject.RESEARCH.value, 7
        )
        assert batch["claim_ids"] == [keep.id]


# --- process_deep_research_verdicts (service) --------------------------------


def _verdict_doc(lines):
    return "## Verdicts\n" + "\n".join(lines) + "\n\n## Notes\nIgnored discussion.\n"


class TestProcessVerdicts:
    def test_happy_path_writes_all_verdict_types(self, test_db):
        v = _pending_claim(test_db, subject_id=1, text="Verified claim")
        i = _pending_claim(test_db, subject_id=2, text="Inaccurate claim")
        d = _pending_claim(test_db, subject_id=3, text="Disputed claim")
        u = _pending_claim(test_db, subject_id=4, text="Unverifiable claim")

        result = process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {v.id} | VERIFIED | - | Castillero Calvo, Historia General, 2004",
                    f"VERDICT: {i.id} | INACCURATE | Founded in 1519, not 1520 | Mena García, 1992",
                    f"VERDICT: {d.id} | DISPUTED | Sources split on the burning | Araúz, 1997; Ward, 1993",
                    f"VERDICT: {u.id} | UNVERIFIABLE | - | -",
                ]
            ),
            test_db,
            claim_ids=[v.id, i.id, d.id, u.id],
        )

        assert result["written"] == 4
        assert result["skipped"] == []
        assert result["missing_claim_ids"] == []
        assert v.verdict == FactCheckVerdict.VERIFIED.value
        assert v.correction is None  # dash stored as NULL
        assert v.citation.startswith("Castillero")
        assert i.verdict == FactCheckVerdict.INACCURATE.value
        assert i.correction == "Founded in 1519, not 1520"
        assert u.verdict == FactCheckVerdict.UNVERIFIABLE.value
        assert u.citation is None
        assert all(
            c.checked_via == "DEEP_RESEARCH" and c.checked_at is not None
            for c in (v, i, d, u)
        )
        # KB fed with the three definitive verdicts, not UNVERIFIABLE.
        assert result["kb_upserts"] == 3
        assert test_db.query(VerifiedClaim).count() == 3

    def test_uncited_definitive_verdicts_skipped(self, test_db):
        claims = [_pending_claim(test_db, subject_id=n) for n in range(3)]
        result = process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {claims[0].id} | VERIFIED | - | -",
                    f"VERDICT: {claims[1].id} | INACCURATE | correction | -",
                    f"VERDICT: {claims[2].id} | DISPUTED | positions |",
                ]
            ),
            test_db,
        )
        assert result["written"] == 0
        assert len(result["skipped"]) == 3
        assert all(
            c.verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value for c in claims
        )

    def test_unknown_label_and_missing_row_skipped(self, test_db):
        claim = _pending_claim(test_db)
        result = process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {claim.id} | PROBABLY_FINE | - | source",
                    "VERDICT: 999999 | VERIFIED | - | source",
                ]
            ),
            test_db,
        )
        assert result["written"] == 0
        reasons = {cid: reason for cid, reason in result["skipped"]}
        assert "unknown verdict label" in reasons[claim.id]
        assert "no longer exists" in reasons[999999]

    def test_stale_row_not_overwritten(self, test_db):
        claim = _pending_claim(test_db, verdict=FactCheckVerdict.VERIFIED.value)
        result = process_deep_research_verdicts(
            _verdict_doc([f"VERDICT: {claim.id} | INACCURATE | changed | source"]),
            test_db,
        )
        assert result["written"] == 0
        assert "no longer pending" in result["skipped"][0][1]
        assert claim.verdict == FactCheckVerdict.VERIFIED.value

    def test_claim_ids_bound_the_batch(self, test_db):
        inside = _pending_claim(test_db, subject_id=1)
        outside = _pending_claim(test_db, subject_id=2)
        unanswered = _pending_claim(test_db, subject_id=3)
        result = process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {inside.id} | VERIFIED | - | source",
                    f"VERDICT: {outside.id} | VERIFIED | - | source",
                ]
            ),
            test_db,
            claim_ids=[inside.id, unanswered.id],
        )
        assert result["written"] == 1
        assert result["skipped"] == [(outside.id, "not in this batch's claim_ids")]
        assert result["missing_claim_ids"] == [unanswered.id]
        assert outside.verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value

    def test_duplicate_id_last_occurrence_wins(self, test_db):
        claim = _pending_claim(test_db)
        process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {claim.id} | INACCURATE | first | source A",
                    f"VERDICT: {claim.id} | VERIFIED | - | source B",
                ]
            ),
            test_db,
        )
        assert claim.verdict == FactCheckVerdict.VERIFIED.value
        assert claim.citation == "source B"

    def test_all_uncited_unverifiable_document_rejected(self, test_db):
        # Failed-run signature: web tools never came up but the run emitted
        # UNVERIFIABLE lines anyway (2026-07-13) — nothing may be written.
        claims = [_pending_claim(test_db, subject_id=n) for n in range(3)]
        result = process_deep_research_verdicts(
            _verdict_doc([f"VERDICT: {c.id} | UNVERIFIABLE | - | -" for c in claims]),
            test_db,
            claim_ids=[c.id for c in claims],
        )
        assert result["written"] == 0
        assert result["skipped"] == []
        assert "uncited UNVERIFIABLE" in result["rejected_reason"]
        assert result["missing_claim_ids"] == sorted(c.id for c in claims)
        assert all(
            c.verdict == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value for c in claims
        )
        assert test_db.query(VerifiedClaim).count() == 0

    def test_one_grounded_verdict_defuses_the_rejection(self, test_db):
        # A run that really searched can legitimately end with UNVERIFIABLEs —
        # any grounded verdict in the document proves the tools worked.
        u = _pending_claim(test_db, subject_id=1)
        v = _pending_claim(test_db, subject_id=2)
        result = process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {u.id} | UNVERIFIABLE | - | -",
                    f"VERDICT: {v.id} | VERIFIED | - | Castillero Calvo, 2004",
                ]
            ),
            test_db,
        )
        assert result["rejected_reason"] is None
        assert result["written"] == 2
        assert u.verdict == FactCheckVerdict.UNVERIFIABLE.value
        assert v.verdict == FactCheckVerdict.VERIFIED.value

    def test_duplicate_claim_text_across_subjects_collapses_in_the_kb(self, test_db):
        # The same fact extracted from two suggestions normalizes to one KB
        # key. With autoflush off, the second row didn't see the first pending
        # insert and both were INSERTed -> unique claim_key violation -> the
        # whole verdict upload 500'd (prod, 2026-07-14: "Napoleon invaded
        # Spain in 1808" from suggestions 53 and 54).
        a = _pending_claim(test_db, subject_id=53, text="Napoleon invaded Spain in 1808.")
        b = _pending_claim(test_db, subject_id=54, text="Napoleon invaded Spain in 1808!")
        other = _pending_claim(test_db, subject_id=55, text="Bolivar won at Boyaca in 1819")

        result = process_deep_research_verdicts(
            _verdict_doc(
                [
                    f"VERDICT: {a.id} | VERIFIED | - | Britannica, Peninsular War",
                    f"VERDICT: {b.id} | VERIFIED | - | Encyclopedia.com, Napoleon in Spain",
                    f"VERDICT: {other.id} | VERIFIED | - | Wikipedia, Battle of Boyaca",
                ]
            ),
            test_db,
        )

        # Both claims are recorded; the KB collapses them to one row.
        assert result["written"] == 3
        assert a.verdict == FactCheckVerdict.VERIFIED.value
        assert b.verdict == FactCheckVerdict.VERIFIED.value
        assert result["kb_upserts"] == 2
        assert test_db.query(VerifiedClaim).count() == 2
        # Last write wins for the shared key.
        napoleon = (
            test_db.query(VerifiedClaim)
            .filter(VerifiedClaim.claim_key == "napoleon invaded spain in 1808")
            .one()
        )
        assert napoleon.citation == "Encyclopedia.com, Napoleon in Spain"


# --- Endpoints ----------------------------------------------------------------


class TestGeneratePromptEndpoint:
    URL = "/api/v1/admin/dashboard/fact-check/deep-research/generate-prompt"

    def test_404_when_nothing_pending(self, test_client):
        response = test_client.post(self.URL, json={})
        assert response.status_code == 404

    def test_400_on_bad_scope(self, test_client):
        assert (
            test_client.post(self.URL, json={"subject_type": "ARTICLE"}).status_code
            == 400
        )
        assert test_client.post(self.URL, json={"subject_id": 3}).status_code == 400

    def test_returns_prompt_batch(self, test_client, test_db):
        claim = _pending_claim(test_db)
        test_db.commit()
        response = test_client.post(self.URL, json={})
        assert response.status_code == 200
        body = response.json()
        assert body["claim_ids"] == [claim.id]
        assert body["claim_count"] == 1
        assert body["pending_total"] == 1
        assert f"CLAIM {claim.id}" in body["prompt_text"]
        assert body["batch_id"]

    def test_scoped_request(self, test_client, test_db):
        claim = _pending_claim(
            test_db, subject_type=FactCheckSubject.RESEARCH.value, subject_id=9
        )
        _pending_claim(test_db, subject_type=FactCheckSubject.SUGGESTION.value)
        test_db.commit()
        response = test_client.post(
            self.URL, json={"subject_type": "research", "subject_id": 9}
        )
        assert response.status_code == 200
        assert response.json()["claim_ids"] == [claim.id]


class TestUploadVerdictsEndpoint:
    URL = "/api/v1/admin/dashboard/fact-check/deep-research/verdicts"

    def test_400_when_no_verdict_lines(self, test_client):
        response = test_client.post(self.URL, json={"content": "no verdicts here"})
        assert response.status_code == 400

    def test_writes_and_reports(self, test_client, test_db):
        claim = _pending_claim(test_db)
        missing = _pending_claim(test_db, subject_id=2)
        test_db.commit()
        response = test_client.post(
            self.URL,
            json={
                "content": _verdict_doc(
                    [f"VERDICT: {claim.id} | VERIFIED | - | Castillero Calvo, 2004"]
                ),
                "claim_ids": [claim.id, missing.id],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["written"] == 1
        assert body["skipped"] == []
        assert body["missing_claim_ids"] == [missing.id]
        assert body["kb_upserts"] == 1

        test_db.expire_all()
        stored = test_db.get(FactCheckClaim, claim.id)
        assert stored.verdict == FactCheckVerdict.VERIFIED.value
        assert stored.checked_via == "DEEP_RESEARCH"

    def test_400_when_document_is_a_failed_run(self, test_client, test_db):
        claims = [_pending_claim(test_db, subject_id=n) for n in range(2)]
        test_db.commit()
        response = test_client.post(
            self.URL,
            json={
                "content": _verdict_doc(
                    [f"VERDICT: {c.id} | UNVERIFIABLE | - | -" for c in claims]
                ),
                "claim_ids": [c.id for c in claims],
            },
        )
        assert response.status_code == 400
        assert "rejected" in response.json()["detail"]
        test_db.expire_all()
        assert all(
            test_db.get(FactCheckClaim, c.id).verdict
            == FactCheckVerdict.NEEDS_DEEP_RESEARCH.value
            for c in claims
        )


# --- Prompt / skill lockstep guard (#389) -------------------------------------


class TestPromptSkillLockstep:
    def test_prompt_template_matches_skill(self):
        """The endpoint and the /fact-check-prompt skill must emit the same
        contract — #389 made the wording mode-agnostic in both. Compares the
        invariant body of the template (between the intro and the CLAIMS
        placeholder) against the skill file verbatim."""
        skill_text = None
        for parent in Path(__file__).resolve().parents:
            candidate = parent / ".claude" / "skills" / "fact-check-prompt" / "SKILL.md"
            if candidate.exists():
                skill_text = candidate.read_text()
                break
        if skill_text is None:
            pytest.skip("skill file not available (test container mounts backend/ only)")

        start = _DEEP_RESEARCH_PROMPT.index("You are verifying")
        end = _DEEP_RESEARCH_PROMPT.index("CLAIMS:")
        invariant = _DEEP_RESEARCH_PROMPT[start:end].strip()
        assert invariant in skill_text
