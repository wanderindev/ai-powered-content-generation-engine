"""Pipeline-memory eval (#632): metrics, seed schema + parsing, run end-to-end, read-only guards.

Voyage and Sonnet are faked with deterministic vectors / rule-based judges;
nothing here calls a real API.
"""

import hashlib
import json
import math
import re
from types import SimpleNamespace

import pytest
from app.core.config import get_settings
from app.models.knowledge_chunk import KnowledgeChunk
from app.services import consistency_check as cc
from app.services import embeddings, llm
from scripts.eval_rag import cli, memory_run, memory_seed, metrics
from sqlalchemy import event, insert

DIMS = 1024


def _unit(sim: float, axis: int, spare: int = 500) -> list[float]:
    """A unit vector with cosine similarity ``sim`` to the pure ``axis`` vector."""
    v = [0.0] * DIMS
    v[axis] = sim
    v[spare] = math.sqrt(max(0.0, 1 - sim * sim))
    return v


def _axis(axis: int) -> list[float]:
    return _unit(1.0, axis)


# --- Metrics --------------------------------------------------------------------------


def test_confusion_and_prf():
    counts = metrics.confusion([True, True, False, False, True], [True, False, True, False, True])
    assert counts == {"tp": 2, "fp": 1, "fn": 1, "tn": 1}
    prf = metrics.precision_recall_f1(counts)
    assert prf["precision"] == pytest.approx(2 / 3)
    assert prf["recall"] == pytest.approx(2 / 3)
    assert prf["f1"] == pytest.approx(2 / 3)


def test_prf_edge_cases():
    assert metrics.precision_recall_f1({"tp": 0, "fp": 0, "fn": 2, "tn": 1}) == {
        "precision": None,
        "recall": 0.0,
        "f1": None,
    }
    assert metrics.precision_recall_f1({"tp": 0, "fp": 1, "fn": 1, "tn": 0})["f1"] == 0.0


def test_threshold_grid_is_inclusive_and_rounded():
    grid = metrics.threshold_grid(0.70, 0.95, 0.025)
    assert grid[0] == 0.7 and grid[-1] == 0.95 and len(grid) == 11
    assert 0.825 in grid
    with pytest.raises(ValueError):
        metrics.threshold_grid(0, 1, 0)


def test_threshold_sweep_and_selection_rule():
    scores = [0.92, 0.81, 0.75, None]
    expected = [True, True, False, False]
    rows = metrics.threshold_sweep(scores, expected, [0.7, 0.775, 0.8, 0.85])
    by_t = {r["threshold"]: r for r in rows}
    assert by_t[0.7]["fp"] == 1 and by_t[0.7]["precision"] == pytest.approx(2 / 3)
    assert by_t[0.775]["f1"] == 1.0 and by_t[0.8]["f1"] == 1.0
    assert by_t[0.85]["recall"] == 0.5
    picked = metrics.select_threshold(rows, min_precision=0.9)
    # Tie on F1 = 1.0 goes to the higher threshold.
    assert picked["met_precision"] and picked["row"]["threshold"] == 0.8


def test_selection_falls_back_when_no_row_meets_precision():
    rows = metrics.threshold_sweep([0.9, 0.9], [True, False], [0.5, 0.95])
    picked = metrics.select_threshold(rows, min_precision=0.9)
    assert picked["met_precision"] is False and picked["row"]["threshold"] == 0.5


# --- Seed schema ------------------------------------------------------------------------


def _contra(
    iid,
    type_,
    text,
    *,
    language="en",
    claim_id=9001,
    chunk_id=1,
    article_id=1,
    perturbation="date",
    original="The canal opened in 1914 after a decade of work.",
):
    return {
        "id": iid,
        "type": type_,
        "language": language,
        "text": text,
        "perturbation": perturbation if type_ == "perturbed" else None,
        "source_article_id": article_id,
        "source_article_title": "The Canal",
        "source_claim_id": claim_id,
        "source_chunk_id": chunk_id,
        "original_text": original,
        "expected_label": memory_seed.EXPECTED[type_],
        "review": None,
    }


def _dup(iid, type_, title, *, language="en", article_id=None, review=None):
    item = {
        "id": iid,
        "type": type_,
        "language": language,
        "title": title,
        "main_topic": f"About {title}.",
        "text": f"{title}\n\nAbout {title}.",
        "source_article_id": article_id,
        "original_text": "The Canal" if article_id else None,
        "expected_label": memory_seed.EXPECTED[type_],
        "review": review if review is not None else ("pending" if type_ == "novel" else None),
    }
    return item


def _doc(contradictions, duplicates):
    return {"version": 1, "contradictions": contradictions, "duplicates": duplicates}


def test_validate_seed_accepts_a_valid_document():
    doc = _doc(
        [_contra("c001", "perturbed", "The canal opened in 1915.")],
        [
            _dup("d001", "near_duplicate", "Digging the big ditch", article_id=1),
            _dup("d002", "novel", "Kuna revolution"),
        ],
    )
    assert memory_seed.validate_seed(doc) == []


def test_validate_seed_reports_every_problem():
    bad_contra = _contra("c001", "perturbed", "", perturbation="color")
    bad_contra["expected_label"] = "CONSISTENT"
    bad_contra["source_claim_id"] = "x"
    novel_with_source = _dup("c001", "novel", "Topic", article_id=3)
    novel_with_source["review"] = "maybe"
    errors = memory_seed.validate_seed(
        {
            "version": 2,
            "contradictions": [bad_contra, "nope"],
            "duplicates": [novel_with_source, {"id": "d9", "type": "other"}],
        }
    )
    text = " ".join(errors)
    for fragment in (
        "version must be 1",
        "text is empty",
        "expected_label must be CONTRADICTS",
        "source_claim_id must be an integer",
        "perturbation must be one of",
        "an item is not an object",
        "duplicate id",
        "a novel topic has no source article",
        "review must be",
        "type must be one of",
    ):
        assert fragment in text, fragment
    assert memory_seed.validate_seed([]) == ["the seed file is not a JSON object"]


def test_load_seed_raises_on_bad_json(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    with pytest.raises(memory_seed.SeedError):
        memory_seed.load_seed(path)


def test_active_drops_reviewed_out_items():
    rows = [
        _dup("d1", "novel", "A", review="drop"),
        _dup("d2", "novel", "B", review="keep"),
        _dup("d3", "novel", "C"),
    ]
    assert [r["id"] for r in memory_seed.active(rows)] == ["d2", "d3"]


# --- Seed parsing -----------------------------------------------------------------------


def _claim(i, text="The canal opened to shipping in August 1914."):
    return memory_seed.SourceClaim(
        chunk_id=100 + i, claim_id=900 + i, article_id=i, text=text, title=f"Article {i}"
    )


def _article(i):
    return memory_seed.SourceArticle(i, f"Title {i}", f"Título {i}", f"Excerpt {i}", None)


def test_split_and_build_batches_balance_languages():
    assert memory_seed.split_languages(15) == {"en": 8, "es": 7}
    sampled = {
        "perturbed": [_claim(i) for i in range(4)],
        "paraphrase": [_claim(i) for i in range(4, 7)],
        "near_duplicate": [_article(i) for i in range(3)],
    }
    counts = {"perturbed": 4, "paraphrase": 3, "near_duplicate": 3, "novel": 5}
    batches = memory_seed.build_batches(sampled, ["The Canal"], counts)
    shape = [(b.type, b.language, b.count) for b in batches]
    assert shape == [
        ("perturbed", "en", 2),
        ("perturbed", "es", 2),
        ("paraphrase", "en", 2),
        ("paraphrase", "es", 1),
        ("near_duplicate", "en", 2),
        ("near_duplicate", "es", 1),
        ("novel", "en", 3),
        ("novel", "es", 2),
    ]
    es_dup = next(b for b in batches if b.type == "near_duplicate" and b.language == "es")
    assert "Título 2" in es_dup.prompt and "Spanish" in es_dup.prompt
    est = memory_seed.estimate_cost(batches, 5)
    assert est["calls"] == 8 and est["cost_usd_max"] > est["cost_usd_min"] > 0


def test_sample_prefers_distinct_articles():
    claims = [
        _claim(1),
        _claim(1, "Another canal fact from 1914, long enough."),
        _claim(2),
        _claim(3),
    ]
    sampled = memory_seed.sample(
        claims,
        [_article(1)],
        {"perturbed": 2, "paraphrase": 1, "near_duplicate": 1, "novel": 0},
        seed=1,
    )
    picked = sampled["perturbed"] + sampled["paraphrase"]
    assert len({c.article_id for c in picked}) == 3


def test_parse_perturbed_drops_malformed_items():
    refs = {"c1": _claim(1), "c2": _claim(2), "c3": _claim(3)}
    batch = memory_seed.SeedBatch("perturbed", "en", "p", refs=refs, count=3)
    items = [
        {
            "ref": "c1",
            "perturbation": "date",
            "text": "The canal opened to shipping in August 1915.",
        },
        {"ref": "c1", "perturbation": "date", "text": "repeat ref"},
        {"ref": "c2", "perturbation": "colour", "text": "bad kind"},
        {
            "ref": "c3",
            "perturbation": "date",
            "text": "The canal opened to  shipping in August 1914.",
        },  # unchanged
        {"ref": "c9", "perturbation": "date", "text": "unknown ref"},
        "not a dict",
        {"ref": "c2", "perturbation": "figure"},
    ]
    parsed, dropped = memory_seed.parse_batch(batch, items)
    assert dropped == 6
    assert len(parsed) == 1
    item = parsed[0]
    assert item["expected_label"] == "CONTRADICTS" and item["source_claim_id"] == 901
    assert item["original_text"] == _claim(1).text and item["source_chunk_id"] == 101


def test_parse_duplicates_and_novel():
    batch = memory_seed.SeedBatch("near_duplicate", "es", "p", refs={"a1": _article(1)}, count=1)
    parsed, dropped = memory_seed.parse_batch(
        batch,
        [
            {"ref": "a1", "title": "Excavando", "main_topic": "La obra."},
            {"ref": "a1", "title": "x"},
        ],
    )
    assert (
        dropped == 1
        and parsed[0]["source_article_id"] == 1
        and parsed[0]["text"] == "Excavando\n\nLa obra."
    )
    novel = memory_seed.SeedBatch("novel", "en", "p", count=2)
    parsed, dropped = memory_seed.parse_batch(
        novel,
        [
            {"title": "Kuna revolution", "main_topic": "1925."},
            {"title": "kuna  revolution", "main_topic": "dup"},
            {"title": "Coffee in Boquete", "main_topic": "Farms."},
            {"title": "Over the cap", "main_topic": "x"},
        ],
    )
    assert [p["title"] for p in parsed] == ["Kuna revolution", "Coffee in Boquete"]
    assert dropped == 2 and all(
        p["review"] == "pending" and p["source_article_id"] is None for p in parsed
    )
    assert memory_seed.parse_batch(novel, {"items": "not a list"}) == ([], 0)


# --- Database fixtures ------------------------------------------------------------------


def _chunk(db, *, source_type, source_id, article_id, content, vector, meta=None):
    return db.execute(
        insert(KnowledgeChunk.__table__)
        .values(
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
            meta=meta,
        )
        .returning(KnowledgeChunk.__table__.c.id)
    ).scalar_one()


CANAL_FACT = "The canal opened to shipping in August 1914."
RAIL_FACT = "The Panama Railroad crossed the isthmus in 1855."
LENGTH_FACT = "The canal is about eighty kilometres long."


@pytest.fixture
def corpus(test_db, article_factory):
    canal = article_factory(title="The Canal", title_es="El Canal")
    rail = article_factory(title="The Railroad", title_es="El Ferrocarril")
    ids = {}
    ids["canal_body"] = _chunk(
        test_db,
        source_type="ARTICLE",
        source_id=canal.id,
        article_id=canal.id,
        content="Canal body text.",
        vector=_axis(10),
    )
    ids["rail_body"] = _chunk(
        test_db,
        source_type="ARTICLE",
        source_id=rail.id,
        article_id=rail.id,
        content="Railroad body text.",
        vector=_axis(11),
    )
    fact = {"language": "en", "verdict": "VERIFIED"}
    ids["canal_fact"] = _chunk(
        test_db,
        source_type="ARTICLE_CLAIM",
        source_id=9001,
        article_id=canal.id,
        content=CANAL_FACT,
        vector=_axis(0),
        meta=fact,
    )
    ids["rail_fact"] = _chunk(
        test_db,
        source_type="ARTICLE_CLAIM",
        source_id=9002,
        article_id=rail.id,
        content=RAIL_FACT,
        vector=_axis(1),
        meta=fact,
    )
    ids["length_fact"] = _chunk(
        test_db,
        source_type="ARTICLE_CLAIM",
        source_id=9003,
        article_id=canal.id,
        content=LENGTH_FACT,
        vector=_axis(2),
        meta=fact,
    )
    test_db.commit()
    return SimpleNamespace(canal=canal, rail=rail, ids=ids)


class Judge:
    """The B4 judge: CONTRADICTS when the claim's year differs from the reference's."""

    def __init__(self):
        self.features = []

    def __call__(self, **kwargs):
        self.features.append(kwargs["feature"])
        prompt = kwargs["messages"][0]["content"]
        lines = []
        for number, claim, ref in re.findall(
            r"^(\d+)\. CLAIM: (.*)\n   REFERENCE: (.*)$", prompt, re.M
        ):
            years_c, years_r = (
                set(re.findall(r"\b1\d{3}\b", claim)),
                set(re.findall(r"\b1\d{3}\b", ref)),
            )
            if years_c and years_r and years_c.isdisjoint(years_r):
                label = "CONTRADICTS"
            elif years_c and years_c == years_r:
                label = "CONSISTENT"
            else:
                label = "UNRELATED"
            lines.append(f"PAIR: {number} | {label} | year check")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="\n".join(lines))],
            usage=SimpleNamespace(input_tokens=400, output_tokens=40),
        )


@pytest.fixture
def env(test_engine, monkeypatch):
    import app.core.database as database

    settings = get_settings()
    monkeypatch.setattr(settings, "voyage_api_key", "fake-key")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key")
    monkeypatch.setattr(settings, "consistency_top_k", 3)
    monkeypatch.setattr(settings, "consistency_min_similarity", 0.5)
    monkeypatch.setattr(settings, "consistency_batch_pairs", 20)
    monkeypatch.setattr(settings, "suggestion_dedup_min_similarity", 0.85)
    monkeypatch.setattr(database, "engine", test_engine)
    vectors: dict[str, list[float]] = {}
    calls = {"documents": [], "queries": []}

    def fake_docs(texts, *, feature="unlabeled", model=None):
        calls["documents"].append((list(texts), feature))
        return [vectors.get(t, _axis(20)) for t in texts]

    def fake_queries(texts, *, feature="unlabeled", model=None):
        calls["queries"].append((list(texts), feature))
        return [vectors.get(t, _axis(20)) for t in texts]

    monkeypatch.setattr(embeddings, "embed_documents", fake_docs)
    monkeypatch.setattr(embeddings, "embed_queries", fake_queries)
    monkeypatch.setattr(
        embeddings, "embed_query", lambda *a, **k: pytest.fail("no single-query embeds")
    )
    judge = Judge()
    monkeypatch.setattr(cc, "create_message", judge)
    return SimpleNamespace(vectors=vectors, calls=calls, judge=judge)


@pytest.fixture
def seed_file(corpus, env, tmp_path):
    canal, rail, ids = corpus.canal, corpus.rail, corpus.ids
    contradictions = [
        _contra(
            "c001",
            "perturbed",
            "The canal opened to shipping in August 1915.",
            claim_id=9001,
            chunk_id=ids["canal_fact"],
            article_id=canal.id,
            original=CANAL_FACT,
        ),
        _contra(
            "c002",
            "perturbed",
            "El Ferrocarril de Panamá cruzó el istmo en 1860.",
            language="es",
            claim_id=9002,
            chunk_id=ids["rail_fact"],
            article_id=rail.id,
            original=RAIL_FACT,
        ),
        _contra(
            "c003",
            "paraphrase",
            "In August 1914 the canal opened to shipping.",
            claim_id=9001,
            chunk_id=ids["canal_fact"],
            article_id=canal.id,
            original=CANAL_FACT,
        ),
        _contra(
            "c004",
            "perturbed",
            "The canal is about ninety kilometres long.",
            claim_id=9003,
            chunk_id=ids["length_fact"],
            article_id=canal.id,
            perturbation="figure",
            original=LENGTH_FACT,
        ),
        _contra(
            "c005",
            "paraphrase",
            "Dropped by review, never run.",
            claim_id=9003,
            chunk_id=ids["length_fact"],
            article_id=canal.id,
            original=LENGTH_FACT,
        ),
    ]
    contradictions[-1]["review"] = "drop"
    env.vectors.update(
        {
            contradictions[0]["text"]: _unit(0.9, 0),
            contradictions[1]["text"]: _unit(0.62, 1, spare=501),
            contradictions[2]["text"]: _unit(0.95, 0, spare=502),
            # Far from its source: never retrieved, so a miss (FN) at every floor.
            contradictions[3]["text"]: _unit(0.1, 2, spare=503),
        }
    )
    duplicates = [
        _dup("d001", "near_duplicate", "Digging the big ditch", article_id=canal.id),
        _dup(
            "d002", "near_duplicate", "Rieles a través del istmo", language="es", article_id=rail.id
        ),
        _dup("d003", "novel", "Canal Zone baseball leagues", review="keep"),
        _dup("d004", "novel", "Cosecha de café en Boquete", language="es"),
    ]
    env.vectors.update(
        {
            duplicates[0]["text"]: _unit(0.92, 10),
            duplicates[1]["text"]: _unit(0.81, 11, spare=504),
            duplicates[2]["text"]: _unit(0.75, 10, spare=505),
            duplicates[3]["text"]: _axis(20),
        }
    )
    path = tmp_path / "memory_seed.json"
    path.write_text(json.dumps(_doc(contradictions, duplicates)))
    return path


READ_ONLY_PREFIXES = ("SELECT", "SHOW", "WITH")
WRITE_WORDS = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|GRANT)\b")


@pytest.fixture
def statements(test_engine):
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(test_engine, "before_cursor_execute", record)
    yield seen
    event.remove(test_engine, "before_cursor_execute", record)


# --- memory run -------------------------------------------------------------------------


def test_memory_run_end_to_end_read_only(seed_file, env, statements, tmp_path, capsys):
    out_dir = tmp_path / "results"
    code = cli.main(
        [
            "memory",
            "run",
            "--seed",
            str(seed_file),
            "--out-dir",
            str(out_dir),
            "--date",
            "2026-10-10",
            "--read-only-prod",
        ]
    )
    assert code == 0, capsys.readouterr().err
    payload = json.loads((out_dir / "2026-10-10-memory.json").read_text())
    md = (out_dir / "2026-10-10-memory.md").read_text()

    b4 = payload["b4"]
    assert b4["items"] == 4  # the review-dropped item is out
    cfg = b4["configured_row"]
    assert (cfg["tp"], cfg["fp"], cfg["fn"], cfg["tn"]) == (2, 0, 1, 1)
    assert cfg["precision"] == 1.0 and cfg["recall"] == pytest.approx(2 / 3)
    # c002's source sits at 0.62: in up to 0.6, out at 0.65; ties go to the higher floor.
    assert b4["chosen_floor"] == 0.6
    by_floor = {r["threshold"]: r for r in b4["sweep"]}
    assert by_floor[0.65]["tp"] == 1 and by_floor[0.3]["source_hit_rate"] == 0.75
    c004 = next(r for r in b4["records"] if r["id"] == "c004")
    assert c004["source_similarity"] == pytest.approx(0.1, abs=1e-4)
    assert b4["judge_source_pair_accuracy"] == 1.0
    assert b4["judge_cost_usd"] > 0 and set(env.judge.features) == {memory_run.JUDGE_FEATURE}
    # The seeded claim met its own source: the source article was not excluded.
    c001 = next(r for r in b4["records"] if r["id"] == "c001")
    assert any(p["is_source"] and p["label"] == "CONTRADICTS" for p in c001["pairs"])

    b3 = payload["b3"]
    assert b3["chosen_threshold"] == 0.8
    cur = b3["current_default"]
    assert cur["threshold"] == 0.85 and cur["tp"] == 1 and cur["recall"] == 0.5
    assert b3["source_match_rate"] == 1.0 and b3["novel_unreviewed"] == 1

    assert payload["recommendation"]["b3"]["switch_on"] is True
    assert payload["recommendation"]["b4"]["switch_on"] is True
    assert "## Recommendation" in md and "## Summary" in md and "| 0.8 |" in md
    assert [f for _, f in env.calls["documents"]] == [memory_run.EMBED_FEATURE]
    assert [f for _, f in env.calls["queries"]] == [memory_run.EMBED_FEATURE]

    assert statements, "the run issued no SQL at all"
    for sql in statements:
        head = sql.lstrip().upper()
        assert head.startswith(READ_ONLY_PREFIXES), sql
        assert not WRITE_WORDS.search(head), sql


def test_memory_run_dry_run_makes_no_model_call(seed_file, env, tmp_path, capsys):
    out_dir = tmp_path / "results"
    assert (
        cli.main(
            ["memory", "run", "--seed", str(seed_file), "--out-dir", str(out_dir), "--dry-run"]
        )
        == 0
    )
    assert "Dry run" in capsys.readouterr().out
    assert not out_dir.exists() and env.judge.features == [] and env.calls["queries"] == []


def test_memory_run_rejects_an_invalid_seed(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": 1, "contradictions": [{"id": "c1"}], "duplicates": []}))
    assert cli.main(["memory", "run", "--seed", str(path)]) == 1
    assert "type must be one of" in capsys.readouterr().err


def test_recommendation_keeps_off_when_bars_are_missed():
    b3 = {
        "selection": {
            "row": {"threshold": 0.7, "precision": 0.5, "recall": 1.0},
            "met_precision": False,
        }
    }
    rec = memory_run.recommend(b3, None)
    assert rec["b3"]["switch_on"] is False and rec["b3"]["text"].startswith("Keep off")


# --- memory seed ------------------------------------------------------------------------


class SeedModel:
    """Fake Sonnet for the seed drafts: one well-formed item per ref, plus junk."""

    def __init__(self):
        self.features = []

    def __call__(self, **kwargs):
        feature = kwargs["feature"]
        self.features.append(feature)
        prompt = kwargs["messages"][0]["content"]
        if feature == memory_seed.FEATURES["novel"]:
            n = int(re.search(r"Propose (\d+)", prompt).group(1))
            items = [
                {"title": f"Novel topic {feature[-3:]} {i}", "main_topic": "Never covered."}
                for i in range(n)
            ]
        elif feature == memory_seed.FEATURES["near_duplicate"]:
            items = [
                {"ref": r, "title": f"Pitch {r}", "main_topic": "Same subject."}
                for r in re.findall(r"^(a\d+): TITLE", prompt, re.M)
            ]
        else:
            items = []
            for ref, text in re.findall(r"^(c\d+): (.*)$", prompt, re.M):
                changed = f"Altered: {text}" if feature.endswith("perturb") else f"Reworded: {text}"
                items.append({"ref": ref, "perturbation": "date", "text": changed})
        items.append({"ref": "zzz", "text": "junk"})
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({"items": items}))], usage=None
        )


def test_memory_seed_end_to_end(corpus, env, statements, monkeypatch, tmp_path, capsys):
    model = SeedModel()
    monkeypatch.setattr(llm, "create_message", model)
    out = tmp_path / "seed.json"
    argv = [
        "memory",
        "seed",
        "--out",
        str(out),
        "--perturbed",
        "2",
        "--paraphrase",
        "1",
        "--near-duplicate",
        "2",
        "--novel",
        "2",
        "--read-only-prod",
    ]
    assert cli.main(argv) == 0, capsys.readouterr().err
    doc = memory_seed.load_seed(out)  # validates
    types = sorted((i["type"], i["language"]) for i in doc["contradictions"] + doc["duplicates"])
    assert types == sorted(
        [
            ("perturbed", "en"),
            ("perturbed", "es"),
            ("paraphrase", "en"),
            ("near_duplicate", "en"),
            ("near_duplicate", "es"),
            ("novel", "en"),
            ("novel", "es"),
        ]
    )
    claim_ids = {i["source_claim_id"] for i in doc["contradictions"]}
    assert len(claim_ids) == 3  # three distinct real claims
    novel = [i for i in doc["duplicates"] if i["type"] == "novel"]
    assert all(
        i["review"] == "pending" and "nearest_similarity" in i and i["suspect"] is False
        for i in novel
    )
    assert doc["stats"]["dropped_items"] == 7  # one junk item per call
    assert set(model.features) == set(memory_seed.FEATURES.values())
    assert [f for _, f in env.calls["documents"]] == [memory_seed.NOVELTY_FEATURE]
    for sql in statements:
        assert sql.lstrip().upper().startswith(READ_ONLY_PREFIXES), sql


def test_memory_seed_dry_run(corpus, env, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(llm, "create_message", lambda **k: pytest.fail("dry run called Sonnet"))
    out = tmp_path / "seed.json"
    argv = [
        "memory",
        "seed",
        "--out",
        str(out),
        "--perturbed",
        "2",
        "--paraphrase",
        "1",
        "--near-duplicate",
        "2",
        "--novel",
        "2",
        "--dry-run",
    ]
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert "Dry run" in text and "estimated $" in text
    assert not out.exists()


def test_memory_seed_needs_enough_content(test_db, env, tmp_path, capsys):
    assert cli.main(["memory", "seed", "--out", str(tmp_path / "s.json"), "--dry-run"]) == 1
    assert "Not enough indexed public content" in capsys.readouterr().err


# --- Read-only guarantees + the production refusal ---------------------------------------


def test_readonly_session_refuses_an_insert(test_engine, monkeypatch):
    import app.core.database as database
    from sqlalchemy.exc import DBAPIError

    monkeypatch.setattr(database, "engine", test_engine)
    db = cli._readonly_session()
    try:
        with pytest.raises(DBAPIError, match="read-only"):
            _chunk(
                db,
                source_type="ARTICLE_CLAIM",
                source_id=1,
                article_id=None,
                content="x",
                vector=_axis(0),
            )
    finally:
        db.rollback()
        db.close()


PROD_URL = "postgresql://doadmin:x@db-postgresql-nyc1-00000-do-user-1-0.g.db.ondigitalocean.com:25060/defaultdb"


class FakeSession:
    def __init__(self, url=PROD_URL, read_only="on"):
        from sqlalchemy.engine import make_url

        self.url = make_url(url)
        self.read_only = read_only
        self.closed = False
        self.sql: list[str] = []

    def get_bind(self):
        return SimpleNamespace(engine=SimpleNamespace(url=self.url))

    def execute(self, statement, *a, **k):
        self.sql.append(str(statement))
        if "transaction_read_only" in str(statement):
            return SimpleNamespace(scalar=lambda: self.read_only)
        raise AssertionError("queried past the guard")

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.mark.parametrize("command", ["seed", "run"])
def test_memory_without_the_flag_refuses_production(
    command, monkeypatch, tmp_path, seed_file, capsys
):
    fake = FakeSession()
    monkeypatch.setattr(cli, "_readonly_session", lambda: fake)
    argv = ["memory", command, "--dry-run"] + (
        ["--seed", str(seed_file)] if command == "run" else ["--out", str(tmp_path / "s.json")]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2 and fake.closed and fake.sql == []
    assert "production" in capsys.readouterr().err


def test_read_only_prod_still_requires_a_read_only_session(monkeypatch, tmp_path, capsys):
    fake = FakeSession(read_only="off")
    monkeypatch.setattr(cli, "_readonly_session", lambda: fake)
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["memory", "seed", "--dry-run", "--read-only-prod", "--out", str(tmp_path / "s.json")]
        )
    assert exc.value.code == 2 and fake.closed
    assert "not read-only" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["drop-tables", "--read-only-prod"],
        ["draft-questions", "--read-only-prod"],
    ],
)
def test_write_capable_commands_do_not_accept_the_flag(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "unrecognized arguments: --read-only-prod" in capsys.readouterr().err


def test_retrieval_accepts_the_flag_only_with_live(monkeypatch, capsys):
    """#693: ``retrieval --live`` reads the live index read-only; eval tables never on prod."""
    monkeypatch.setattr(cli, "_session", lambda: pytest.fail("must not open a session"))
    assert cli.main(["retrieval", "--read-only-prod"]) == 2
    assert "needs --live" in capsys.readouterr().err


def test_write_capable_commands_still_refuse_production(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "environment", "development")
    fake = FakeSession()
    monkeypatch.setattr(cli, "_session", lambda: fake)
    with pytest.raises(SystemExit) as exc:
        cli.main(["drop-tables"])
    assert exc.value.code == 2 and fake.closed


def test_calibrated_defaults_with_flags_off():
    """#632 (2026-10-10): B3 calibrated to 0.725, B4 kept at 0.5; the flags stay off."""
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["suggestion_dedup_min_similarity"].default == 0.725
    assert fields["consistency_min_similarity"].default == 0.5
    assert fields["suggestion_dedup_enabled"].default is False
    assert fields["cross_article_consistency_enabled"].default is False
