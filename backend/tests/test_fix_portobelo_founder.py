"""Tests for scripts.fix_portobelo_founder (#714)."""

from datetime import UTC, datetime

from app.models.fact_check import FactCheckClaim
from app.models.verified_claim import VerifiedClaim
from app.services import fact_check
from scripts.fix_portobelo_founder import (
    EDITS,
    NEW,
    OLD,
    Delete,
    Replace,
    plan_replace,
    run,
)


def _claim(db, article_id, text):
    row = FactCheckClaim(
        subject_type="ARTICLE",
        subject_id=article_id,
        claim_text=text,
        claim_type="PERSON",
        verdict="VERIFIED",
        checked_via="GROUNDING",
        checked_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    return row


def test_plan_replace_statuses():
    edit = Replace("research", 1, "content")
    status, new, detail = plan_replace(f"Founded by {OLD}.", edit)
    assert (status, new) == ("apply", f"Founded by {NEW}.")
    assert "before:" in detail and "after:" in detail

    assert plan_replace(f"Founded by {NEW}.", edit)[0] == "already applied"
    assert plan_replace(f"{OLD} and {OLD}", edit)[0] == "mismatch"  # count 1 expected
    assert plan_replace("", edit)[0] == "mismatch"
    assert plan_replace(None, edit)[0] == "mismatch"


def test_never_touches_other_velardes_or_valverdes():
    edit = Replace("research", 1, "content")
    text = "Óscar Vargas Velarde wrote it; Friar Vicente de Valverde was at Cajamarca."
    assert plan_replace(text, edit)[0] == "mismatch"
    for e in EDITS:
        old = e.expected if isinstance(e, Delete) else e.old
        assert "Francisco Velarde y Mercado" in old


def test_dry_run_writes_nothing_and_execute_is_idempotent(test_db, article_factory):
    article = article_factory(outline=f"- 1597: {OLD} founds Portobelo", content=f"By {NEW}.")
    keep = _claim(test_db, article.id, f"San Felipe was founded by Governor {OLD}.")
    gone = _claim(test_db, article.id, f"{OLD} was Governor.")
    kb = VerifiedClaim(
        claim_key=fact_check._normalize("Portobelo was founded in 1597."),
        claim_text="Portobelo was founded in 1597.",
        claim_type="DATE",
        verdict="VERIFIED",
        correction=f"Founded 20 March 1597 by {OLD}, after Vargas Velarde's count.",
        source_method="DEEP_RESEARCH",
    )
    test_db.add(kb)
    test_db.commit()
    kb_key = kb.claim_key
    edits = [
        Replace("articles", article.id, "outline"),
        Replace(
            "fact_check_claims",
            keep.id,
            "claim_text",
            old=f"Governor {OLD}.",
            new=f"{NEW}.",
        ),
        Delete("fact_check_claims", gone.id, "claim_text", expected=f"{OLD} was Governor."),
        Replace("verified_claims", kb.id, "correction"),
    ]

    preview = run(test_db, edits, execute=False)
    assert [o.status for o in preview["outcomes"]] == ["apply"] * 4
    assert preview["articles"] == [article.id]
    assert preview["kb"] is True
    test_db.expire_all()
    assert test_db.get(FactCheckClaim, gone.id) is not None
    assert OLD in test_db.get(type(article), article.id).outline

    applied = run(test_db, edits, execute=True)
    assert [o.status for o in applied["outcomes"]] == ["apply"] * 4
    test_db.expire_all()
    assert test_db.get(type(article), article.id).outline == f"- 1597: {NEW} founds Portobelo"
    assert test_db.get(type(article), article.id).content == f"By {NEW}."
    assert test_db.get(FactCheckClaim, keep.id).claim_text == f"San Felipe was founded by {NEW}."
    assert test_db.get(FactCheckClaim, gone.id) is None
    row = test_db.get(VerifiedClaim, kb.id)
    assert row.correction == f"Founded 20 March 1597 by {NEW}, after Vargas Velarde's count."
    assert row.claim_key == kb_key

    again = run(test_db, edits, execute=True)
    assert [o.status for o in again["outcomes"]] == ["already applied"] * 4
    assert again["articles"] == [] and again["kb"] is False
