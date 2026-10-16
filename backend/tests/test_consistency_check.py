"""Cross-article consistency check, shadow mode (#631).

The flag-off identity of the fact-check job is covered by the unmodified
``TestRunFactCheckJob`` (test_fact_check.py) and the #625 job-tail tests
(test_knowledge_claims.py); here: the flag-off guard itself, the check with a
fake judge over deterministic vectors, the INACCURATE / DISPUTED KB handling,
the cost caps, re-runs, isolation, and the admin endpoints + grid count.
"""

import hashlib
import math
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.api import dashboard
from app.core.config import get_settings
from app.models.consistency_finding import ConsistencyFinding
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.fact_check import FactCheckClaim
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.verified_claim import VerifiedClaim
from app.services import consistency_check as cc
from app.services import embeddings
from app.services import fact_check
from sqlalchemy import insert, text
from sqlalchemy.orm import sessionmaker

DIMS = 1024


def _unit(sim: float, axis: int) -> list[float]:
    """A unit vector with cosine similarity ``sim`` to the claim on ``axis``."""
    v = [0.0] * DIMS
    v[axis] = sim
    v[axis + 500] = math.sqrt(max(0.0, 1 - sim * sim))
    return v


def _axis_vec(axis: int) -> list[float]:
    v = [0.0] * DIMS
    v[axis] = 1.0
    return v


class FakeJudge:
    """Answers each pair by ``rules[(claim, reference)]`` (default UNRELATED)."""

    def __init__(self, rules=None, raw=None, fail=False):
        self.rules = rules or {}
        self.raw = raw
        self.fail = fail
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    def __call__(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        if self.fail:
            raise RuntimeError("sonnet down")
        if self.raw is not None:
            body = self.raw
        else:
            lines = []
            for number, claim, ref in re.findall(
                r"^(\d+)\. CLAIM: (.*)\n   REFERENCE: (.*)$", prompt, re.M
            ):
                label = self.rules.get((claim, ref), "UNRELATED")
                lines.append(f"PAIR: {number} | {label} | judged {label.lower()}")
            body = "\n".join(lines)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=body)],
            usage=SimpleNamespace(input_tokens=500, output_tokens=50),
        )

    def pair_count(self, i=0):
        return len(re.findall(r"^\d+\. CLAIM:", self.prompts[i], re.M))


@pytest.fixture
def env(test_engine, monkeypatch):
    """Flag on, sessions on the test DB, Voyage faked by a text -> vector map."""
    settings = get_settings()
    monkeypatch.setattr(settings, "voyage_api_key", "fake-key")
    monkeypatch.setattr(settings, "cross_article_consistency_enabled", True)
    monkeypatch.setattr(settings, "consistency_max_claims", 40)
    monkeypatch.setattr(settings, "consistency_top_k", 3)
    monkeypatch.setattr(settings, "consistency_min_similarity", 0.5)
    monkeypatch.setattr(settings, "consistency_max_pairs", 120)
    monkeypatch.setattr(settings, "consistency_batch_pairs", 20)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(cc, "SessionLocal", factory)
    state = SimpleNamespace(vectors={}, embed_calls=[], judge=FakeJudge())

    def fake_embed_queries(texts, *, feature="unlabeled", model=None):
        state.embed_calls.append((list(texts), feature))
        return [state.vectors[t] for t in texts]

    monkeypatch.setattr(embeddings, "embed_queries", fake_embed_queries)
    monkeypatch.setattr(
        embeddings, "embed_query", lambda *a, **k: pytest.fail("must use the batched vectors")
    )
    monkeypatch.setattr(cc, "create_message", lambda **kw: state.judge(**kw))
    return state


def _claim(db, article_id, claim_text, verdict="VERIFIED"):
    row = FactCheckClaim(
        subject_type="ARTICLE",
        subject_id=article_id,
        claim_text=claim_text,
        claim_type="DATE",
        verdict=verdict,
        checked_via="GROUNDING",
    )
    db.add(row)
    db.commit()
    return row


def _chunk(db, *, source_type, source_id, article_id, content, vector, meta):
    db.execute(
        insert(KnowledgeChunk.__table__).values(
            source_type=source_type,
            source_id=source_id,
            article_id=article_id,
            language="en",
            chunk_index=0,
            heading_path=None,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            token_count=len(content) // 4 + 1,
            embedding_model="voyage-4",
            embedding=vector,
            meta={"language": "en", **meta},
        )
    )
    db.commit()


def _other_fact(db, other_article_id, content, vector, *, verdict="VERIFIED", original=None):
    claim = _claim(db, other_article_id, original or content, verdict=verdict)
    meta = {"verdict": verdict}
    if original:
        meta["original_claim"] = original
    _chunk(
        db,
        source_type="ARTICLE_CLAIM",
        source_id=claim.id,
        article_id=other_article_id,
        content=content,
        vector=vector,
        meta=meta,
    )
    return claim


def _kb(db, claim_text, vector, *, verdict="VERIFIED", correction=None):
    row = VerifiedClaim(
        claim_key=" ".join(claim_text.lower().split()),
        claim_text=claim_text,
        claim_type="DATE",
        verdict=verdict,
        correction=correction,
        citation="https://kb.org",
        source_method="WEB_SEARCH",
    )
    db.add(row)
    db.commit()
    content = f"{claim_text}\nCorrection: {correction}" if correction else claim_text
    _chunk(
        db,
        source_type="VERIFIED_CLAIM",
        source_id=row.id,
        article_id=None,
        content=content,
        vector=vector,
        meta={"verdict": verdict},
    )
    return row


def _findings(db, article_id=None):
    db.expire_all()
    q = db.query(ConsistencyFinding)
    if article_id is not None:
        q = q.filter(ConsistencyFinding.article_id == article_id)
    return q.order_by(ConsistencyFinding.id).all()


C1 = "Panama Viejo was founded in 1519 by Pedrarias Davila"
C2 = "The Panama Railroad was completed in 1855"
REF1 = "Panama Viejo was founded in 1521 by Pedrarias Davila"
REF2 = "Workers finished the Panama Railroad in 1855"


@pytest.fixture
def two_articles(test_db, article_factory, env):
    article = article_factory(title="Checked")
    other = article_factory(title="Other")
    c1 = _claim(test_db, article.id, C1)
    c2 = _claim(test_db, article.id, C2)
    env.vectors = {C1: _axis_vec(0), C2: _axis_vec(1)}
    _other_fact(test_db, other.id, REF1, _unit(0.9, 0))
    _other_fact(test_db, other.id, REF2, _unit(0.92, 1))
    return SimpleNamespace(article=article, other=other, c1=c1, c2=c2)


# --- The check ---------------------------------------------------------------


def test_contradiction_is_recorded_and_paraphrase_is_not(test_db, env, two_articles):
    env.judge.rules = {(C1, REF1): "CONTRADICTS", (C2, REF2): "CONSISTENT"}

    summary = cc.run_consistency_check(two_articles.article.id)

    assert summary["pairs"] == 2
    assert summary["counts"] == {"CONSISTENT": 1, "CONTRADICTS": 1, "UNRELATED": 0}
    rows = _findings(test_db)
    assert len(rows) == 1
    (row,) = rows
    assert row.article_id == two_articles.article.id
    assert row.claim_id == two_articles.c1.id
    assert row.claim_text == C1
    assert row.other_source_type == "ARTICLE_CLAIM"
    assert row.other_article_id == two_articles.other.id
    assert row.other_text == REF1
    assert row.verdict == "CONTRADICTS"
    assert row.explanation
    assert row.resolved_at is None
    # One batched embedding, labelled; judge on Sonnet, low effort, its own feature.
    assert env.embed_calls == [([C1, C2], "consistency_check")]
    kwargs = env.judge.kwargs[0]
    assert kwargs["feature"] == "consistency_check"
    assert kwargs["output_config"] == {"effort": "low"}


def test_own_article_chunks_are_excluded(test_db, env, article_factory):
    article = article_factory(title="Checked")
    _claim(test_db, article.id, C1)
    env.vectors = {C1: _axis_vec(0)}
    own = _claim(test_db, article.id, REF1)
    env.vectors[REF1] = _axis_vec(5)
    _chunk(
        test_db,
        source_type="ARTICLE_CLAIM",
        source_id=own.id,
        article_id=article.id,
        content=REF1,
        vector=_unit(0.95, 0),
        meta={"verdict": "VERIFIED"},
    )
    env.judge.rules = {(C1, REF1): "CONTRADICTS"}

    summary = cc.run_consistency_check(article.id)

    assert summary["claims"] == 2
    assert summary["pairs"] == 0
    assert env.judge.prompts == []
    assert _findings(test_db) == []


def test_below_the_floor_is_not_judged(test_db, env, two_articles, monkeypatch):
    monkeypatch.setattr(get_settings(), "consistency_min_similarity", 0.95)
    summary = cc.run_consistency_check(two_articles.article.id)
    assert summary["pairs"] == 0
    assert env.judge.prompts == []


@pytest.mark.parametrize(
    "raw",
    [
        "Here are my verdicts: pair 1 contradicts.",
        "PAIR: 1 | CONTRADICT | typo label",
        "PAIR: 1 | CONTRADICTS | -",
        "PAIR: 1 | CONTRADICTS |",
        "PAIR: 9 | CONTRADICTS | out of range",
        "",
    ],
)
def test_malformed_judge_output_records_nothing(test_db, env, two_articles, raw):
    env.judge.raw = raw
    summary = cc.run_consistency_check(two_articles.article.id)
    assert summary["counts"]["CONTRADICTS"] == 0
    assert _findings(test_db) == []


def test_parse_keeps_first_line_per_number():
    parsed = cc.parse_judge_lines(
        "PAIR: 1 | CONSISTENT | same\nPAIR: 1 | CONTRADICTS | later\nPAIR: 2 | UNRELATED | x", 2
    )
    assert parsed == {0: ("CONSISTENT", "same"), 1: ("UNRELATED", "x")}


def test_judge_failure_is_isolated(test_db, env, two_articles):
    env.judge.fail = True
    summary = cc.run_consistency_check(two_articles.article.id)
    assert summary["failed_batches"] == 1
    assert _findings(test_db) == []


def test_unexpected_error_never_raises(env, monkeypatch, article_factory):
    article = article_factory()

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(cc, "_load_claims", boom)
    assert cc.run_consistency_check(article.id) is None


def test_embeddings_disabled_skips_quietly(test_db, env, two_articles, monkeypatch):
    monkeypatch.setattr(get_settings(), "voyage_api_key", "")
    assert cc.run_consistency_check(two_articles.article.id) is None
    assert env.embed_calls == []


# --- INACCURATE / DISPUTED KB rows -------------------------------------------


def test_inaccurate_kb_row_is_judged_on_its_correction(test_db, env, article_factory):
    article = article_factory()
    claim = "Henry Morgan sacked Panama Viejo in 1672"
    falsehood = "Henry Morgan sacked Panama Viejo in 1672"
    correction = "Henry Morgan sacked Panama Viejo in 1671"
    disputed = "Panama Viejo had 10,000 inhabitants in 1671"
    _claim(test_db, article.id, claim)
    env.vectors = {claim: _axis_vec(0)}
    kb = _kb(test_db, falsehood, _unit(0.99, 0), verdict="INACCURATE", correction=correction)
    _kb(test_db, disputed, _unit(0.9, 0), verdict="DISPUTED", correction="8,000 vs 10,000")
    _kb(test_db, "Panama Viejo was sacked", _unit(0.8, 0), verdict="INACCURATE")  # no correction
    env.judge.rules = {(claim, correction): "CONTRADICTS"}

    summary = cc.run_consistency_check(article.id)

    prompt = env.judge.prompts[0]
    assert summary["pairs"] == 1  # DISPUTED and correction-less INACCURATE dropped
    assert f"REFERENCE: {correction}" in prompt
    assert f"KNOWN FALSEHOOD: {falsehood}" in prompt
    assert disputed not in prompt
    (row,) = _findings(test_db)
    assert row.other_source_type == "VERIFIED_CLAIM"
    assert row.other_source_id == kb.id
    assert row.other_article_id is None
    assert row.other_text == correction


def test_corrected_article_claim_shows_its_original_as_falsehood(test_db, env, article_factory):
    article = article_factory()
    other = article_factory(title="Other")
    _claim(test_db, article.id, C1)
    env.vectors = {C1: _axis_vec(0)}
    _other_fact(
        test_db, other.id, REF1, _unit(0.9, 0), verdict="CORRECTED", original="Founded in 1500"
    )
    cc.run_consistency_check(article.id)
    assert "KNOWN FALSEHOOD: Founded in 1500" in env.judge.prompts[0]


# --- Cost caps ---------------------------------------------------------------


def test_cost_caps_are_respected(test_db, env, article_factory, monkeypatch):
    settings = get_settings()
    article = article_factory()
    other = article_factory(title="Other")
    texts = [f"Claim number {i} about the canal" for i in range(4)]
    for i, t in enumerate(texts):
        _claim(test_db, article.id, t)
        env.vectors[t] = _axis_vec(i)
        for j, sim in enumerate((0.9, 0.8, 0.7)):
            _other_fact(test_db, other.id, f"Reference {i}.{j}", _unit(sim, i))

    monkeypatch.setattr(settings, "consistency_max_claims", 3)
    monkeypatch.setattr(settings, "consistency_top_k", 2)
    monkeypatch.setattr(settings, "consistency_max_pairs", 5)
    monkeypatch.setattr(settings, "consistency_batch_pairs", 2)

    summary = cc.run_consistency_check(article.id)

    assert env.embed_calls[0][0] == texts[:3]  # max_claims
    assert summary["pairs"] == 5  # 3 claims x top_k 2 = 6, capped at max_pairs
    assert summary["batches"] == 3  # batches of 2 -> 2 + 2 + 1
    assert [env.judge.pair_count(i) for i in range(3)] == [2, 2, 1]
    assert all("Reference 3." not in p for p in env.judge.prompts)
    assert all(f"Reference {i}.2" not in p for i in range(4) for p in env.judge.prompts)
    assert summary["input_tokens"] == 1500 and summary["output_tokens"] == 150
    assert summary["cost_usd"] > 0


def test_summary_line_is_logged(test_db, env, two_articles, caplog):
    with caplog.at_level("INFO", logger="app.services.consistency_check"):
        cc.run_consistency_check(two_articles.article.id)
    line = next(r.message for r in caplog.records if r.message.startswith("consistency check:"))
    for field in ("pairs=2", "input_tokens=", "output_tokens=", "est_cost_usd=", "elapsed_ms="):
        assert field in line


# --- Re-runs -----------------------------------------------------------------


def test_rerun_replaces_open_findings_and_keeps_resolved(test_db, env, two_articles):
    article_id = two_articles.article.id
    env.judge.rules = {(C1, REF1): "CONTRADICTS", (C2, REF2): "CONTRADICTS"}
    cc.run_consistency_check(article_id)
    first = _findings(test_db, article_id)
    assert len(first) == 2
    first_ids = [r.id for r in first]

    # Dismiss one; re-run with the same verdicts.
    dismissed = first[0]
    dismissed.resolved_at = datetime.now(UTC)
    dismissed.resolution = "DISMISSED"
    test_db.commit()
    cc.run_consistency_check(article_id)

    rows = _findings(test_db, article_id)
    assert len(rows) == 2  # the dismissed one is not re-recorded, the open one replaced
    assert {r.claim_text for r in rows} == {C1, C2}
    assert sum(1 for r in rows if r.resolved_at is None) == 1
    assert first_ids[1] not in {r.id for r in rows}

    # The open contradiction goes away when the judge no longer finds it.
    env.judge.rules = {}
    cc.run_consistency_check(article_id)
    rows = _findings(test_db, article_id)
    assert [r.resolution for r in rows] == ["DISMISSED"]


def test_rerun_with_a_failed_batch_only_adds(test_db, env, two_articles):
    article_id = two_articles.article.id
    env.judge.rules = {(C1, REF1): "CONTRADICTS"}
    cc.run_consistency_check(article_id)
    assert len(_findings(test_db, article_id)) == 1
    env.judge.fail = True
    cc.run_consistency_check(article_id)
    assert len(_findings(test_db, article_id)) == 1


def test_claim_rerun_sets_claim_id_null_and_keeps_the_finding(test_db, env, two_articles):
    env.judge.rules = {(C1, REF1): "CONTRADICTS"}
    cc.run_consistency_check(two_articles.article.id)
    test_db.query(FactCheckClaim).filter(FactCheckClaim.id == two_articles.c1.id).delete()
    test_db.commit()
    (row,) = _findings(test_db)
    assert row.claim_id is None
    assert row.claim_text == C1


# --- Job tail: flag off / on -------------------------------------------------


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
    monkeypatch.setattr(fact_check, "reindex_claims", lambda ids=(), verified=False: None)

    def tier(db, job, progress):
        return {
            "subject_type": job.params["subject_type"],
            "subject_id": job.params["subject_id"],
            "total_claims": 0,
            "counts": {},
        }

    monkeypatch.setattr(fact_check, "_run_fact_check_for_subject", tier)
    calls = []
    return SimpleNamespace(factory=factory, calls=calls)


def test_flag_off_makes_no_consistency_call(test_db, job_env, monkeypatch, article_factory):
    monkeypatch.setattr(get_settings(), "cross_article_consistency_enabled", False)
    monkeypatch.setattr(fact_check, "run_consistency_check", lambda *a: pytest.fail("flag is off"))
    monkeypatch.setattr(embeddings, "embed_queries", lambda *a, **k: pytest.fail("voyage"))
    monkeypatch.setattr(cc, "create_message", lambda **k: pytest.fail("sonnet"))
    article = article_factory()
    job = _job(test_db, "ARTICLE", article.id)

    fact_check.run_fact_check_job(job.id)

    test_db.expire_all()
    assert test_db.get(CrawlJob, job.id).status == "SUCCEEDED"


def test_flag_on_runs_after_succeeded_for_articles_only(
    test_db, job_env, monkeypatch, article_factory
):
    monkeypatch.setattr(get_settings(), "cross_article_consistency_enabled", True)
    seen = []

    def check(article_id):
        with job_env.factory() as db:
            seen.append((article_id, [j.status for j in db.query(CrawlJob).all()]))

    monkeypatch.setattr(fact_check, "run_consistency_check", check)
    article = article_factory()
    job = _job(test_db, "ARTICLE", article.id)
    fact_check.run_fact_check_job(job.id)
    assert seen == [(article.id, ["SUCCEEDED"])]

    other = _job(test_db, "RESEARCH", 1)
    fact_check.run_fact_check_job(other.id)
    assert len(seen) == 1


def test_judge_exception_keeps_the_job_succeeded(test_db, job_env, env, two_articles):
    env.judge.fail = True
    job = _job(test_db, "ARTICLE", two_articles.article.id)

    fact_check.run_fact_check_job(job.id)

    test_db.expire_all()
    assert test_db.get(CrawlJob, job.id).status == "SUCCEEDED"
    assert env.judge.prompts  # the judge really was called
    assert _findings(test_db) == []


def test_a_raising_check_keeps_the_job_succeeded(test_db, job_env, monkeypatch, article_factory):
    monkeypatch.setattr(get_settings(), "cross_article_consistency_enabled", True)

    def boom(article_id):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(fact_check, "run_consistency_check", boom)
    article = article_factory()
    job = _job(test_db, "ARTICLE", article.id)
    fact_check.run_fact_check_job(job.id)
    test_db.expire_all()
    assert test_db.get(CrawlJob, job.id).status == "SUCCEEDED"


# --- Admin endpoints + grid --------------------------------------------------


def _finding(db, article_id, other_article_id=None, **kw):
    row = ConsistencyFinding(
        article_id=article_id,
        claim_text=kw.get("claim_text", C1),
        other_source_type=kw.get("other_source_type", "ARTICLE_CLAIM"),
        other_source_id=1,
        other_article_id=other_article_id,
        other_text=kw.get("other_text", REF1),
        verdict="CONTRADICTS",
        explanation="1519 vs 1521",
    )
    db.add(row)
    db.commit()
    return row


BASE = "/api/v1/admin/dashboard/articles"


def test_list_and_resolve_endpoints(test_db, test_client, admin_token, article_factory):
    article = article_factory(title="Checked")
    other = article_factory(title="Other article")
    f1 = _finding(test_db, article.id, other.id)
    f2 = _finding(test_db, article.id, claim_text=C2, other_source_type="VERIFIED_CLAIM")

    res = test_client.get(f"{BASE}/{article.id}/consistency-findings")
    assert res.status_code == 200
    body = res.json()
    assert body["open_count"] == 2
    first = body["findings"][0]
    assert first["other_article_title"] == "Other article"
    assert first["explanation"] == "1519 vs 1521"

    res = test_client.post(
        f"{BASE}/{article.id}/consistency-findings/{f1.id}/resolve",
        json={"resolution": "DISMISSED"},
    )
    assert res.status_code == 200
    assert res.json()["resolution"] == "DISMISSED"
    assert res.json()["resolved_at"]
    # Idempotent: the first resolution sticks.
    res = test_client.post(
        f"{BASE}/{article.id}/consistency-findings/{f1.id}/resolve",
        json={"resolution": "RESOLVED"},
    )
    assert res.json()["resolution"] == "DISMISSED"
    res = test_client.post(
        f"{BASE}/{article.id}/consistency-findings/{f2.id}/resolve",
        json={"resolution": "RESOLVED"},
    )
    assert res.json()["resolution"] == "RESOLVED"

    assert test_client.get(f"{BASE}/{article.id}/consistency-findings").json()["findings"] == []
    full = test_client.get(f"{BASE}/{article.id}/consistency-findings?include_resolved=true")
    assert len(full.json()["findings"]) == 2


def test_resolve_validation_and_404s(test_db, test_client, admin_token, article_factory):
    article = article_factory()
    other = article_factory(title="Other")
    finding = _finding(test_db, article.id)
    url = f"{BASE}/{other.id}/consistency-findings/{finding.id}/resolve"
    assert test_client.post(url, json={"resolution": "RESOLVED"}).status_code == 404
    url = f"{BASE}/{article.id}/consistency-findings/{finding.id}/resolve"
    assert test_client.post(url, json={"resolution": "MAYBE"}).status_code == 422
    assert test_client.get(f"{BASE}/999999/consistency-findings").status_code == 404


def test_endpoints_require_admin(test_client, article_factory):
    article = article_factory()
    assert test_client.get(f"{BASE}/{article.id}/consistency-findings").status_code == 401


def test_grid_counts_open_findings(test_db, test_client, admin_token, article_factory):
    article = article_factory(title="Checked")
    clean = article_factory(title="Clean")
    _finding(test_db, article.id)
    _finding(test_db, article.id, claim_text=C2)
    closed = _finding(test_db, article.id, claim_text="closed")
    closed.resolved_at = datetime.now(UTC)
    closed.resolution = "RESOLVED"
    test_db.commit()

    rows = {r["id"]: r for r in test_client.get(BASE).json()}
    assert rows[article.id]["consistency_open_count"] == 2
    assert rows[clean.id]["consistency_open_count"] == 0


def test_grid_works_before_migration_0046(test_db, article_factory):
    """Drop the table (transactional DDL, rolled back): the listing still works."""
    article = article_factory(title="Checked")
    test_db.execute(text("DROP TABLE consistency_findings"))
    try:
        items = dashboard.list_articles(db=test_db)
        assert [i.consistency_open_count for i in items if i.id == article.id] == [0]
        assert test_db.query(FactCheckClaim).count() == 0  # transaction still usable
    finally:
        test_db.rollback()
    assert test_db.execute(text("SELECT count(*) FROM consistency_findings")).scalar() == 0
