"""RAG eval harness against the test DB: refusal, eval tables, search(table=), run (#627).

Voyage and Anthropic are mocked; nothing here calls a real API.
"""

import json
from types import SimpleNamespace

import pytest
from app.core.config import get_settings
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import embeddings, llm
from app.services.knowledge import retrieval
from app.services.knowledge.chunker import build_embedding_text, content_hash
from scripts.eval_rag import cli, drafting, runner, tables
from scripts.eval_rag.golden import parse_golden
from scripts.eval_rag.tables import ProductionRefusedError, assert_not_production
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.orm import sessionmaker

DIMS = 1024
MODEL = "voyage-4-lite"


def _vec(axis: int) -> list[float]:
    v = [0.0] * DIMS
    v[axis] = 1.0
    return v


CANAL, RAILROAD, OTHER = _vec(0), _vec(1), _vec(2)


def _axis_for(text_: str) -> list[float]:
    t = text_.lower()
    if "canal" in t:
        return CANAL
    if "railroad" in t or "ferrocarril" in t:
        return RAILROAD
    return OTHER


# --- Production refusal ---------------------------------------------------------------


PROD_URL = "postgresql://doadmin:x@db-postgresql-nyc1-00000-do-user-1-0.g.db.ondigitalocean.com:25060/defaultdb"


@pytest.mark.parametrize(
    ("environment", "url"),
    [
        ("production", "postgresql://panama:localdev@db:5432/panama_in_context"),
        ("Production", None),
        ("development", PROD_URL),
        ("test", PROD_URL.replace("postgresql://", "postgresql+psycopg2://")),
    ],
)
def test_refuses_production(environment, url):
    with pytest.raises(ProductionRefusedError):
        assert_not_production(environment, url)


@pytest.mark.parametrize(
    "url",
    ["postgresql://panama:localdev@db:5432/panama_in_context", "postgresql://u:p@localhost:5433/x"],
)
def test_allows_local(url):
    assert_not_production("development", url)


def test_cli_exits_before_touching_a_production_db(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(get_settings(), "environment", "production")
    opened = []

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(engine=SimpleNamespace(url=_url("postgresql://u:p@db:5432/x")))

        def close(self):
            opened.append("closed")

        def execute(self, *a, **k):  # pragma: no cover - must never run
            raise AssertionError("queried a production DB")

    monkeypatch.setattr(cli, "_session", FakeSession)
    golden_file = tmp_path / "g.json"
    golden_file.write_text(json.dumps({"version": 1, "questions": [_q("q1", [1])]}))
    for argv in (
        ["retrieval", "--golden", str(golden_file), "--dry-run"],
        ["draft-questions", "--dry-run"],
        ["drop-tables"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
    assert opened == ["closed"] * 3
    assert "production" in capsys.readouterr().err


def _url(value):
    from sqlalchemy.engine import make_url

    return make_url(value)


# --- Fixtures ---------------------------------------------------------------------------


@pytest.fixture
def voyage(monkeypatch):
    """Fake Voyage: keyword-axis vectors, billed tokens counted through the seam."""
    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
    embeddings.reset_usage_totals()
    calls = {"documents": [], "queries": []}

    def fake_embed(texts, input_type, feature, *, timeout=None, max_attempts=None, model=None):
        model = model or get_settings().embedding_model
        calls["documents" if input_type == "document" else "queries"].append((model, list(texts)))
        embeddings._log_usage(
            feature=feature,
            endpoint="embeddings",
            model=model,
            inputs=len(texts),
            total_tokens=10 * len(texts),
        )
        return [_axis_for(t) for t in texts]

    def fake_rerank(query, documents, *, top_k=None, feature="unlabeled"):
        embeddings._log_usage(
            feature=feature,
            endpoint="rerank",
            model="rerank-2.5",
            inputs=len(documents),
            total_tokens=100,
        )
        return [embeddings.RerankResult(i, 0.9 - i / 10) for i in range(len(documents))]

    monkeypatch.setattr(embeddings, "_embed", fake_embed)
    monkeypatch.setattr(embeddings, "rerank", fake_rerank)
    yield calls
    embeddings.reset_usage_totals()


@pytest.fixture
def eval_cleanup(test_db):
    yield
    test_db.rollback()
    for model in (MODEL, "voyage-4"):
        tables.drop_eval_table(test_db, model)


def _chunk(
    db, article, *, content, language="en", heading="Intro", embedding=None, model=None, idx=0
):
    title = (article.title_es or article.title) if language == "es" else article.title
    hashed = content_hash(build_embedding_text(title, heading, content))
    return db.execute(
        insert(KnowledgeChunk.__table__)
        .values(
            source_type="ARTICLE",
            source_id=article.id,
            article_id=article.id,
            language=language,
            chunk_index=idx,
            heading_path=heading,
            content=content,
            content_hash=hashed,
            token_count=len(content) // 4 + 1,
            embedding=embedding,
            embedding_model=model,
        )
        .returning(KnowledgeChunk.__table__.c.id)
    ).scalar_one()


@pytest.fixture
def corpus(test_db, article_factory):
    canal = article_factory(title="The Canal", title_es="El Canal")
    railroad = article_factory(title="The Railroad", title_es="El Ferrocarril")
    ids = {
        "canal_en": _chunk(
            test_db, canal, content="The canal opened in 1914 after a decade of work.", idx=0
        ),
        "canal_es": _chunk(
            test_db, canal, content="El canal abrió en 1914 tras una década.", language="es", idx=1
        ),
        # Already embedded with voyage-4: reused for that model, re-embedded for lite.
        "rail_en": _chunk(
            test_db,
            railroad,
            content="The railroad crossed the isthmus in 1855.",
            heading="Construction",
            embedding=RAILROAD,
            model="voyage-4",
            idx=0,
        ),
    }
    test_db.commit()
    return {"canal": canal, "railroad": railroad, "chunks": ids}


# --- Eval tables ------------------------------------------------------------------------


def test_eval_table_name_is_sanitised():
    assert tables.eval_table_name("voyage-4-lite") == "eval_knowledge_chunks_voyage_4_lite"
    with pytest.raises(ValueError):
        tables.model_slug("---")


def test_eval_table_is_outside_base_metadata():
    from app.core.database import Base

    table = tables.eval_table(MODEL)
    assert table.name not in Base.metadata.tables
    assert [c.name for c in table.columns] == [c.name for c in KnowledgeChunk.__table__.columns]


def test_plan_is_read_only_and_sync_copies_with_same_ids(test_db, voyage, corpus, eval_cleanup):
    plan = tables.plan_sync(test_db, MODEL)
    assert not tables.eval_table_exists(test_db, MODEL)  # planning created nothing
    assert plan.source_rows == 3 and plan.hash_mismatches == 0
    assert len(plan.embed) == 3 and plan.copy_ids == [] and plan.embed_tokens > 0
    tables.apply_sync(test_db, plan)

    table = tables.eval_table(MODEL)
    rows = test_db.execute(select(table.c.id, table.c.embedding_model, table.c.search_vector)).all()
    assert sorted(r.id for r in rows) == sorted(corpus["chunks"].values())
    assert {r.embedding_model for r in rows} == {MODEL}
    assert all(r.search_vector for r in rows)  # generated column came along
    # Embedded with the eval model, with the indexer's embedding text.
    ((model, texts),) = voyage["documents"]
    assert model == MODEL
    assert "The Railroad > Construction\n\nThe railroad crossed the isthmus in 1855." in texts
    assert "El Canal > Intro\n\nEl canal abrió en 1914 tras una década." in texts
    # No FK to articles and no default on the id (independent of knowledge_chunks).
    assert inspect(test_db.get_bind()).get_foreign_keys(table.name) == []
    # knowledge_chunks itself untouched.
    assert (
        test_db.execute(
            select(KnowledgeChunk.__table__.c.embedding_model).where(
                KnowledgeChunk.__table__.c.id == corpus["chunks"]["canal_en"]
            )
        ).scalar_one()
        is None
    )

    # A re-run is free.
    again = tables.plan_sync(test_db, MODEL)
    assert (again.keep, len(again.embed), again.stale_ids) == (3, 0, [])


def test_same_model_vectors_are_copied_not_re_embedded(test_db, voyage, corpus, eval_cleanup):
    plan = tables.plan_sync(test_db, "voyage-4")
    assert plan.copy_ids == [corpus["chunks"]["rail_en"]]
    assert len(plan.embed) == 2
    tables.apply_sync(test_db, plan)
    assert sum(len(t) for _, t in voyage["documents"]) == 2
    no_reuse = tables.plan_sync(test_db, "voyage-4", reuse=False)
    assert no_reuse.keep == 3  # already present and unchanged


def test_changed_and_deleted_source_rows_are_resynced(test_db, voyage, corpus, eval_cleanup):
    tables.apply_sync(test_db, tables.plan_sync(test_db, MODEL))
    c = KnowledgeChunk.__table__.c
    test_db.execute(
        KnowledgeChunk.__table__.update()
        .where(c.id == corpus["chunks"]["canal_en"])
        .values(content="The canal opened in 1914.", content_hash="changed")
    )
    test_db.execute(KnowledgeChunk.__table__.delete().where(c.id == corpus["chunks"]["canal_es"]))
    test_db.commit()
    plan = tables.plan_sync(test_db, MODEL)
    assert sorted(plan.stale_ids) == sorted(
        [corpus["chunks"]["canal_en"], corpus["chunks"]["canal_es"]]
    )
    assert [s.id for s in plan.embed] == [corpus["chunks"]["canal_en"]]
    assert plan.hash_mismatches == 1  # the hand-set hash no longer matches the text
    tables.apply_sync(test_db, plan)
    counts = tables.corpus_counts(test_db, tables.eval_table(MODEL))
    assert counts == {"total": 2, "ARTICLE": 2, "embedded": 2}


# --- retrieval.search(table=, embedding_model=) ---------------------------------------


def test_search_over_an_eval_table(test_db, voyage, corpus, eval_cleanup):
    tables.apply_sync(test_db, tables.plan_sync(test_db, MODEL))
    # Make the live table disagree: only the eval table has the canal vectors.
    result = retrieval.search(
        test_db,
        "When did the canal open?",
        mode="vector",
        table=tables.eval_table(MODEL),
        embedding_model=MODEL,
    )
    assert result.results[0].article_id == corpus["canal"].id
    assert voyage["queries"][-1][0] == MODEL
    # The live table has no canal vectors, so the same query cannot rank it.
    live = retrieval.search(test_db, "When did the canal open?", mode="vector")
    assert [h.article_id for h in live.results] == [corpus["railroad"].id]
    assert voyage["queries"][-1][0] == get_settings().embedding_model


def test_fts_over_an_eval_table_uses_its_generated_vector(test_db, voyage, corpus, eval_cleanup):
    tables.apply_sync(test_db, tables.plan_sync(test_db, MODEL))
    table = tables.eval_table(MODEL)
    test_db.execute(text(f"DELETE FROM {table.name} WHERE language = 'es'"))
    test_db.commit()
    result = retrieval.search(test_db, "década", mode="fts", table=table)
    assert result.results == []
    live = retrieval.search(test_db, "década", mode="fts")
    assert [h.article_id for h in live.results] == [corpus["canal"].id]


def test_default_search_never_reads_eval_tables(test_db, voyage, corpus, eval_cleanup):
    tables.apply_sync(test_db, tables.plan_sync(test_db, MODEL))
    table = tables.eval_table(MODEL)
    test_db.execute(text(f"DELETE FROM {table.name}"))
    test_db.commit()
    assert retrieval.search(test_db, "canal", mode="fts").results  # live rows still found
    assert retrieval.search(test_db, "canal", mode="fts", table=table).results == []


# --- The eval run + results writer ------------------------------------------------------


def _q(qid, expected, *, qtype="factual", language="en", question="When did the canal open?"):
    return {
        "id": qid,
        "language": language,
        "question": question,
        "type": qtype,
        "expected_article_ids": expected,
    }


def test_run_eval_and_summary(test_db, voyage, corpus, eval_cleanup):
    gold = parse_golden(
        {
            "version": 1,
            "questions": [
                _q("q1", [corpus["canal"].id]),
                _q("q2", [corpus["railroad"].id], question="Who built the railroad?"),
                _q(
                    "q3",
                    [corpus["canal"].id],
                    qtype="cross-lingual",
                    language="es",
                    question="¿Cuándo abrió el canal?",
                ),
                _q("q4", [], qtype="unanswerable", question="What is the capital of Mongolia?"),
            ],
        }
    )
    for model in (MODEL,):
        tables.apply_sync(test_db, tables.plan_sync(test_db, model))
    records = runner.run_eval(
        test_db, gold, models=[MODEL], modes=list(runner.MODES), table_for=tables.eval_table
    )
    assert len(records) == 4 * 4  # fts runs once
    assert {r["model"] for r in records if r["mode"] == "fts"} == {runner.FTS_MODEL_LABEL}

    by = {(r["question_id"], r["mode"]): r for r in records}
    assert by[("q1", "vector")]["metrics"]["recall@1"] == 1.0
    assert by[("q1", "vector")]["embed_tokens"] == 10
    assert by[("q1", "fts")]["embed_tokens"] == 0
    assert by[("q1", "hybrid+rerank")]["rerank_tokens"] == 100
    assert by[("q1", "hybrid+rerank")]["cost_usd"] > by[("q1", "hybrid")]["cost_usd"] > 0
    # The cross-lingual Spanish question finds the canal through its English chunks.
    assert by[("q3", "vector")]["ranked_article_ids"][0] == corpus["canal"].id
    assert by[("q4", "vector")]["metrics"]["mrr"] is None
    sim = by[("q4", "vector")]["signals"]["top1_vector_similarity"]
    assert sim is not None and sim < by[("q1", "vector")]["signals"]["top1_vector_similarity"]
    assert by[("q1", "hybrid+rerank")]["signals"]["top1_rerank_score"] == pytest.approx(0.9)
    assert by[("q2", "vector")]["section_rank"] is None  # no expected_section given

    summary = runner.summarize(records)
    rows = {(r["model"], r["mode"]): r for r in summary["rows"]}
    vector = rows[(MODEL, "vector")]
    assert vector["questions"] == 4 and vector["answerable"] == 3
    assert vector["recall@1"] == 1.0 and vector["mrr"] == 1.0
    assert vector["latency_p50_ms"] is not None
    floors = [a for a in summary["abstain"] if a["mode"] == "vector"][0]
    assert floors["signal"] == "top1_vector_similarity"
    assert floors["unanswerable"]["n"] == 1 and floors["answerable"]["n"] == 3
    assert floors["floors"]["abstain_all"]["unanswerable_abstained"] == 1.0
    assert list(floors["per_unanswerable"]) == ["q4"]


def test_cli_retrieval_end_to_end(
    test_db, test_engine, voyage, corpus, eval_cleanup, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_session", sessionmaker(bind=test_engine))
    golden_file = tmp_path / "golden.json"
    golden_file.write_text(
        json.dumps(
            {
                "version": 1,
                "questions": [
                    _q("q1", [corpus["canal"].id]),
                    _q("q2", [], qtype="unanswerable", question="Capital of Mongolia?"),
                ],
            }
        )
    )
    base = [
        "retrieval",
        "--golden",
        str(golden_file),
        "--models",
        MODEL,
        "--out-dir",
        str(tmp_path),
    ]

    assert cli.main([*base, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Estimated Voyage cost" in out and "Dry run" in out
    assert voyage["documents"] == [] and voyage["queries"] == []
    assert not tables.eval_table_exists(test_db, MODEL)

    assert cli.main([*base, "--date", "2026-10-09"]) == 0
    md = (tmp_path / "2026-10-09-retrieval.md").read_text()
    data = json.loads((tmp_path / "2026-10-09-retrieval.json").read_text())
    assert "| voyage-4-lite | vector |" in md and "Abstain floor" in md
    assert "python -m scripts.eval_rag retrieval" in md
    assert data["meta"]["questions"] == 2 and data["meta"]["unanswerable"] == 1
    assert data["meta"]["tables"][0]["counts"]["total"] == 3
    assert len(data["records"]) == 2 * 4
    assert {a["signal"] for a in data["summary"]["abstain"]} >= {
        "top1_vector_similarity",
        "top1_rrf_score",
        "top1_rerank_score",
        "top1_fts_score",
    }


def test_cli_retrieval_needs_a_key_for_embedding_modes(
    test_engine, corpus, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_session", sessionmaker(bind=test_engine))
    golden_file = tmp_path / "golden.json"
    golden_file.write_text(
        json.dumps({"version": 1, "questions": [_q("q1", [corpus["canal"].id])]})
    )
    code = cli.main(
        ["retrieval", "--golden", str(golden_file), "--models", MODEL, "--out-dir", str(tmp_path)]
    )
    assert code == 2
    assert "VOYAGE_API_KEY" in capsys.readouterr().err


# --- draft-questions --------------------------------------------------------------------


def test_draft_questions_end_to_end(test_db, test_engine, corpus, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_session", sessionmaker(bind=test_engine))
    monkeypatch.setattr(drafting, "MIN_CHUNK_CHARS", 10)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake")
    calls = []

    def fake_create_message(**kwargs):
        calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        assert kwargs["model"] == llm.SONNET_MODEL and kwargs["feature"] == "rag_eval_drafts"
        if "CANNOT answer" in prompt:
            body = {"drafts": [{"language": "en", "subtype": "off_topic", "question": "Mongolia?"}]}
        else:
            body = {"drafts": [{"ref": "P1", "question": "When did it open?", "answer": "1914."}]}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(body))])

    monkeypatch.setattr(llm, "create_message", fake_create_message)
    out = tmp_path / "drafts.json"
    argv = [
        "draft-questions",
        "--out",
        str(out),
        "--factual",
        "1",
        "--cross-lingual",
        "0",
        "--multi-article",
        "0",
        "--unanswerable",
        "2",
    ]

    assert cli.main([*argv, "--dry-run"]) == 0
    assert calls == [] and not out.exists()
    assert "estimated $" in capsys.readouterr().out

    before = test_db.execute(text("SELECT count(*) FROM knowledge_chunks")).scalar_one()
    assert cli.main(argv) == 0
    assert len(calls) == 2
    doc = json.loads(out.read_text())
    assert [d["type"] for d in doc["drafts"]] == ["factual", "unanswerable"]
    assert all(d["status"] is None for d in doc["drafts"])
    assert doc["drafts"][0]["source"]["excerpt"]
    assert test_db.execute(text("SELECT count(*) FROM knowledge_chunks")).scalar_one() == before
    assert cli.main(argv) == 1  # refuses to overwrite without --force
