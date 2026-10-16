"""Reader-style golden questions (#693): schema, Opus draft/verify parsing, key-facts scoring.

No real model call: ``reader.call`` is exercised with ``llm.create_message``
faked, everything else is pure.
"""

import json
from types import SimpleNamespace

import pytest
from app.services import llm
from app.services.llm import HAIKU_MODEL
from scripts.eval_rag import answers, answers_report, cli, golden, reader
from scripts.eval_rag.golden import GoldenError, key_facts_found, load_golden, validate_questions

EN_31 = (
    "Intro paragraph about Darién.\n\n"
    "In 1510 Balboa hid aboard the ship of Martín Fernández de Enciso, bound for Urabá.\n\n"
    "Later, Santa María la Antigua was founded."
)
ES_31 = (
    "Introducción sobre el Darién.\n\n"
    "En 1510 Balboa se escondió en el barco de Martín Fernández de Enciso, rumbo a Urabá.\n\n"
    "Después se fundó Santa María la Antigua."
)


def _articles():
    return [
        reader.PublicArticle(31, "Vow and Victory", "Voto y victoria", EN_31, ES_31),
        reader.PublicArticle(
            32,
            "The Balboa Era",
            "La era de Balboa",
            "Balboa reached Urabá with Enciso in 1510.\n\nIn 1513 he crossed the isthmus.",
            "Balboa llegó a Urabá con Enciso en 1510.\n\nEn 1513 cruzó el istmo.",
        ),
        reader.PublicArticle(
            36, "The Path to the Pacific", None, "On 25 September 1513 he saw the South Sea.", ""
        ),
    ]


def _reader_q(**kw) -> dict:
    q = {
        "id": "r001",
        "language": "en",
        "question": "When did Balboa get to the mainland?",
        "type": "reader",
        "expected_article_ids": [31],
        "key_facts": ["1510", "Enciso"],
        "source": "opus",
        "subtype": "distractor",
    }
    q.update(kw)
    return q


# --- Schema -----------------------------------------------------------------------------


def test_reader_questions_validate_and_round_trip():
    (q,) = validate_questions([_reader_q()])
    assert q.type == "reader" and q.key_facts == ["1510", "Enciso"] and q.source == "opus"
    assert q.to_dict()["key_facts"] == ["1510", "Enciso"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"key_facts": None}, "needs key_facts"),
        ({"key_facts": []}, "needs key_facts"),
        ({"key_facts": ["1510", " "]}, "key_facts must be"),
        ({"expected_article_ids": []}, "needs at least one expected article"),
        ({"source": {"chunk_id": 1}}, "source must be a string"),
    ],
)
def test_invalid_reader_questions_are_rejected(change, message):
    with pytest.raises(GoldenError, match=message):
        validate_questions([_reader_q(**change)])


def test_key_facts_matching_ignores_case_accents_and_digit_groups():
    answer = "Balboa llegó en 1510 con ENCISO; unas 1.500 leguas. Núñez."
    assert key_facts_found(answer, ["1510", "Enciso", "1,500", "Nunez", "1513"]) == [
        "1510",
        "Enciso",
        "1,500",
        "Nunez",
    ]


def test_committed_golden_set_is_valid():
    gold = load_golden()
    readers = [q for q in gold.questions if q.type == "reader"]
    operator = [q for q in gold.questions if q.source == "operator"]
    if not readers:
        pytest.skip("golden set has no reader questions yet")
    assert {q.question for q in operator} >= {
        "When did Vasco Nunez de Balboa arrive to mainland Panama?",
        "When did Balboa discover the South Sea?",
        "¿Cuándo descubrió Balboa el Mar del Sur?",
        "When did Balboa discover the Pacific?",
        "¿Cuándo descubrió Balboa el Pacífico?",
    }
    assert all(q.key_facts for q in readers)
    assert gold.curation["reader"]["drafted"] >= gold.curation["reader"]["kept"]


# --- Drafting -----------------------------------------------------------------------------


def test_draft_batches_hold_whole_articles_and_every_title():
    arts = _articles()
    batches = reader.build_draft_batches(arts, arts)
    assert len(batches) == 1 and set(batches[0].refs) == {"31", "32", "36"}
    assert "Santa María la Antigua was founded" in batches[0].prompt  # whole article text
    assert "[36] The Path to the Pacific" in batches[0].prompt
    est = reader.estimate_cost(batches)
    assert est["calls"] == 1 and est["cost_usd_max"] > est["cost_usd_min"] > 0


def test_parse_draft_batch_drops_malformed_and_unsupported_items():
    arts = _articles()
    (batch,) = reader.build_draft_batches(arts, arts)
    good = {
        "article_id": 31,
        "language": "es",
        "kind": "when",
        "question": "¿Cuándo llegó Balboa a tierra firme?",
        "reference_answer": "En 1510, con Enciso.",
        "key_facts": ["1510", "Enciso"],
    }
    items = [
        good,
        "not an object",
        {**good, "article_id": 99},  # not in this batch
        {**good, "kind": "trivia"},
        {**good, "language": "fr"},
        {**good, "key_facts": []},
        {**good, "key_facts": ["1510", "1510"]},
        {**good, "reference_answer": ""},
        {**good, "key_facts": ["1492"]},  # not in the Spanish text of article 31
    ]
    drafts, dropped = reader.parse_draft_batch(batch, items)
    assert dropped == {"malformed": 7, "key_fact_not_in_article": 1}
    (d,) = drafts
    assert d["type"] == "reader" and d["source"] == "opus" and d["expected_article_ids"] == [31]


def test_parse_unanswerable_batch():
    drafts, dropped = reader.parse_unanswerable_batch(
        [{"language": "en", "question": "What happened in 1989?", "period": "1989"}, {"x": 1}]
    )
    assert dropped == {"malformed": 1}
    assert drafts[0]["type"] == "unanswerable" and drafts[0]["subtype"] == "uncovered_period"


# --- Keyword search and verification ------------------------------------------------------


def test_keyword_candidates_need_every_key_fact():
    arts = _articles()
    found = reader.keyword_candidates(_reader_q(), arts, exclude={31})
    assert [a.id for a in found] == [32]
    excerpt = reader.excerpts(arts[0], _reader_q(), 1)
    assert excerpt == [
        "In 1510 Balboa hid aboard the ship of Martín Fernández de Enciso, bound for Urabá."
    ]


def _verify_batch():
    arts = _articles()
    drafts = [
        {**_reader_q(), "reference_answer": "In 1510, with Enciso."},
        {**_reader_q(question="Who did Balboa sail with?"), "reference_answer": "Enciso."},
        {**_reader_q(question="Odd question?"), "reference_answer": "x"},
    ]
    (batch,) = reader.build_verify_batches(drafts, arts)
    return batch, {a.id: a for a in arts}


def test_verify_batch_shows_draft_and_keyword_articles():
    batch, _ = _verify_batch()
    assert set(batch.refs) == {"R1", "R2", "R3"}
    assert batch.refs["R1"][1] == [31, 32]
    assert '<article id="32"' in batch.prompt


def test_apply_verdicts_keep_fix_drop_and_malformed():
    batch, by_id = _verify_batch()
    items = [
        {  # keep with reworded answer: stays a keep, draft answer kept
            "ref": "R1",
            "verdict": "keep",
            "expected_article_ids": [31],
            "key_facts": ["1510", "Enciso"],
            "reference_answer": "Reworded.",
        },
        {  # fix: completes the ids
            "ref": "R2",
            "verdict": "fix",
            "expected_article_ids": [31, 32],
            "key_facts": ["Enciso"],
            "reference_answer": "With Enciso, in 1510.",
            "reason": "32 also states it",
        },
        {"ref": "R3", "verdict": "drop", "reason": "ambiguous"},
        {"ref": "R9", "verdict": "keep"},  # unknown ref
    ]
    decided, counts = reader.apply_verdicts(batch, items, by_id)
    r1, r2, r3 = decided
    assert r1["verdict"] == "keep" and r1["reference_answer"] == "In 1510, with Enciso."
    assert r2["verdict"] == "fix" and r2["expected_article_ids"] == [31, 32]
    assert r2["draft"]["expected_article_ids"] == [31] and r2["key_facts"] == ["Enciso"]
    assert r3["verdict"] == "drop" and r3["verify_reason"] == "ambiguous"
    assert counts == {"verify_malformed": 1}


@pytest.mark.parametrize(
    "item",
    [
        {"verdict": "keep", "expected_article_ids": [36], "key_facts": ["1510"]},  # not shown
        {"verdict": "keep", "expected_article_ids": [31], "key_facts": ["Pizarro"]},  # absent
        {"verdict": "maybe", "expected_article_ids": [31], "key_facts": ["1510"]},
        {"verdict": "fix", "expected_article_ids": [], "key_facts": ["1510"]},
        {"verdict": "fix", "expected_article_ids": [31], "key_facts": "1510"},
    ],
)
def test_malformed_verdicts_drop_the_item_never_repair(item):
    batch, by_id = _verify_batch()
    decided, counts = reader.apply_verdicts(
        batch, [{"ref": "R1", "reference_answer": "x", **item}], by_id
    )
    assert decided[0]["verdict"] == "malformed"
    assert counts["verify_malformed"] == 1 and counts["unverified"] == 2


def test_build_document_keeps_operator_items_and_maps_statuses():
    seeds = reader.seed_drafts()
    items = [
        {**seeds[0], "verdict": "drop", "verify_reason": "no"},
        {**seeds[1], "verdict": "keep"},
        {**_reader_q(), "reference_answer": "a", "verdict": "fix", "draft": {"x": 1}},
        {**_reader_q(question="Bad?"), "reference_answer": "a", "verdict": "malformed"},
        {
            "language": "es",
            "question": "¿Qué pasó en 1989?",
            "type": "unanswerable",
            "subtype": "uncovered_period",
            "expected_article_ids": [],
            "source": "opus",
            "verdict": "keep",
        },
    ]
    doc = reader.build_document(items, stats={"drafted_reader": 2}, spend=reader.Spend(), seed=1)
    by_id = {d["id"]: d for d in doc["drafts"]}
    assert by_id["op01"]["status"] == "keep" and "verifier said drop" in by_id["op01"]["notes"]
    assert by_id["r001"]["status"] == "edit" and by_id["r002"]["status"] == "drop"
    assert by_id["ru001"]["status"] == "keep"
    assert doc["summary"]["fixed"] == 1 and doc["summary"]["dropped"] == 1
    gold, counts = golden.drafts_to_golden(json.loads(reader.dumps(doc)))
    assert counts.by_type == {"reader": 3, "unanswerable": 1}
    assert {q.source for q in gold.questions} == {"operator", "opus"}


def test_call_logs_usage_under_its_feature(monkeypatch):
    seen = {}

    def fake_create_message(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"questions": []}')],
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=200,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    monkeypatch.setattr(llm, "create_message", fake_create_message)
    spend = reader.Spend()
    arts = _articles()
    assert reader.call(reader.build_draft_batches(arts, arts)[0], spend, "questions") == []
    assert seen["feature"] == reader.DRAFT_FEATURE and seen["model"] == llm.OPUS_MODEL
    assert seen["output_config"] == {"effort": reader.EFFORT}
    # #716: the shared title listing + rules ride in a cache-marked first block,
    # the batch's own articles in an unmarked second one; together the same text.
    batch = reader.build_draft_batches(arts, arts)[0]
    head, tail = seen["messages"][0]["content"]
    assert head == llm.cached_text(batch.prefix)
    assert "[36] The Path to the Pacific" in head["text"]
    assert "cache_control" not in tail and '<article id="31"' in tail["text"]
    assert head["text"] + tail["text"] == batch.prompt
    out = spend.as_dict()
    assert out[reader.DRAFT_FEATURE]["input_tokens"] == 1000 and out["total_cost_usd"] > 0


def test_run_survives_api_errors_and_keeps_finished_drafts(monkeypatch):
    """#708: an Anthropic API error on one batch is counted and skipped; the
    run returns everything else instead of ending."""
    import anthropic
    import httpx

    calls = []

    def fake_call(batch, spend, required_key):
        calls.append(batch.kind)
        if len(calls) == 1:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
        if batch.kind == "verify" and calls.count("verify") == 1:
            raise anthropic.InternalServerError(
                "overloaded",
                response=httpx.Response(529, request=httpx.Request("POST", "https://x")),
                body=None,
            )
        return []

    monkeypatch.setattr(reader, "call", fake_call)
    arts = _articles()
    decided, stats, _spend = reader.run(arts, drafted_from=arts, n_unanswerable=0)
    assert stats["api_errors"] == 2 and stats["failed_calls"] == 2
    assert len(calls) == stats["draft_calls"] + stats["verify_calls"]
    # The operator seeds still come back (unverified), so nothing finished is lost.
    assert {d["id"] for d in decided} >= {s["id"] for s in reader.seed_drafts()}


def test_key_facts_match_on_number_and_word_boundaries():
    """#708: a number never matches inside a longer number, and a plain space
    is not a thousands separator; accents, case and real separators still fold."""
    assert key_facts_found("Founded in 1525.", ["25", "1525"]) == ["1525"]
    assert key_facts_found("25,000 men", ["25", "25000"]) == ["25000"]
    assert key_facts_found("In 1,513 and 1.513", ["1513"]) == ["1513"]
    assert key_facts_found("year 1513", ["1,513"]) == ["1,513"]
    assert key_facts_found("in 1513 500 men", ["1513500", "500", "1513"]) == ["500", "1513"]
    assert key_facts_found("unas 1 500 leguas", ["1500"]) == ["1500"]
    assert key_facts_found("Peruvian gold", ["Peru"]) == []
    # A digit edge only refuses another digit: ordinals and decades still match.
    assert key_facts_found("September 25th, 1513", ["25", "1513"]) == ["25", "1513"]
    assert key_facts_found("in the 1500s", ["1500"]) == ["1500"]
    assert key_facts_found("el 1º de mayo", ["1"]) == ["1"]
    assert key_facts_found("in 1525 with 25,000", ["25"]) == []
    assert key_facts_found("(25) ships; Pérez de Robles.", ["25", "Perez de Robles"]) == [
        "25",
        "Perez de Robles",
    ]


# --- golden --append ------------------------------------------------------------------------


def test_golden_append_merges_and_records_curation(tmp_path, capsys):
    base = {
        "version": 1,
        "curation": {"drafted": 1, "kept": 1},
        "questions": [
            {
                "id": "d001",
                "language": "en",
                "question": "Q?",
                "type": "factual",
                "expected_article_ids": [1],
            }
        ],
    }
    out = tmp_path / "golden.json"
    out.write_text(json.dumps(base))
    seeds = reader.seed_drafts()
    doc = reader.build_document(
        [{**s, "verdict": "keep"} for s in seeds], stats={}, spend=reader.Spend(), seed=1
    )
    drafts = tmp_path / "reader.json"
    drafts.write_text(reader.dumps(doc))
    md = tmp_path / "curation.md"
    argv = ["golden", "--drafts", str(drafts), "--out", str(out), "--append"]
    assert cli.main([*argv, "--curation-md", str(md)]) == 0
    text = md.read_text()
    assert "## Fixed" in text and "## Dropped" in text and "| op01 |" in text
    merged = load_golden(out)
    assert len(merged.questions) == 1 + len(seeds)
    assert merged.curation["drafted"] == 1  # the #653 block is untouched
    assert merged.curation["reader"]["questions_added"] == len(seeds)
    # A second append clashes on ids.
    assert cli.main(["golden", "--drafts", str(drafts), "--out", str(out), "--append"]) == 1
    assert "ids already in" in capsys.readouterr().err


# --- Answer scoring ---------------------------------------------------------------------------


def _record(**kw) -> dict:
    base = {
        "suite": "golden",
        "id": "r001",
        "model": HAIKU_MODEL,
        "question": "q?",
        "language": "en",
        "stage": "answered",
        "error": None,
        "answered": True,
        "answer": "He arrived in 1510 with Enciso.",
        "cited_article_ids": [31],
        "cited_passage_ids": ["P1"],
        "passage_article_ids": [36, 31, 31, 50],
        "top_rerank": 0.9,
        "top_similarity": 0.6,
        "degraded": [],
        "retrieval_ms": 1,
        "llm_ms": 1,
        "total_ms": 2,
        "usage": {"output_tokens": 10},
        "cost_usd": 0.001,
        "judge": {
            "faithful": True,
            "unsupported": [],
            "answer_language": "en",
            "complied_with_injection": False,
            "reason": "",
        },
        "judge_error": None,
        "judge_cost_usd": 0.0,
    }
    base.update(kw)
    return base


def _score(record, expected=(31, 32), facts=("1510", "Enciso"), qtype="reader"):
    return answers.score_golden(
        record, list(expected), qtype != "unanswerable", qtype, key_facts=list(facts) or None
    )


def test_score_golden_passage_recall_and_key_facts():
    r = _score(_record())
    assert r["passage_recall"] == 0.5 and r["passage_hit"] is True
    assert r["key_facts_found"] == ["1510", "Enciso"] and r["key_facts_hit"] is True
    partial = _score(_record(answer="He arrived in 1510."))
    assert partial["key_facts_found"] == ["1510"] and partial["key_facts_hit"] is False
    missed = _score(_record(stage="malformed", answered=False, answer="", judge=None))
    assert missed["key_facts_hit"] is False and missed["passage_recall"] == 0.5
    no_facts = _score(_record(), facts=())
    assert no_facts["key_facts_hit"] is None
    old = _score({k: v for k, v in _record().items() if k != "passage_article_ids"})
    assert old["passage_recall"] is None  # a results file from before #693


def test_summary_reports_outcome_shares_and_per_type_slices():
    records = [
        _score(_record(id="a")),
        _score(_record(id="b", stage="malformed", answered=False, answer="", judge=None)),
        _score(_record(id="c", stage="not_covered", answered=False, answer="", judge=None)),
        _score(
            _record(id="d", stage="abstained", answered=False, answer="", judge=None),
            expected=(),
            facts=(),
            qtype="unanswerable",
        ),
    ]
    s = answers.summarize(records, [HAIKU_MODEL])[HAIKU_MODEL]
    assert s["malformed_share"] == 0.25 and s["not_covered_share"] == 0.25
    assert s["answered_share"] == 0.25 and s["key_facts_hit_n"] == "1/3"
    assert s["key_facts_hit_answered"] == 1.0 and s["passage_recall"] == 0.5
    assert s["by_type"]["reader"]["n"] == 3 and s["by_type"]["unanswerable"]["n"] == 1
    assert s["answerable_answered"] == pytest.approx(1 / 3)  # the #637 key is unchanged
    md = answers_report._reader_section({HAIKU_MODEL: s}, [HAIKU_MODEL])
    assert any("passage recall" in line for line in md)
    assert any(line.startswith("| reader | 3 |") for line in md)
