"""Claim chunks: article fact-check claims + the verified-claims KB (#625, epic #617)."""

from datetime import UTC, datetime

import pytest
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.fact_check import FactCheckClaim, FactCheckSubject, FactCheckVerdict
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.verified_claim import VerifiedClaim
from app.services import embeddings, fact_check
from app.services.knowledge import claims, indexer
from scripts import reindex_knowledge
from sqlalchemy import literal_column, select
from sqlalchemy.orm import sessionmaker

DIMS = 1024
_C = KnowledgeChunk.__table__.c


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.error: Exception | None = None

    def __call__(self, texts, *, feature="unlabeled"):
        assert feature == indexer.FEATURE
        if self.error:
            raise self.error
        self.calls.append(list(texts))
        return [[0.02] * DIMS for _ in texts]

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]


@pytest.fixture
def env(test_engine, monkeypatch):
    from app.core.config import get_settings

    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    for module in (indexer, claims, reindex_knowledge):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "embed_documents", fake)
    return fake


def _claim(db, article_id, text, verdict, correction=None, subject_type="ARTICLE"):
    row = FactCheckClaim(
        subject_type=subject_type,
        subject_id=article_id,
        claim_text=text,
        claim_type="DATE",
        verdict=verdict,
        correction=correction,
        checked_via="GROUNDING",
        checked_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _kb(db, text, verdict, correction=None):
    row = VerifiedClaim(
        claim_key=fact_check._normalize(text),
        claim_text=text,
        claim_type="DATE",
        verdict=verdict,
        correction=correction,
        citation="Castillero Calvo, 2004",
        source_method="DEEP_RESEARCH",
    )
    db.add(row)
    db.flush()
    return row


def _chunks(db, source_type, **where):
    db.expire_all()
    criteria = [_C.source_type == source_type]
    criteria += [_C[k] == v for k, v in where.items()]
    return db.execute(
        select(
            _C.source_id,
            _C.article_id,
            _C.language,
            _C.content,
            _C.meta,
            _C.embedding.is_not(None).label("embedded"),
        )
        .where(*criteria)
        .order_by(_C.source_id)
    ).all()


ALL_VERDICTS = [
    ("Panama Viejo was founded in 1519.", "VERIFIED", None),
    ("Balboa reached the Pacific in 1515.", "CORRECTED", "Balboa reached the Pacific in 1513."),
    ("The canal opened in 1915.", "INACCURATE", "The canal opened in 1914."),
    ("Morgan sacked the city in 1670.", "DISPUTED", "Sources give 1670 and 1671."),
    ("A mule train carried 300 bars.", "UNVERIFIABLE", None),
    ("The fair ran for 40 days.", "NEEDS_DEEP_RESEARCH", None),
    ("Portobelo had 9 forts.", "PENDING", None),
]


@pytest.fixture
def article_with_claims(test_db, article_factory):
    article = article_factory(title="Panama Viejo")
    rows = [_claim(test_db, article.id, *spec) for spec in ALL_VERDICTS]
    test_db.commit()
    return article, rows


# --- Selection ----------------------------------------------------------------


def test_only_verified_and_corrected_claims_are_indexed_as_facts(env, test_db, article_with_claims):
    article, rows = article_with_claims

    result = claims.index_article_claims(article.id)

    assert result.status == "indexed"
    assert result.source_type == "ARTICLE_CLAIM"
    chunks = _chunks(test_db, "ARTICLE_CLAIM", article_id=article.id)
    assert [c.content for c in chunks] == [
        "Panama Viejo was founded in 1519.",
        "Balboa reached the Pacific in 1513.",  # CORRECTED -> the correction text
    ]
    assert [c.source_id for c in chunks] == [rows[0].id, rows[1].id]
    assert {c.language for c in chunks} == {"en"}
    assert all(c.embedded for c in chunks)
    assert chunks[1].meta["verdict"] == "CORRECTED"
    assert chunks[1].meta["original_claim"] == "Balboa reached the Pacific in 1515."
    excluded = {"INACCURATE", "DISPUTED", "UNVERIFIABLE", "NEEDS_DEEP_RESEARCH", "PENDING"}
    assert not excluded & {c.meta["verdict"] for c in chunks}
    for text, _verdict, correction in ALL_VERDICTS[2:]:
        assert text not in env.texts and (correction or text) not in env.texts


@pytest.mark.parametrize(
    "verdict,correction,expected",
    [
        ("VERIFIED", None, "The claim."),
        ("CORRECTED", "The fix.", "The fix."),
        ("CORRECTED", None, None),  # no correction text: nothing to state
        ("INACCURATE", "The fix.", None),
        ("DISPUTED", "Two views.", None),
        ("UNVERIFIABLE", None, None),
        ("NEEDS_DEEP_RESEARCH", None, None),
        ("PENDING", None, None),
    ],
)
def test_article_claim_text(verdict, correction, expected):
    claim = FactCheckClaim(claim_text="The claim.", verdict=verdict, correction=correction)
    assert claims.article_claim_text(claim) == expected


def test_other_subjects_claims_are_not_article_claims(env, test_db, article_factory):
    article = article_factory()
    _claim(test_db, article.id, "A research fact.", "VERIFIED", subject_type="RESEARCH")
    test_db.commit()

    assert claims.index_article_claims(article.id).status == "unchanged"
    assert _chunks(test_db, "ARTICLE_CLAIM") == []


# --- Incremental --------------------------------------------------------------


def test_rerun_of_an_article_fact_check_leaves_no_orphans(env, test_db, article_with_claims):
    article, rows = article_with_claims
    claims.index_article_claims(article.id)
    env.calls.clear()
    old_ids = {r.id for r in rows}

    # A re-run replaces the subject's rows wholesale, with new ids.
    test_db.query(FactCheckClaim).filter(FactCheckClaim.subject_id == article.id).delete()
    kept = _claim(test_db, article.id, "Panama Viejo was founded in 1519.", "VERIFIED")
    added = _claim(test_db, article.id, "Drake died off Portobelo in 1596.", "VERIFIED")
    _claim(test_db, article.id, "Balboa reached the Pacific in 1515.", "INACCURATE", "1513")
    test_db.commit()

    result = claims.index_article_claims(article.id)

    assert result.status == "indexed"
    assert env.texts == ["Drake died off Portobelo in 1596."]  # same text keeps its vector
    assert (result.kept, result.embedded, result.deleted) == (1, 1, 1)
    chunks = _chunks(test_db, "ARTICLE_CLAIM", article_id=article.id)
    assert [c.source_id for c in chunks] == [kept.id, added.id]
    assert not old_ids & {c.source_id for c in _chunks(test_db, "ARTICLE_CLAIM")}
    assert all(c.embedded for c in chunks)


def test_rerun_on_unchanged_claims_makes_no_call(env, test_db, article_with_claims):
    article, _rows = article_with_claims
    claims.index_article_claims(article.id)
    env.calls.clear()

    assert claims.index_article_claims(article.id).status == "unchanged"
    assert env.calls == []


def test_resolved_verdict_adds_the_claim(env, test_db, article_with_claims):
    article, rows = article_with_claims
    claims.index_article_claims(article.id)
    env.calls.clear()

    rows[5].verdict = FactCheckVerdict.VERIFIED.value  # deep research resolved it
    rows[0].verdict = FactCheckVerdict.DISPUTED.value
    test_db.commit()
    claims.index_article_claims(article.id)

    contents = [c.content for c in _chunks(test_db, "ARTICLE_CLAIM", article_id=article.id)]
    assert contents == ["Balboa reached the Pacific in 1513.", "The fair ran for 40 days."]
    assert env.texts == ["The fair ran for 40 days."]


def test_deleting_the_article_cascades_its_claim_chunks(env, test_db, article_with_claims):
    article, _rows = article_with_claims
    claims.index_article_claims(article.id)
    test_db.delete(article)
    test_db.commit()

    assert _chunks(test_db, "ARTICLE_CLAIM") == []
    assert claims.index_article_claims(article.id).status == "missing"


def test_embedding_failure_is_captured_and_writes_nothing(
    env, test_db, article_with_claims, monkeypatch
):
    article, _rows = article_with_claims
    captured = []
    monkeypatch.setattr(claims.sentry_sdk, "capture_exception", captured.append)
    env.error = embeddings.EmbeddingsError("Voyage returned HTTP 500")

    assert claims.index_article_claims(article.id).status == "failed"
    assert claims.index_verified_claims().status in ("failed", "unchanged")
    assert len(captured) >= 1
    assert _chunks(test_db, "ARTICLE_CLAIM") == []


def test_empty_voyage_key_skips_before_any_db_access(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "voyage_api_key", "")

    def no_session():
        raise AssertionError("a disabled run must not touch the database")

    monkeypatch.setattr(claims, "SessionLocal", no_session)
    assert claims.index_article_claims(1).status == "disabled"
    assert claims.index_verified_claims().status == "disabled"


# --- The verified-claims KB -----------------------------------------------------


def test_kb_rows_are_indexed_one_per_row_with_their_verdict(env, test_db):
    verified = _kb(test_db, "Panama Viejo was founded in 1519.", "VERIFIED")
    wrong = _kb(test_db, "The canal opened in 1915.", "INACCURATE", "It opened in 1914.")
    disputed = _kb(test_db, "Morgan sacked the city in 1670.", "DISPUTED", "1670 or 1671.")
    test_db.commit()

    result = claims.index_verified_claims()

    assert result.status == "indexed" and result.article_id is None
    chunks = _chunks(test_db, "VERIFIED_CLAIM")
    assert [c.source_id for c in chunks] == [verified.id, wrong.id, disputed.id]
    assert [c.content for c in chunks] == [
        "Panama Viejo was founded in 1519.",
        "The canal opened in 1915.\nCorrection: It opened in 1914.",
        "Morgan sacked the city in 1670.\nPositions: 1670 or 1671.",
    ]
    assert [c.meta["verdict"] for c in chunks] == ["VERIFIED", "INACCURATE", "DISPUTED"]
    assert chunks[0].meta["citation"] == "Castillero Calvo, 2004"
    assert chunks[0].meta["source_method"] == "DEEP_RESEARCH"
    assert all(c.article_id is None and c.language == "en" and c.embedded for c in chunks)


def test_kb_update_reembeds_only_the_changed_row_and_drops_deleted(env, test_db):
    a = _kb(test_db, "Claim A.", "VERIFIED")
    b = _kb(test_db, "Claim B.", "VERIFIED")
    c = _kb(test_db, "Claim C.", "VERIFIED")
    test_db.commit()
    claims.index_verified_claims()
    env.calls.clear()
    assert claims.index_verified_claims().status == "unchanged"
    assert env.calls == []

    b.verdict, b.correction = "INACCURATE", "Claim B is wrong."
    a.citation = "New source"  # meta-only change: no re-embed
    test_db.delete(c)
    test_db.commit()
    result = claims.index_verified_claims()

    assert env.texts == ["Claim B.\nCorrection: Claim B is wrong."]
    assert result.deleted == 2  # c, and b's old text
    chunks = _chunks(test_db, "VERIFIED_CLAIM")
    assert [ch.source_id for ch in chunks] == [a.id, b.id]
    assert chunks[0].meta["citation"] == "New source"


def _versions(db, source_type) -> dict[int, str]:
    """Chunk row id -> its xmin: every UPDATE / INSERT of a row gives it a new one."""
    db.expire_all()
    db.commit()  # a fresh snapshot
    return dict(
        db.execute(
            select(_C.id, literal_column("xmin::text")).where(_C.source_type == source_type)
        ).all()
    )


def test_kb_reindex_after_one_change_updates_only_that_row(env, test_db):
    """#728: a reindex rewrites only the changed row, never an unchanged one."""
    rows = [_kb(test_db, f"Claim {n}.", "VERIFIED") for n in range(6)]
    test_db.commit()
    claims.index_verified_claims()
    before = _versions(test_db, "VERIFIED_CLAIM")
    by_source = {
        source_id: row_id
        for row_id, source_id in test_db.execute(
            select(_C.id, _C.source_id).where(_C.source_type == "VERIFIED_CLAIM")
        ).all()
    }

    rows[2].citation = "New source"  # meta-only: the row is UPDATEd in place
    test_db.commit()
    dry = claims.index_verified_claims(dry_run=True)
    assert (dry.status, dry.updated, dry.embedded, dry.deleted) == ("dry_run", 1, 0, 0)
    result = claims.index_verified_claims()

    assert (result.status, result.updated, result.embedded, result.deleted) == (
        "indexed",
        1,
        0,
        0,
    )
    after = _versions(test_db, "VERIFIED_CLAIM")
    changed = {row_id for row_id in before if after.get(row_id) != before[row_id]}
    assert changed == {by_source[rows[2].id]}

    # A text change replaces that row (one delete + one insert) and touches no other.
    rows[4].claim_text = "Claim four, reworded."
    test_db.commit()
    before = after
    result = claims.index_verified_claims()
    assert (result.updated, result.embedded, result.deleted) == (0, 1, 1)
    after = _versions(test_db, "VERIFIED_CLAIM")
    assert {i for i in before if i in after and after[i] != before[i]} == set()
    assert set(before) - set(after) == {by_source[rows[4].id]}
    assert claims.index_verified_claims().status == "unchanged"


def test_article_claims_rerun_moves_only_the_rows_that_changed(env, test_db, article_with_claims):
    """Re-extracted claims move to new ids; a later unchanged run writes nothing."""
    article, rows = article_with_claims
    claims.index_article_claims(article.id)
    # A fact-check re-run: same claims, new ids (every kept row moves source_id).
    for row in rows:
        test_db.delete(row)
    test_db.commit()
    fresh = [_claim(test_db, article.id, *spec) for spec in ALL_VERDICTS]
    test_db.commit()

    result = claims.index_article_claims(article.id)
    assert (result.updated, result.embedded, result.deleted) == (2, 0, 0)
    assert [c.source_id for c in _chunks(test_db, "ARTICLE_CLAIM")] == [fresh[0].id, fresh[1].id]
    before = _versions(test_db, "ARTICLE_CLAIM")
    assert claims.index_article_claims(article.id).status == "unchanged"
    assert _versions(test_db, "ARTICLE_CLAIM") == before


def test_kb_ignores_verdicts_that_carry_no_reusable_fact(env, test_db):
    _kb(test_db, "Never resolved.", "UNVERIFIABLE")
    test_db.commit()

    assert claims.index_verified_claims().status == "unchanged"
    assert _chunks(test_db, "VERIFIED_CLAIM") == []


# --- Hooks --------------------------------------------------------------------


def _job(db, subject_type, subject_id):
    job = CrawlJob(
        job_type=CrawlJobType.FACT_CHECK.value,
        status=CrawlJobStatus.PENDING.value,
        params={"subject_type": subject_type, "subject_id": subject_id},
    )
    db.add(job)
    db.commit()
    return job


@pytest.fixture
def job_env(test_engine, monkeypatch):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(fact_check, "SessionLocal", factory)
    calls = []
    monkeypatch.setattr(
        fact_check, "reindex_claims", lambda ids=(), verified=False: calls.append((ids, verified))
    )
    return calls


def _fake_tier(stats_claims):
    def run(db, job, progress):
        params = job.params
        return {
            "subject_type": params["subject_type"],
            "subject_id": params["subject_id"],
            "total_claims": stats_claims,
            "counts": {},
        }

    return run


def test_article_job_reindexes_that_articles_claims(job_env, test_db, monkeypatch):
    monkeypatch.setattr(fact_check, "_run_fact_check_for_subject", _fake_tier(3))
    job = _job(test_db, FactCheckSubject.ARTICLE.value, 42)

    fact_check.run_fact_check_job(job.id)

    assert job_env == [([42], False)]


def test_external_job_reindexes_the_kb(job_env, test_db, monkeypatch):
    monkeypatch.setattr(fact_check, "_run_fact_check_for_subject", _fake_tier(3))
    job = _job(test_db, FactCheckSubject.RESEARCH.value, 7)

    fact_check.run_fact_check_job(job.id)

    assert job_env == [((), True)]


def test_failed_job_does_not_reindex(job_env, test_db, monkeypatch):
    def boom(db, job, progress):
        raise ValueError("no research")

    monkeypatch.setattr(fact_check, "_run_fact_check_for_subject", boom)
    job = _job(test_db, FactCheckSubject.ARTICLE.value, 42)

    fact_check.run_fact_check_job(job.id)

    assert job_env == []
    test_db.expire_all()
    assert test_db.get(CrawlJob, job.id).status == CrawlJobStatus.FAILED.value


def test_a_raising_reindex_never_touches_the_job_result(test_engine, test_db, monkeypatch):
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(fact_check, "SessionLocal", factory)
    monkeypatch.setattr(fact_check, "_run_fact_check_for_subject", _fake_tier(1))
    monkeypatch.setattr(fact_check.sentry_sdk, "capture_exception", lambda e: None)

    def explode(*a, **k):
        raise RuntimeError("reindex bug")

    monkeypatch.setattr(fact_check, "reindex_claims", explode)
    job = _job(test_db, FactCheckSubject.ARTICLE.value, 42)

    fact_check.run_fact_check_job(job.id)  # must not raise

    test_db.expire_all()
    assert test_db.get(CrawlJob, job.id).status == CrawlJobStatus.SUCCEEDED.value


def test_deep_research_verdicts_index_article_claims_and_kb(
    env, test_client, admin_token, test_db, article_factory
):
    article = article_factory()
    claim = _claim(test_db, article.id, "The fair ran for 40 days.", "NEEDS_DEEP_RESEARCH")
    test_db.commit()

    response = test_client.post(
        "/api/v1/admin/dashboard/fact-check/deep-research/verdicts",
        json={
            "content": f"VERDICT: {claim.id} | VERIFIED | - | Castillero Calvo, 2004",
            "claim_ids": [claim.id],
        },
    )

    assert response.status_code == 200
    article_chunks = _chunks(test_db, "ARTICLE_CLAIM", article_id=article.id)
    assert [(c.source_id, c.content) for c in article_chunks] == [
        (claim.id, "The fair ran for 40 days.")
    ]
    kb_chunks = _chunks(test_db, "VERIFIED_CLAIM")
    assert [c.content for c in kb_chunks] == ["The fair ran for 40 days."]


# --- The sweep ----------------------------------------------------------------


def test_sweep_covers_claims_and_the_kb(env, test_db, article_with_claims):
    article, _rows = article_with_claims
    _kb(test_db, "Panama Viejo was founded in 1519.", "VERIFIED")
    test_db.commit()

    results = reindex_knowledge.sweep(reindex_knowledge.article_ids(), dry_run=True, verified=True)
    totals = reindex_knowledge.summarize(results, dry_run=True)

    assert env.calls == []
    assert totals["articles"] == 1
    assert totals["by_source"]["ARTICLE_CLAIM"]["embedded"] == 2
    assert totals["by_source"]["VERIFIED_CLAIM"]["embedded"] == 1
    assert totals["est_cost_usd"] == embeddings.estimate_cost(
        embeddings.document_model(), totals["embed_tokens"]
    )

    assert reindex_knowledge.main(["--all"]) == 0
    assert len(_chunks(test_db, "ARTICLE_CLAIM")) == 2
    assert len(_chunks(test_db, "VERIFIED_CLAIM")) == 1
    env.calls.clear()
    assert reindex_knowledge.main(["--all"]) == 0
    assert env.calls == []


def test_sweep_logs_dead_tuples_and_vacuums_above_the_threshold(env, test_db, monkeypatch):
    """#728: a real sweep that writes many rows ends with VACUUM (ANALYZE)."""
    for n in range(3):
        _kb(test_db, f"Claim {n}.", "VERIFIED")
    test_db.commit()
    vacuums = []
    real = reindex_knowledge.vacuum_knowledge_chunks

    def spy():
        real()  # VACUUM really runs (on an AUTOCOMMIT connection)
        vacuums.append(True)

    monkeypatch.setattr(reindex_knowledge, "vacuum_knowledge_chunks", spy)

    assert reindex_knowledge.main(["--all"]) == 0  # inserts only: no dead tuples
    assert vacuums == []
    assert reindex_knowledge.dead_tuples() is not None

    monkeypatch.setattr(reindex_knowledge, "VACUUM_DEAD_TUPLES", 1)
    test_db.execute(select(VerifiedClaim)).scalars().first().citation = "Changed"
    test_db.commit()
    assert reindex_knowledge.main(["--all"]) == 0
    assert vacuums == [True]
    assert reindex_knowledge.main(["--dry-run", "--all"]) == 0  # never vacuums
    assert vacuums == [True]
