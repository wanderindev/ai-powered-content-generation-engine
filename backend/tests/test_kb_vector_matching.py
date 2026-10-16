"""KB matching via vector candidates (#628) and its replay script.

Flag off is covered by the unchanged ``TestKbMatching`` in test_fact_check.py;
here: flag on (candidates from ``retrieval.search``, floor and top_k, exact
key first, the same Haiku guard), the fallbacks, ``search(query_vector=)`` and
``scripts.eval_kb_matching`` (no writes, correct metrics).
"""

import hashlib
import logging
import math
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.core.config import get_settings
from app.models.fact_check import FactCheckClaim
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.verified_claim import VerifiedClaim
from app.services import embeddings
from app.services import fact_check as fc
from app.services.fact_check import ExtractedClaim, _normalize, match_claims_against_kb
from app.services.knowledge import retrieval
from scripts import eval_kb_matching as replay
from sqlalchemy import func, insert, select

DIMS = 1024


def _vec(*weights: tuple[int, float]) -> list[float]:
    v = [0.0] * DIMS
    for i, w in weights:
        v[i] = w
    return v


def _unit(sim: float, axis: int = 1) -> list[float]:
    """A unit vector whose cosine similarity with axis 0 is ``sim``."""
    return _vec((0, sim), (axis, math.sqrt(max(0.0, 1 - sim * sim))))


QUERY = _vec((0, 1.0))


class FakeLLM:
    """Records every prompt; answers per claim from ``answers`` (claim text -> KB id)."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.prompts = []

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        claims_part = prompt.split("NEW CLAIMS:\n", 1)[1].split("\n\nKB CLAIMS:", 1)[0]
        lines = []
        for line in claims_part.splitlines():
            number, text = line.split(". ", 1)
            kb_id = self.answers.get(text)
            lines.append(f"MATCH: {number} | {kb_id if kb_id is not None else 'NONE'}")
        return MagicMock(
            content=[MagicMock(text="\n".join(lines))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=10),
        )

    def kb_ids(self, prompt_index=0):
        kb_part = self.prompts[prompt_index].split("KB CLAIMS:\n", 1)[1]
        return [int(m) for m in re.findall(r"^\[(\d+)\]", kb_part, re.M)]


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM()
    client = MagicMock()
    client.messages.create.side_effect = fake.create
    monkeypatch.setattr("app.services.llm.get_client", lambda: client)
    return fake


@pytest.fixture
def vector_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "voyage_api_key", "fake-key")
    monkeypatch.setattr(settings, "kb_vector_matching_enabled", True)
    monkeypatch.setattr(settings, "kb_vector_top_k", 5)
    monkeypatch.setattr(settings, "kb_vector_min_similarity", 0.7)
    calls = []

    def fake_embed_queries(texts, *, feature="unlabeled", model=None):
        calls.append((list(texts), feature))
        return [QUERY for _ in texts]

    monkeypatch.setattr(embeddings, "embed_queries", fake_embed_queries)
    monkeypatch.setattr(
        embeddings,
        "embed_query",
        lambda *a, **k: pytest.fail("search must use the precomputed vector"),
    )
    return calls


def _kb(db, text, *, verdict="VERIFIED", sim=None, chunk_verdict=None, axis=1):
    row = VerifiedClaim(
        claim_key=_normalize(text),
        claim_text=text,
        claim_type="DATE",
        verdict=verdict,
        citation="https://kb.org",
        source_method="WEB_SEARCH",
    )
    db.add(row)
    db.commit()
    if sim is not None:
        db.execute(
            insert(KnowledgeChunk.__table__).values(
                source_type="VERIFIED_CLAIM",
                source_id=row.id,
                article_id=None,
                language="en",
                chunk_index=0,
                heading_path=None,
                content=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                token_count=len(text) // 4 + 1,
                embedding_model="voyage-4",
                embedding=_unit(sim, axis),
                meta={"language": "en", "verdict": chunk_verdict or verdict},
            )
        )
        db.commit()
    return row


CLAIM = "Panama Viejo was founded in 1519"


class TestVectorPath:
    def test_candidates_come_from_vector_search_above_the_floor(self, test_db, vector_on, llm):
        near = _kb(test_db, "Pedrarias founded the city of Panama in 1519", sim=0.95)
        _kb(test_db, "The Darien colony was abandoned in 1524", sim=0.5, axis=2)
        # Shares words with the claim but has no chunk: word overlap would pick
        # it, the vector path cannot.
        _kb(test_db, "Panama Viejo was founded by royal charter in 1521")
        llm.answers = {CLAIM: near.id}

        matches = match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db)

        assert matches == {0: near}
        assert llm.kb_ids() == [near.id]
        assert vector_on == [([CLAIM], fc.KB_MATCH_FEATURE)]

    def test_top_k_keeps_the_nearest(self, test_db, vector_on, llm, monkeypatch):
        monkeypatch.setattr(get_settings(), "kb_vector_top_k", 2)
        rows = [
            _kb(test_db, f"Claim number {i} about the isthmus", sim=s, axis=i + 1)
            for i, s in enumerate([0.75, 0.99, 0.9])
        ]
        match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db)
        assert llm.kb_ids() == [rows[1].id, rows[2].id]

    def test_inaccurate_and_disputed_rows_are_candidates(self, test_db, vector_on, llm):
        wrong = _kb(test_db, "Panama Viejo was founded in 1520", verdict="INACCURATE", sim=0.97)
        disputed = _kb(test_db, "Panama Viejo was founded in 1518", verdict="DISPUTED", sim=0.9)
        llm.answers = {CLAIM: wrong.id}
        matches = match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db)
        assert matches == {0: wrong}
        assert llm.kb_ids() == [wrong.id, disputed.id]

    def test_stale_chunk_of_an_unreusable_row_is_dropped(self, test_db, vector_on, llm):
        # Chunk still says VERIFIED, row has since become NEEDS_DEEP_RESEARCH.
        _kb(
            test_db,
            "Panama Viejo stood on the coast",
            verdict="NEEDS_DEEP_RESEARCH",
            sim=0.9,
            chunk_verdict="VERIFIED",
        )
        assert match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db) == {}
        assert llm.prompts == []

    def test_exact_key_first_without_embedding_or_llm(self, test_db, vector_on, llm):
        kb = _kb(test_db, "Panama Viejo was FOUNDED in 1519!", sim=1.0)
        matches = match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db)
        assert matches == {0: kb}
        assert vector_on == []
        assert llm.prompts == []

    def test_hallucinated_id_rejected(self, test_db, vector_on, llm):
        near = _kb(test_db, "Pedrarias founded the city of Panama in 1519", sim=0.95)
        llm.answers = {CLAIM: near.id + 999}
        assert match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db) == {}

    def test_before_llm_runs_before_the_embedding_and_the_haiku_pass(
        self, test_db, vector_on, llm, monkeypatch
    ):
        near = _kb(test_db, "Pedrarias founded the city of Panama in 1519", sim=0.95)
        llm.answers = {CLAIM: near.id}
        events = []
        real = embeddings.embed_queries

        def embed(texts, **kwargs):
            events.append("embed")
            return real(texts, **kwargs)

        monkeypatch.setattr(embeddings, "embed_queries", embed)
        original_create = llm.create

        def create(**kwargs):
            events.append("haiku")
            return original_create(**kwargs)

        llm.create = create
        monkeypatch.setattr("app.services.llm.get_client", lambda: _client(llm))

        def before_llm():
            test_db.commit()
            events.append("before_llm")

        matches = match_claims_against_kb(
            [ExtractedClaim(CLAIM, "DATE")], test_db, before_llm=before_llm
        )
        assert matches == {0: near}
        assert events == ["before_llm", "embed", "before_llm", "haiku"]


def _client(fake):
    client = MagicMock()
    client.messages.create.side_effect = lambda **kw: fake.create(**kw)
    return client


class TestFallback:
    def _word_overlap_fixture(self, db, llm):
        kb = _kb(db, "The city of Panama Viejo was founded in the year 1519")
        llm.answers = {CLAIM: kb.id}
        return kb

    def test_flag_off_never_embeds(self, test_db, llm, monkeypatch):
        monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
        monkeypatch.setattr(embeddings, "embed_queries", lambda *a, **k: pytest.fail("flag is off"))
        kb = self._word_overlap_fixture(test_db, llm)
        assert match_claims_against_kb([ExtractedClaim(CLAIM, "DATE")], test_db) == {0: kb}

    def test_embeddings_disabled_uses_word_overlap(self, test_db, llm, monkeypatch, caplog):
        monkeypatch.setattr(get_settings(), "kb_vector_matching_enabled", True)
        monkeypatch.setattr(get_settings(), "voyage_api_key", "")
        kb = self._word_overlap_fixture(test_db, llm)
        with caplog.at_level(logging.INFO, logger="app.services.fact_check"):
            matches = match_claims_against_kb(
                [ExtractedClaim(CLAIM, "DATE"), ExtractedClaim("Morgan sacked it", "EVENT")],
                test_db,
            )
        assert matches == {0: kb}
        assert caplog.text.count("embeddings disabled") == 1

    def test_failed_embedding_uses_word_overlap_logged_once(
        self, test_db, vector_on, llm, monkeypatch, caplog
    ):
        def boom(*a, **k):
            raise embeddings.EmbeddingsError("timeout")

        monkeypatch.setattr(embeddings, "embed_queries", boom)
        kb = self._word_overlap_fixture(test_db, llm)
        claims = [ExtractedClaim(CLAIM, "DATE"), ExtractedClaim("Morgan sacked it", "EVENT")]
        with caplog.at_level(logging.WARNING, logger="app.services.fact_check"):
            matches = match_claims_against_kb(claims, test_db)
        assert matches == {0: kb}
        assert caplog.text.count("query embedding failed") == 1


def test_search_uses_a_precomputed_query_vector(test_db, vector_on):
    kb = _kb(test_db, "Pedrarias founded the city of Panama in 1519", sim=0.8)
    result = retrieval.search(
        test_db,
        "anything",
        mode="vector",
        source_types=["VERIFIED_CLAIM"],
        visibility="internal",
        query_vector=QUERY,
    )
    assert [c.source_id for c in result.chunks] == [kb.id]
    assert result.chunks[0].vector_distance == pytest.approx(0.2, abs=1e-4)
    assert result.degraded == []


# --- Replay script --------------------------------------------------------------


def _claim(db, text, *, subject="SUGGESTION", verdict="VERIFIED"):
    row = FactCheckClaim(
        subject_type=subject,
        subject_id=1,
        claim_text=text,
        claim_type="DATE",
        verdict=verdict,
    )
    db.add(row)
    db.commit()
    return row


def _counts(db):
    return tuple(
        db.execute(select(func.count()).select_from(t)).scalar_one()
        for t in (FactCheckClaim.__table__, VerifiedClaim.__table__, KnowledgeChunk.__table__)
    )


@pytest.fixture
def replay_corpus(test_db, vector_on, llm):
    # A claim with its OWN KB row (must never match itself) plus a different
    # KB row stating the same fact, near in vector space but word-distant.
    own_text = "Panama Viejo was founded in 1519"
    _claim(test_db, own_text)
    _kb(test_db, own_text, sim=1.0)
    other = _kb(test_db, "Pedrarias established the settlement during 1519", sim=0.9, axis=2)
    # A claim with no own row whose word-overlap twin is vector-far.
    words_text = "Henry Morgan sacked Panama Viejo in 1671"
    _claim(test_db, words_text, subject="RESEARCH", verdict="INACCURATE")
    twin = _kb(test_db, "In 1671 Henry Morgan sacked old Panama Viejo", sim=0.55, axis=3)
    # A duplicate-key claim: de-duplicated away.
    _claim(test_db, "panama viejo was FOUNDED in 1519", subject="ARTICLE")
    # Not a replayed subject.
    _claim(test_db, "The canal opened in 1914", subject="EDU_MATERIAL")
    llm.answers = {own_text: other.id, words_text: twin.id}
    return SimpleNamespace(own=own_text, other=other, words=words_text, twin=twin)


def test_replay_metrics_and_no_writes(test_db, replay_corpus, llm, monkeypatch):
    before = _counts(test_db)
    for name in ("add", "commit", "flush", "delete"):
        monkeypatch.setattr(test_db, name, lambda *a, **k: pytest.fail("replay wrote"))

    report = replay.run_replay(test_db, floors=(0.5, 0.8), top_k=5)

    monkeypatch.undo()
    assert _counts(test_db) == before
    meta = report["meta"]
    assert meta["claims"] == 2
    assert meta["claims_with_own_kb_row"] == 1
    by = {r["selector"]: r for r in report["results"]}
    assert set(by) == {"word_overlap", "vector@0.5", "vector@0.8"}

    old = by["word_overlap"]
    # Word overlap finds the Morgan twin only (the own-key row is excluded and
    # the Pedrarias row shares one significant word).
    assert old["matches"] == 1
    assert old["hit_rate"] == 0.5
    assert old["haiku_calls"] == 1
    assert old["haiku_input_tokens"] == 100
    assert old["haiku_output_tokens"] == 10
    # INACCURATE claim matched to a VERIFIED row: disagreement.
    assert old["verdict_agreement"] == 0.0
    assert old["verdict_agreement_n"] == 1

    low = by["vector@0.5"]
    assert low["matches"] == 2
    assert low["hit_rate"] == 1.0
    assert low["overlap_vs_word_overlap"] == {
        "both": 1,
        "only_this": 1,
        "only_word_overlap": 0,
        "same_kb_row": 1,
    }
    # VERIFIED claim -> VERIFIED row agrees; INACCURATE -> VERIFIED does not.
    assert low["verdict_agreement"] == 0.5

    high = by["vector@0.8"]
    assert high["matches"] == 1
    # The Morgan claim still has candidates (the 1519 rows), just not its twin.
    assert high["claims_with_candidates"] == 2
    assert high["by_subject"]["SUGGESTION"]["hit_rate"] == 1.0
    assert high["by_subject"]["RESEARCH"]["hit_rate"] == 0.0

    # The own-key row is never a candidate of its own claim (the 1519 claim
    # matched the *other* row at every floor, though its own row is nearest).
    own_id = (
        test_db.query(VerifiedClaim).filter_by(claim_key=_normalize(replay_corpus.own)).one().id
    )
    claims = replay.load_replay_claims(test_db)
    kb = replay.load_kb(test_db)
    self_ids = replay._self_ids(claims, kb)
    assert self_ids == [own_id, None]
    assert own_id not in replay.old_candidates(claims, kb, self_ids).candidates[0]
    assert fc.select_vector_candidates(
        [(own_id, 1.0), (replay_corpus.other.id, 0.9)],
        top_k=5,
        min_similarity=0.5,
        exclude_id=own_id,
    ) == [replay_corpus.other.id]


def test_replay_dry_run_calls_nothing(test_db, replay_corpus, llm, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_queries", lambda *a, **k: pytest.fail("voyage"))
    report = replay.run_replay(test_db, floors=(0.5, 0.6, 0.7), top_k=5, dry_run=True)
    assert llm.prompts == []
    est = report["estimate"]
    assert est["word_overlap"]["calls"] == 1
    assert est["total_cost_usd_max"] >= est["word_overlap"]["cost_usd_max"]
    assert "Dry run" in replay.render_markdown(report)


def test_replay_writes_markdown_and_json(test_db, replay_corpus, llm, tmp_path):
    report = replay.run_replay(test_db, floors=(0.5,), top_k=5)
    md, js = replay.write_report(report, tmp_path / "2026-10-09-kb-matching.md")
    text = md.read_text()
    assert "| word_overlap |" in text and "| vector@0.5 |" in text
    assert js.suffix == ".json" and '"hit_rate"' in js.read_text()


def test_replay_uses_its_own_feature_label(test_db, replay_corpus, monkeypatch):
    labels = []

    def fake_create_message(**kwargs):
        labels.append(kwargs["feature"])
        return MagicMock(content=[MagicMock(text="MATCH: 1 | NONE")], usage=None)

    monkeypatch.setattr(fc, "create_message", fake_create_message)
    replay.run_replay(test_db, floors=(0.5,), top_k=5)
    assert labels and set(labels) == {"eval_kb_matching"}


def test_readonly_session_refuses_writes(test_db, test_engine, monkeypatch):
    import app.core.database as database
    from sqlalchemy.exc import DBAPIError

    monkeypatch.setattr(database, "engine", test_engine)
    db = replay._readonly_session()
    try:
        with pytest.raises(DBAPIError, match="read-only"):
            db.add(
                VerifiedClaim(claim_key="x", claim_text="x", claim_type="DATE", verdict="VERIFIED")
            )
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_main_dry_run(test_db, replay_corpus, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(replay, "_readonly_session", lambda: test_db)
    assert replay.main(["--dry-run", "--limit", "10", "--out", str(tmp_path / "r.md")]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert not (tmp_path / "r.md").exists()
