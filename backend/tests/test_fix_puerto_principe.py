"""Tests for scripts.fix_puerto_principe (#730)."""

from datetime import UTC, datetime

from app.models.fact_check import FactCheckClaim
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.verified_claim import VerifiedClaim
from app.services import fact_check
from scripts.fix_puerto_principe import (
    EDITS,
    NEW,
    OLD,
    ClearContext,
    Replace,
    plan_replace,
    run,
)


def _claim(db, article_id, text):
    row = FactCheckClaim(
        subject_type="ARTICLE",
        subject_id=article_id,
        claim_text=text,
        claim_type="PLACE",
        verdict="VERIFIED",
        checked_via="GROUNDING",
        checked_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    return row


def _kb(db, text):
    row = VerifiedClaim(
        claim_key=fact_check._normalize(text),
        claim_text=text,
        claim_type="PLACE",
        verdict="VERIFIED",
        source_method="DEEP_RESEARCH",
    )
    db.add(row)
    db.commit()
    return row


def _chunk(db, article_id, index, context):
    row = KnowledgeChunk(
        source_type="ARTICLE",
        source_id=article_id,
        article_id=article_id,
        language="es",
        chunk_index=index,
        content=f"chunk {index}",
        content_hash=f"{index:064d}",
        token_count=2,
        meta={"language": "es", "context": context},
    )
    db.add(row)
    db.commit()
    return row


def test_plan_replace_statuses():
    edit = Replace("research", 1, "content")
    status, new, detail = plan_replace(f"The raid on {OLD}.", edit)
    assert (status, new) == ("apply", f"The raid on {NEW}.")
    assert "before:" in detail and "after:" in detail

    assert plan_replace(f"The raid on {NEW}.", edit)[0] == "already applied"
    assert plan_replace(f"{OLD} and {OLD}", edit)[0] == "mismatch"  # count 1 expected
    assert plan_replace("", edit)[0] == "mismatch"
    assert plan_replace(None, edit)[0] == "mismatch"


def test_edits_only_target_the_cuban_town():
    for e in EDITS:
        if isinstance(e, Replace):
            assert OLD in e.old and NEW in e.new
            assert e.new == e.old.replace(OLD, NEW)
    # Port-au-Prince ("Puerto Príncipe" in Spanish) and Santiago del Príncipe never match.
    edit = Replace("articles", 1, "content_es")
    text = "Filadelfia, París, Puerto Príncipe; Santiago del Príncipe."
    assert plan_replace(text, edit)[0] == "already applied"
    assert plan_replace("Santiago del Príncipe", edit)[0] == "mismatch"


def test_spanish_edits_chain_on_one_field(test_db, article_factory):
    body = (
        f"Su operación tuvo como objetivo {OLD} (actual Camagüey). "
        f"El exitoso ataque a {OLD} demostró algo. La operación de {NEW} siguió."
    )
    article = article_factory()
    article.content_es = body
    test_db.commit()
    edits = [e for e in EDITS if e.table == "articles" and e.column == "content_es"]
    edits = [Replace("articles", article.id, e.column, e.old, e.new) for e in edits]

    applied = run(test_db, edits, execute=True)
    assert [o.status for o in applied["outcomes"]] == ["apply", "apply"]
    assert applied["narration"] == [article.id]
    test_db.expire_all()
    assert OLD not in test_db.get(type(article), article.id).content_es
    assert (
        "tuvo como objetivo Puerto Príncipe (actual"
        in test_db.get(type(article), article.id).content_es
    )

    again = run(test_db, edits, execute=True)
    assert [o.status for o in again["outcomes"]] == ["already applied"] * 2
    assert again["narration"] == []


def test_dry_run_writes_nothing_and_execute_is_idempotent(test_db, article_factory):
    article = article_factory(content=f"## The {OLD} Raid\n\nThe raid on {OLD} paid.")
    claim = _claim(test_db, article.id, f"Morgan raided {OLD}.")
    kb = _kb(test_db, f"The {OLD} raid yielded 50,000 pieces of eight.")
    stale = _chunk(test_db, article.id, 0, f"El ataque a {OLD} en 1668.")
    fine = _chunk(test_db, article.id, 1, "Morgan en Port Royal.")
    edits = [
        Replace("articles", article.id, "content", count=2),
        Replace("fact_check_claims", claim.id, "claim_text"),
        Replace("verified_claims", kb.id, "claim_text", rekey=True),
        ClearContext(article.id),
    ]

    preview = run(test_db, edits, execute=False)
    assert [o.status for o in preview["outcomes"]] == ["apply"] * 4
    assert preview["articles"] == [article.id]
    assert preview["kb"] is True
    assert preview["narration"] == []  # only content_es is narrated
    test_db.expire_all()
    assert OLD in test_db.get(type(article), article.id).content
    assert test_db.get(KnowledgeChunk, stale.id).meta["context"]

    applied = run(test_db, edits, execute=True)
    assert [o.status for o in applied["outcomes"]] == ["apply"] * 4
    test_db.expire_all()
    assert test_db.get(type(article), article.id).content == (
        f"## The {NEW} Raid\n\nThe raid on {NEW} paid."
    )
    assert test_db.get(FactCheckClaim, claim.id).claim_text == f"Morgan raided {NEW}."
    row = test_db.get(VerifiedClaim, kb.id)
    assert row.claim_text == f"The {NEW} raid yielded 50,000 pieces of eight."
    assert row.claim_key == "the puerto prncipe raid yielded 50000 pieces of eight"
    assert "context" not in test_db.get(KnowledgeChunk, stale.id).meta
    assert test_db.get(KnowledgeChunk, stale.id).meta["language"] == "es"
    assert test_db.get(KnowledgeChunk, fine.id).meta["context"] == "Morgan en Port Royal."

    again = run(test_db, edits, execute=True)
    assert [o.status for o in again["outcomes"]] == ["already applied"] * 4
    assert again["articles"] == [] and again["kb"] is False


def test_rekey_refuses_a_key_another_row_holds(test_db):
    _kb(test_db, f"Morgan raided {NEW}.")
    old = _kb(test_db, f"Morgan raided {OLD}.")
    out = run(test_db, [Replace("verified_claims", old.id, "claim_text", rekey=True)])
    assert out["outcomes"][0].status == "mismatch"
    assert "already belongs" in out["outcomes"][0].detail
    test_db.expire_all()
    assert test_db.get(VerifiedClaim, old.id).claim_text == f"Morgan raided {OLD}."
