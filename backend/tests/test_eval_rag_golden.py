"""RAG eval golden schema / loader, draft -> golden conversion, draft parsing (#627). No DB."""

import json
from pathlib import Path

import pytest
from scripts.eval_rag import cli, drafting, golden
from scripts.eval_rag.golden import GoldenError, drafts_to_golden, load_golden, parse_golden

FIXTURE = Path(__file__).parent / "fixtures" / "rag_golden_sample.json"


def _q(**overrides):
    base = {
        "id": "q1",
        "language": "en",
        "question": "Who built the railroad?",
        "type": "factual",
        "expected_article_ids": [1],
    }
    base.update(overrides)
    return base


def _doc(*questions):
    return {"version": 1, "questions": list(questions)}


# --- Loader ---------------------------------------------------------------------------


def test_fixture_loads():
    gold = load_golden(FIXTURE)
    assert [q.id for q in gold.questions] == ["t001", "t002", "t003", "t004"]
    assert [q.answerable for q in gold.questions] == [True, True, True, False]
    assert gold.questions[0].expected_section == "Construction"
    assert gold.questions[3].subtype == "off_topic"


def test_search_languages_default_and_cross_lingual():
    gold = load_golden(FIXTURE)
    by_id = {q.id: q for q in gold.questions}
    assert golden.search_languages(by_id["t001"]) is None  # both languages
    # A Spanish cross-lingual question searches the English chunks.
    assert golden.search_languages(by_id["t003"]) == ["en"]
    explicit = parse_golden(_doc(_q(search_languages=["es"]))).questions[0]
    assert golden.search_languages(explicit) == ["es"]


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (_q(language="fr"), "language"),
        (_q(type="trivia"), "type"),
        (_q(question="  "), "question"),
        (_q(expected_article_ids=[]), "needs at least one"),
        (_q(expected_article_ids=["1"]), "list of integers"),
        (_q(expected_article_ids=[True]), "list of integers"),
        (_q(expected_article_ids=[1, 1]), "duplicates"),
        (_q(type="multi-article", expected_article_ids=[1]), "at least two"),
        (_q(type="unanswerable", expected_article_ids=[3]), "no expected articles"),
        (_q(type="cross-lingual", expected_article_ids=[]), "needs at least one"),
        (_q(search_languages=[]), "search_languages"),
        (_q(search_languages=["de"]), "search_languages"),
        (_q(notes=5), "notes"),
        (_q(id=""), "id"),
    ],
)
def test_invalid_questions_are_rejected(question, message):
    with pytest.raises(GoldenError) as exc:
        parse_golden(_doc(question))
    assert any(message in e for e in exc.value.errors)


def test_every_problem_is_reported_and_ids_unique():
    with pytest.raises(GoldenError) as exc:
        parse_golden(_doc(_q(), _q(), _q(id="q2", language="xx", type="nope")))
    errors = exc.value.errors
    assert any("duplicate ids" in e for e in errors)
    assert any("language" in e for e in errors) and any("type" in e for e in errors)


def test_document_level_validation(tmp_path):
    with pytest.raises(GoldenError, match="version"):
        parse_golden({"version": 2, "questions": [_q()]})
    with pytest.raises(GoldenError, match="non-empty"):
        parse_golden({"version": 1, "questions": []})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(GoldenError, match="invalid JSON"):
        load_golden(bad)


def test_golden_round_trips():
    gold = load_golden(FIXTURE)
    again = parse_golden(json.loads(json.dumps(gold.to_dict())))
    assert again.to_dict() == gold.to_dict()


# --- Drafts -> golden -----------------------------------------------------------------


def _draft(i, status, **overrides):
    base = {
        "id": f"d{i:03d}",
        "status": status,
        "language": "en",
        "question": f"Question {i}?",
        "type": "factual",
        "expected_article_ids": [i],
        "expected_section": "Intro",
        "reference_answer": "An answer.",
        "source": {"chunk_id": i, "article_id": i, "excerpt": "..."},
        "notes": None,
    }
    base.update(overrides)
    return base


def test_drafts_to_golden_counts_and_strips_curation_fields():
    doc = {
        "generated_at": "2026-10-09T00:00:00+00:00",
        "drafts": [
            _draft(1, "keep"),
            _draft(2, "edit", question="Edited question?", language="es"),
            _draft(3, "drop"),
            _draft(4, "keep", type="unanswerable", expected_article_ids=[], subtype="off_topic"),
        ],
    }
    gold, report = drafts_to_golden(doc, drafts_file="x.json")
    assert (report.drafted, report.kept, report.edited, report.dropped) == (4, 2, 1, 1)
    assert [q.id for q in gold.questions] == ["d001", "d002", "d004"]
    assert gold.questions[1].question == "Edited question?"
    assert report.by_type == {"factual": 2, "unanswerable": 1}
    assert report.by_language == {"en": 2, "es": 1}
    assert gold.curation["drafts_file"] == "x.json"
    assert gold.curation["kept"] == 2 and gold.curation["drafts_generated_at"]
    out = gold.to_dict()
    assert "source" not in out["questions"][0] and "status" not in out["questions"][0]
    assert parse_golden(out).curation["dropped"] == 1


def test_undecided_drafts_are_refused():
    doc = {"drafts": [_draft(1, "keep"), _draft(2, None), _draft(3, "maybe")]}
    with pytest.raises(GoldenError, match="d002, d003"):
        drafts_to_golden(doc)


def test_an_invalid_kept_draft_is_refused():
    doc = {"drafts": [_draft(1, "keep", expected_article_ids=[])]}
    with pytest.raises(GoldenError, match="needs at least one"):
        drafts_to_golden(doc)


def test_golden_command_writes_file_and_refuses_overwrite(tmp_path, capsys):
    drafts = tmp_path / "drafts.json"
    drafts.write_text(json.dumps({"drafts": [_draft(1, "keep"), _draft(2, "drop")]}))
    out = tmp_path / "golden.json"
    assert cli.main(["golden", "--drafts", str(drafts), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "kept 1, edited 0, dropped 1" in printed
    assert "only 0 unanswerable" in printed
    assert [q.id for q in load_golden(out).questions] == ["d001"]
    assert cli.main(["golden", "--drafts", str(drafts), "--out", str(out)]) == 1
    assert cli.main(["golden", "--drafts", str(drafts), "--out", str(out), "--force"]) == 0


def test_golden_command_reports_undecided(tmp_path, capsys):
    drafts = tmp_path / "drafts.json"
    drafts.write_text(json.dumps({"drafts": [_draft(1, None)]}))
    assert cli.main(["golden", "--drafts", str(drafts), "--out", str(tmp_path / "g.json")]) == 1
    assert "without status" in capsys.readouterr().err


# --- Draft sampling / parsing (pure) --------------------------------------------------


def _chunk(cid, article_id, language="en", category_id=1, heading="Intro"):
    return drafting.SampledChunk(
        chunk_id=cid,
        article_id=article_id,
        category_id=category_id,
        language=language,
        heading_path=heading,
        content=f"Body of chunk {cid}. " * 40,
        title=f"Article {article_id}",
        title_es=f"Artículo {article_id}",
    )


CHUNKS = [
    _chunk(i * 10 + j, i, lang, category_id=i % 3)
    for i in range(1, 13)
    for j, lang in enumerate(("en", "es"))
]


def test_sample_is_deterministic_and_does_not_repeat_articles():
    counts = {"factual": 4, "cross-lingual": 2, "multi-article": 2, "unanswerable": 4}
    first = drafting.sample(CHUNKS, counts, seed=1)
    assert first.keys() == {"factual", "cross-lingual", "multi-article"}
    again = drafting.sample(CHUNKS, counts, seed=1)
    assert [c.chunk_id for c in first["factual"]] == [c.chunk_id for c in again["factual"]]
    single = [c.article_id for c in first["factual"] + first["cross-lingual"]]
    assert len(single) == len(set(single)) == 6
    assert [c.language for c in first["factual"]] == ["en", "en", "es", "es"]
    for a, b in first["multi-article"]:
        assert a.article_id != b.article_id and a.category_id == b.category_id
        assert a.language == b.language


def test_build_batches_and_cost_estimate():
    counts = {"factual": 2, "cross-lingual": 2, "multi-article": 2, "unanswerable": 4}
    sampled = drafting.sample(CHUNKS, counts, seed=3)
    batches = drafting.build_batches(sampled, ["Article 1", "Article 2"], counts)
    assert [b.type for b in batches] == [
        "factual",
        "cross-lingual",
        "multi-article",
        "unanswerable",
    ]
    assert "Article 2" in batches[-1].prompt
    est = drafting.estimate_cost(batches)
    assert est["calls"] == 4 and est["input_tokens_est"] > 0
    assert est["cost_usd_max"] > est["cost_usd_min"] > 0


def test_parse_batch_keeps_good_items_and_drops_malformed():
    a, b = _chunk(1, 1, "en"), _chunk(2, 2, "es")
    batch = drafting.DraftBatch("cross-lingual", "prompt", {"P1": a, "P2": b})
    items = [
        {"ref": "P1", "question": "¿Quién?", "answer": "Él."},
        {"ref": "P1", "question": "dup ref"},
        {"ref": "P9", "question": "unknown ref"},
        {"ref": "P2"},
        "not a dict",
        {"ref": "P2", "question": "Who?"},
    ]
    drafts, dropped = drafting.parse_batch(batch, items)
    assert dropped == 4
    assert drafts[0]["language"] == "es" and drafts[0]["search_languages"] == ["en"]
    assert drafts[0]["expected_article_ids"] == [1]
    assert drafts[0]["source"]["chunk_id"] == 1 and drafts[0]["source"]["excerpt"]
    assert drafts[1]["language"] == "en" and drafts[1]["search_languages"] == ["es"]

    pair = drafting.DraftBatch("multi-article", "p", {"M1": (a, _chunk(3, 3, "en"))})
    drafts, dropped = drafting.parse_batch(pair, [{"ref": "M1", "question": "Compare?"}])
    assert dropped == 0 and drafts[0]["expected_article_ids"] == [1, 3]
    assert len(drafts[0]["source"]) == 2

    una = drafting.DraftBatch("unanswerable", "p")
    drafts, dropped = drafting.parse_batch(
        una,
        [
            {"language": "es", "subtype": "off_topic", "question": "¿Capital de Mongolia?"},
            {"language": "fr", "subtype": "off_topic", "question": "?"},
            {"language": "en", "subtype": "other", "question": "?"},
        ],
    )
    assert dropped == 2 and drafts[0]["expected_article_ids"] == []


def test_build_document_is_curation_ready():
    a = _chunk(1, 1)
    batch = drafting.DraftBatch("factual", "p", {"P1": a})
    drafts, _ = drafting.parse_batch(batch, [{"ref": "P1", "question": "Q?", "answer": "A."}])
    una, _ = drafting.parse_batch(
        drafting.DraftBatch("unanswerable", "p"),
        [{"language": "en", "subtype": "off_topic", "question": "Off?"}],
    )
    doc = drafting.build_document(una + drafts, seed=1, sampled_chunks=5, stats={}, usage={})
    assert [d["id"] for d in doc["drafts"]] == ["d001", "d002"]
    assert [d["type"] for d in doc["drafts"]] == ["factual", "unanswerable"]
    assert all(d["status"] is None for d in doc["drafts"])
    assert doc["drafts"][1]["source"] is None
    # Marking them all keep converts cleanly.
    for d in doc["drafts"]:
        d["status"] = "keep"
    gold, report = drafts_to_golden(json.loads(drafting.dumps(doc)))
    assert report.kept == 2 and gold.questions[0].reference_answer == "A."


def test_results_markdown_records_the_curation_counts():
    """#653: the kept / edited / dropped counts travel into the results page."""
    from scripts.eval_rag.report import render_markdown

    meta = {
        "date": "2026-10-10",
        "golden_path": "eval/rag/golden.json",
        "golden_sha256": "0" * 64,
        "questions": 3,
        "answerable": 2,
        "unanswerable": 1,
        "k": 10,
        "candidates": 50,
        "rerank_model": "rerank-2.5",
        "rerank_top_n": 50,
        "command": "python -m scripts.eval_rag retrieval",
        "curation": {"drafts_file": "d.json", "drafted": 4, "kept": 2, "edited": 1, "dropped": 1},
    }
    empty = {"rows": [], "by_type": [], "abstain": []}
    assert "drafted 4, kept 2, edited 1, dropped 1" in render_markdown(meta, empty)
    assert "Curation" not in render_markdown({**meta, "curation": None}, empty)
