"""Situating sentences for article chunks (#697, epic #698).

The generator (``app/services/knowledge/context.py``), the chunk text it feeds
(embedding and rerank get it, readers and the answer model do not), the
indexer's use of it (generated once, kept, retried after a failure, quiet with
no key) and the reindex sweep's dry run.
"""

import json
from types import SimpleNamespace

import pytest
from app.models.knowledge_chunk import KnowledgeChunk
from app.prompts.knowledge_context import build_user_prompt
from app.services import ask_pic, embeddings, llm
from app.services.knowledge import claims, indexer, retrieval
from app.services.knowledge import context as chunk_context
from app.services.knowledge.chunker import (
    build_embedding_text,
    chunk_article,
    content_hash,
    contextual_text,
)
from scripts import reindex_knowledge
from scripts.eval_rag import tables
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

DIMS = 1024


def _para(word: str, n: int = 120) -> str:
    return " ".join(f"{word}{i}." for i in range(n))


def _body(*sections: tuple[str, str]) -> str:
    return "\n\n".join(f"## {heading}\n\n{text}" for heading, text in sections)


def _sentence(language: str, index: int) -> str:
    return f"{language} sentence {index}: Balboa reaches the Gulf of Urabá in 1510."


def _message(payload, *, usage=(1000, 200)):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="..."),
            SimpleNamespace(type="text", text=json.dumps(payload)),
        ],
        usage=SimpleNamespace(
            input_tokens=usage[0],
            output_tokens=usage[1],
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
    )


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts, *, feature="unlabeled"):
        self.calls.append(list(texts))
        return [[0.01 * (i % 7 + 1)] * DIMS for i in range(len(texts))]

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]


class FakeHaiku:
    """Fake ``create_message`` for the context module: one sentence per wanted index."""

    def __init__(self):
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.skip: set[int] = set()  # indexes left out of the reply

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        prompt = kwargs["messages"][0]["content"]
        language = "es" if 'language="Spanish"' in prompt else "en"
        wanted = [int(i) for i in prompt.rsplit(":", 1)[1].strip(" .").split(", ")]
        return _message(
            {
                "contexts": [
                    {"index": i, "context": _sentence(language, i)}
                    for i in wanted
                    if i not in self.skip
                ]
            }
        )


@pytest.fixture
def env(test_engine, monkeypatch):
    """Indexer + sweep on the test DB, fake Voyage, fake Haiku, an Anthropic key set."""
    from app.core.config import get_settings

    factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(indexer, "SessionLocal", factory)
    monkeypatch.setattr(claims, "SessionLocal", factory)
    monkeypatch.setattr(reindex_knowledge, "SessionLocal", factory)
    monkeypatch.setattr(get_settings(), "voyage_api_key", "fake-key")
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "fake-key")
    embedder = FakeEmbedder()
    monkeypatch.setattr(embeddings, "embed_documents", embedder)
    haiku = FakeHaiku()
    monkeypatch.setattr(chunk_context, "create_message", haiku)
    return SimpleNamespace(embedder=embedder, haiku=haiku, settings=get_settings())


@pytest.fixture
def article(test_db, article_factory):
    a = article_factory(
        title="The Canal",
        title_es="El Canal",
        content=_body(("Origins", _para("origin")), ("Legacy", _para("legacy"))),
    )
    a.content_es = _body(("Orígenes", _para("origen")))
    test_db.commit()
    return a


def _rows(test_db, article_id):
    c = KnowledgeChunk.__table__.c
    test_db.expire_all()
    return test_db.execute(
        select(c.id, c.language, c.chunk_index, c.content, c.content_hash, c.meta)
        .where(c.source_type == "ARTICLE", c.source_id == article_id)
        .order_by(c.language, c.chunk_index)
    ).all()


def _expected(a):
    return chunk_article(a.title, a.content, "en") + chunk_article(a.title_es, a.content_es, "es")


# --- Chunk text ---------------------------------------------------------------


def test_contextual_text_puts_the_sentence_under_the_prefix():
    assert contextual_text("T > H", "Who and when.", "Body.") == "T > H\nWho and when.\n\nBody."
    assert contextual_text("T > H", None, "Body.") == build_embedding_text("T", "H", "Body.")
    assert contextual_text("", "Who.", "Body.") == "Who.\n\nBody."


def test_the_hash_ignores_the_sentence():
    chunk = chunk_article("The Canal", _body(("Origins", _para("origin", 20))), "en")[0]
    situated = indexer.with_context(chunk, "Who and when.")

    assert situated.content_hash == chunk.content_hash == content_hash(chunk.embedding_text)
    assert situated.text_to_embed == "The Canal > Origins\nWho and when.\n\n" + chunk.content
    assert chunk.text_to_embed == chunk.embedding_text
    assert situated.content == chunk.content


# --- The generator --------------------------------------------------------------


def test_prompt_lists_every_chunk_and_the_wanted_indexes():
    prompt = build_user_prompt('A "T" & B', "es", [(0, "H1", "a < b"), (1, None, "c")], wanted=[1])
    assert "title='A \"T\" &amp; B'" in prompt
    assert 'language="Spanish"' in prompt
    assert '<chunk index="0" heading="H1">\na &lt; b\n</chunk>' in prompt
    assert '<chunk index="1">' in prompt
    assert prompt.endswith("chunk indexes: 1.")


def test_parse_contexts_keeps_only_wanted_clean_sentences():
    data = {
        "contexts": [
            {"index": 0, "context": "  Balboa   arrives.\n"},
            {"index": 0, "context": "duplicate"},
            {"index": 5, "context": "not asked for"},
            {"index": True, "context": "bool is not an index"},
            {"index": 1, "context": ""},
            {"index": 2, "context": "x " * 400},
            "junk",
        ]
    }
    out = chunk_context.parse_contexts(data, [0, 1, 2])
    assert out[0] == "Balboa arrives."
    assert 1 not in out and 5 not in out
    assert len(out[2]) <= chunk_context.MAX_CONTEXT_CHARS + 1 and out[2].endswith("…")
    assert chunk_context.parse_contexts(["not a dict"], [0]) == {}
    assert chunk_context.parse_contexts({"contexts": "no"}, [0]) == {}


def test_situate_calls_haiku_low_effort_with_the_schema(env):
    chunks = chunk_article("The Canal", _body(("Origins", _para("origin"))), "en")
    result = chunk_context.situate("The Canal", "en", chunks, [0], log_label="t")

    assert result.status == "ok"
    assert result.contexts == {0: _sentence("en", 0)}
    call = env.haiku.calls[0]
    assert call["feature"] == "knowledge_context"
    assert call["model"] == llm.HAIKU_MODEL
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert result.cost_usd > 0


def test_situate_without_a_key_makes_no_call(env):
    env.settings.anthropic_api_key = ""
    chunks = chunk_article("T", "Body text.", "en")

    result = chunk_context.situate("T", "en", chunks, [0])

    assert result.status == "disabled" and result.contexts == {}
    assert env.haiku.calls == []


@pytest.mark.parametrize(
    "error",
    [
        llm.BudgetExceededError("cap"),
        llm.TruncationError("cut"),
        llm.RefusalError("no"),
        RuntimeError("transport"),
    ],
)
def test_situate_never_raises(env, error):
    env.haiku.error = error
    chunks = chunk_article("T", "Body text.", "en")

    result = chunk_context.situate("T", "en", chunks, [0])

    assert result.contexts == {}
    assert result.status.endswith(type(error).__name__)


def test_situate_malformed_reply_is_no_sentence(env, monkeypatch):
    monkeypatch.setattr(chunk_context, "create_message", lambda **_: _message({"other": 1}))
    chunks = chunk_article("T", "Body text.", "en")

    result = chunk_context.situate("T", "en", chunks, [0])

    assert result.status == "malformed" and result.contexts == {}


# --- The indexer ------------------------------------------------------------------


def test_first_index_embeds_and_stores_the_sentence(env, test_db, article):
    result = indexer.index_article(article.id)

    expected = _expected(article)
    assert result.status == "indexed"
    assert result.context_wanted == result.context_written == len(expected)
    assert result.context_calls == 2  # one per language
    assert result.context_cost_usd > 0
    rows = _rows(test_db, article.id)
    assert len(rows) == len(expected)
    for row in rows:
        assert row.meta["context"] == _sentence(row.language, row.chunk_index)
        assert row.meta["context"] not in row.content  # what readers see is unchanged
    by_hash = {c.content_hash: c for c in expected}
    assert sorted(env.embedder.texts) == sorted(
        contextual_text(by_hash[r.content_hash].prefix, r.meta["context"], r.content) for r in rows
    )


def test_rerun_makes_no_haiku_or_embedding_call(env, test_db, article):
    indexer.index_article(article.id)
    before = _rows(test_db, article.id)
    env.haiku.calls.clear()
    env.embedder.calls.clear()

    result = indexer.index_article(article.id)

    assert result.status == "unchanged"
    assert env.haiku.calls == [] and env.embedder.calls == []
    assert _rows(test_db, article.id) == before


def test_reindex_adds_the_twin_key_in_place_keeping_the_sentence(env, test_db, article):
    """#713: rows indexed before the twin key existed gain it on the next run
    as an in-place meta update: same rows, same sentence, no Haiku or Voyage call."""
    from app.services.knowledge.chunker import TWIN_KEY
    from sqlalchemy import update

    indexer.index_article(article.id)
    table = KnowledgeChunk.__table__
    for row in _rows(test_db, article.id):
        meta = {k: v for k, v in row.meta.items() if k != TWIN_KEY}
        test_db.execute(update(table).where(table.c.id == row.id).values(meta=meta))
    test_db.commit()
    before = _rows(test_db, article.id)
    assert all(TWIN_KEY not in r.meta for r in before)
    env.haiku.calls.clear()
    env.embedder.calls.clear()

    assert indexer.index_article(article.id, dry_run=True).status == "dry_run"
    result = indexer.index_article(article.id)

    assert result.status == "indexed"
    assert (result.embedded, result.deleted) == (0, 0)
    assert env.haiku.calls == [] and env.embedder.calls == []
    after = _rows(test_db, article.id)
    assert [r.id for r in after] == [r.id for r in before]
    expected = {(c.meta["language"], c.content_hash): c for c in _expected(article)}
    for old, new in zip(before, after, strict=True):
        assert new.meta["context"] == old.meta["context"]
        assert new.meta[TWIN_KEY] == expected[(new.language, new.content_hash)].meta[TWIN_KEY]
    assert indexer.index_article(article.id).status == "unchanged"


def test_editing_one_section_situates_only_its_chunks(env, test_db, article):
    indexer.index_article(article.id)
    env.haiku.calls.clear()
    article.content = _body(("Origins", _para("origin")), ("Legacy", _para("changed")))
    test_db.commit()

    result = indexer.index_article(article.id)

    changed = [c for c in _expected(article) if "changed0." in c.content]
    assert result.context_wanted == len(changed)
    assert len(env.haiku.calls) == 1  # English only
    prompt = env.haiku.calls[0]["messages"][0]["content"]
    assert "origin0." in prompt  # the whole article is the context
    assert all(r.meta.get("context") for r in _rows(test_db, article.id))


def test_failed_call_indexes_without_the_sentence_and_the_next_run_retries(env, test_db, article):
    env.haiku.error = RuntimeError("down")

    result = indexer.index_article(article.id)

    assert result.status == "indexed"
    assert result.context_written == 0
    rows = _rows(test_db, article.id)
    assert rows and not any((r.meta or {}).get("context") for r in rows)
    assert sorted(env.embedder.texts) == sorted(c.embedding_text for c in _expected(article))

    env.haiku.error = None
    env.embedder.calls.clear()
    old_ids = {r.id for r in rows}
    retry = indexer.index_article(article.id)

    assert retry.status == "indexed"
    assert retry.context_written == len(rows)
    upgraded = _rows(test_db, article.id)
    assert all(r.meta.get("context") for r in upgraded)
    assert not old_ids & {r.id for r in upgraded}  # replaced in the same transaction
    assert len(env.embedder.texts) == len(rows)


def test_partial_reply_keeps_the_rest_for_later(env, test_db, article):
    env.haiku.skip = {0}

    indexer.index_article(article.id)

    rows = _rows(test_db, article.id)
    missing = [r for r in rows if not (r.meta or {}).get("context")]
    assert {(r.language, r.chunk_index) for r in missing} == {("en", 0), ("es", 0)}


# --- Retry cap (#712) -------------------------------------------------------------


def _attempts(rows) -> dict[tuple[str, int], int]:
    return {(r.language, r.chunk_index): (r.meta or {}).get("context_attempts", 0) for r in rows}


def _exhaust(article_id: int) -> list:
    """Run the indexer until the cap is reached (and twice more)."""
    return [indexer.index_article(article_id) for _ in range(chunk_context.MAX_ATTEMPTS + 2)]


def test_repeated_failures_stop_calling_haiku_after_the_cap(env, test_db, article):
    env.haiku.error = RuntimeError("down")

    results = _exhaust(article.id)

    # One call per language per attempt, then nothing more.
    assert len(env.haiku.calls) == 2 * chunk_context.MAX_ATTEMPTS
    assert [r.status for r in results[chunk_context.MAX_ATTEMPTS :]] == ["unchanged"] * 2
    rows = _rows(test_db, article.id)
    assert set(_attempts(rows).values()) == {chunk_context.MAX_ATTEMPTS}
    assert all(r.meta.get("context_failed_at") for r in rows)
    assert results[-1].context_missing == results[-1].context_capped == len(rows)
    # Counting an attempt rewrites the meta only: nothing is re-embedded.
    assert len(env.embedder.texts) == len(rows)


def test_an_empty_sentence_counts_as_a_failed_attempt(env, test_db, article):
    env.haiku.skip = {0}  # chunk 0 never gets a sentence (_clean -> "")

    _exhaust(article.id)

    # The first call covers every chunk; then only chunk 0 per language, until capped.
    assert len(env.haiku.calls) == 2 * chunk_context.MAX_ATTEMPTS
    attempts = _attempts(_rows(test_db, article.id))
    assert attempts.pop(("en", 0)) == attempts.pop(("es", 0)) == chunk_context.MAX_ATTEMPTS
    assert set(attempts.values()) == {0}  # the situated chunks carry no count


def test_a_budget_stop_is_not_counted(env, test_db, article):
    env.haiku.error = llm.BudgetExceededError("cap")

    indexer.index_article(article.id)

    assert set(_attempts(_rows(test_db, article.id)).values()) == {0}


def test_retry_context_resets_the_count(env, test_db, article):
    env.haiku.error = RuntimeError("down")
    _exhaust(article.id)
    env.haiku.calls.clear()

    # Still failing: one more call per language, and the count starts over.
    assert reindex_knowledge.main(["--article", str(article.id), "--retry-context"]) == 0
    assert len(env.haiku.calls) == 2
    assert set(_attempts(_rows(test_db, article.id)).values()) == {1}

    env.haiku.error = None
    _exhaust(article.id)  # under the cap again, so a plain run retries
    assert reindex_knowledge.main(["--article", str(article.id), "--retry-context"]) == 0
    rows = _rows(test_db, article.id)
    assert all(r.meta.get("context") for r in rows)
    assert not any("context_attempts" in r.meta or "context_failed_at" in r.meta for r in rows)


def test_retry_context_on_a_capped_article_succeeds(env, test_db, article):
    env.haiku.error = RuntimeError("down")
    _exhaust(article.id)
    env.haiku.error = None
    env.haiku.calls.clear()

    assert indexer.index_article(article.id).status == "unchanged"  # capped: no call
    assert env.haiku.calls == []
    result = indexer.index_article(article.id, retry_context=True)

    assert result.status == "indexed"
    assert result.context_written == len(_expected(article))
    assert result.context_missing == result.context_capped == 0
    assert all(r.meta.get("context") for r in _rows(test_db, article.id))


def test_an_edited_chunk_gets_fresh_attempts(env, test_db, article):
    env.haiku.error = RuntimeError("down")
    _exhaust(article.id)
    env.haiku.calls.clear()
    article.content = _body(("Origins", _para("origin")), ("Legacy", _para("changed")))
    test_db.commit()

    indexer.index_article(article.id)

    changed = {c.content_hash for c in _expected(article) if "changed0." in c.content}
    assert changed
    assert len(env.haiku.calls) == 1  # English only, for the edited chunk(s)
    for row in _rows(test_db, article.id):
        expected = 1 if row.content_hash in changed else chunk_context.MAX_ATTEMPTS
        assert row.meta["context_attempts"] == expected


def test_sweep_reports_sentence_less_chunks(env, test_db, article):
    env.haiku.skip = {0}
    indexer.index_article(article.id)

    dry = reindex_knowledge.summarize(
        reindex_knowledge.sweep(reindex_knowledge.article_ids(), dry_run=True), dry_run=True
    )
    assert dry["context_missing"] == 2 and dry["context_capped"] == 0

    _exhaust(article.id)
    env.haiku.calls.clear()
    real = reindex_knowledge.summarize(
        reindex_knowledge.sweep(reindex_knowledge.article_ids()), dry_run=False
    )
    assert real["statuses"]["unchanged"] >= 1 and env.haiku.calls == []
    assert real["context_missing"] == real["context_capped"] == 2


def test_no_anthropic_key_indexes_quietly_without_sentences(env, test_db, article):
    env.settings.anthropic_api_key = ""

    first = indexer.index_article(article.id)
    again = indexer.index_article(article.id)

    assert first.status == "indexed" and first.context_wanted == 0
    assert again.status == "unchanged"  # no churn retrying sentences it cannot get
    assert env.haiku.calls == []


def test_dry_run_estimates_the_upgrade_without_calls(env, test_db, article):
    env.settings.anthropic_api_key = ""
    indexer.index_article(article.id)  # indexed before #697: no sentences
    env.settings.anthropic_api_key = "fake-key"
    env.embedder.calls.clear()
    before = _rows(test_db, article.id)

    result = indexer.index_article(article.id, dry_run=True)

    assert result.status == "dry_run"
    assert result.context_wanted == result.embedded == result.deleted == len(before)
    assert result.context_calls == 2
    assert result.context_input_tokens > 0 and result.context_cost_usd > 0
    assert result.embed_tokens > sum(c.token_count for c in _expected(article))
    assert env.haiku.calls == [] and env.embedder.calls == []
    assert _rows(test_db, article.id) == before


def test_claims_get_no_sentence(env, test_db, article):
    chunk = claims._claim_chunk(1, "Balboa reached the Pacific in 1513.", {})
    assert chunk.text_to_embed == chunk.embedding_text == chunk.content


# --- Retrieval and Ask PIC ----------------------------------------------------------


def test_rerank_scores_title_heading_sentence_and_chunk(env, test_db, article, monkeypatch):
    indexer.index_article(article.id)
    documents = []

    def fake_rerank(query, docs, *, top_k=None, feature="unlabeled"):
        documents.extend(docs)
        return [embeddings.RerankResult(i, 0.9 - i / 100) for i in range(len(docs))]

    monkeypatch.setattr(embeddings, "embed_query", lambda q, **_: [0.01] * DIMS)
    monkeypatch.setattr(embeddings, "rerank", fake_rerank)

    result = retrieval.search(test_db, "origin", rerank=True, k=5, visibility="internal")

    assert result.reranked
    es = next(c for c in result.chunks if c.language == "es")
    assert es.article_title == "El Canal"
    assert retrieval.rerank_text(es) == (
        f"El Canal > Orígenes\n{_sentence('es', es.chunk_index)}\n\n{es.content}"
    )
    assert set(documents) == {retrieval.rerank_text(c) for c in result.chunks[: len(documents)]}
    # What the answer model is shown: the title and the chunk, not the sentence.
    passages = ask_pic._passages(result, "en")
    prompt = ask_pic._user_prompt(result, passages, "When?", "en")
    assert "The Canal" in prompt and "origin0." in prompt
    assert "Gulf of Urabá" not in prompt


def test_rerank_text_without_a_sentence_is_the_embedding_text():
    hit = retrieval.ChunkHit(
        chunk_id=1,
        source_type="ARTICLE",
        source_id=1,
        article_id=1,
        language="en",
        chunk_index=0,
        heading_path="H",
        content="Body.",
        meta={"language": "en"},
        article_title="T",
    )
    assert retrieval.rerank_text(hit) == build_embedding_text("T", "H", "Body.")
    claim = retrieval.ChunkHit(
        chunk_id=2,
        source_type="ARTICLE_CLAIM",
        source_id=9,
        article_id=1,
        language="en",
        chunk_index=0,
        heading_path=None,
        content="A claim.",
        meta={},
        article_title="T",
    )
    assert retrieval.rerank_text(claim) == "A claim."


# --- The sweep and the eval tables --------------------------------------------------


def test_sweep_dry_run_reports_haiku_cost_and_duration(env, test_db, article):
    results = reindex_knowledge.sweep(reindex_knowledge.article_ids(), dry_run=True)
    totals = reindex_knowledge.summarize(results, dry_run=True)

    assert totals["context_wanted"] == len(_expected(article))
    assert totals["context_calls"] == 2
    assert totals["context_cost_usd"] > 0
    assert totals["est_minutes"] > 0
    assert env.haiku.calls == []


def test_sweep_with_workers_is_idempotent(env, test_db, article, article_factory):
    other = article_factory(title="Other", content=_body(("Only", _para("only"))))

    assert reindex_knowledge.main(["--all", "--workers", "2"]) == 0
    assert _rows(test_db, other.id) and _rows(test_db, article.id)
    env.haiku.calls.clear()
    env.embedder.calls.clear()

    assert reindex_knowledge.main(["--all", "--workers", "2"]) == 0
    assert env.haiku.calls == [] and env.embedder.calls == []


def test_sweep_rejects_too_many_workers(env):
    with pytest.raises(SystemExit):
        reindex_knowledge.main(["--all", "--workers", "9"])


def test_eval_tables_rebuild_the_contextual_text(env, test_db, article):
    indexer.index_article(article.id)

    source = tables._source_chunks(test_db)

    rows = {r.id: r for r in _rows(test_db, article.id)}
    article_chunks = [s for s in source if s.id in rows]
    assert article_chunks and all(s.hash_matches for s in article_chunks)
    for s in article_chunks:
        assert rows[s.id].meta["context"] in s.embedding_text
