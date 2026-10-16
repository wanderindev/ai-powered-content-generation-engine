"""#633/#664: glossary TSV, candidate extraction, eval-before metrics and the sync dry run."""

import csv
from types import SimpleNamespace
from unittest.mock import MagicMock

import deepl
import pytest
from scripts import eval_glossary, glossary_candidates, glossary_tsv, sync_deepl_glossary
from scripts.glossary_tsv import (
    GLOSSARY_TSV,
    GlossaryEntry,
    GlossaryFormatError,
    count_term,
    entries_from_draft,
    format_glossary_tsv,
    load_glossary_tsv,
    parse_glossary_tsv,
)

FIXTURE_TSV = (
    "en\tes\tvariants\n"
    "# comment\n"
    "Canal Zone\tZona del Canal\tzona canalera; Zona Canalera\n"
    "Casco Antiguo\tCasco Antiguo\n"
)


# --- TSV --------------------------------------------------------------------


HEADER_ONLY = "en\tes\tvariants\n"


def test_committed_tsv_is_curated_and_valid():
    """#664 committed the operator-curated glossary; it must load and stay DeepL-clean."""
    entries = load_glossary_tsv(GLOSSARY_TSV)
    # 276 drafted: 190 keep + 1 edit, 85 dropped; then the operator dropped the
    # short "Santa María la Antigua" row (it expanded every mention to the full name).
    assert len(entries) == 190
    assert sum(e.do_not_translate for e in entries) == 112
    assert all("\t" not in e.en + e.es and e.es not in e.variants for e in entries)
    by_en = {e.en: e.es for e in entries}
    assert by_en["Panama Canal"] == "Canal de Panamá"
    assert by_en["Casco Antiguo"] == "Casco Antiguo"  # do-not-translate
    assert by_en["Puerto Bello"] == "Portobelo"  # the one edited row
    assert by_en["Mule Trains"] == "caravanas de mulas"  # the corpus term, not "recuas"
    # Dropped: generic words the model flagged, plus Santa María and Columbus.
    assert (
        not {"Between", "Indigenous", "Santa María", "Santa María la Antigua", "Columbus"}
        & by_en.keys()
    )
    # Round-trips through the writer byte for byte.
    assert format_glossary_tsv(entries) == GLOSSARY_TSV.read_text(encoding="utf-8")


def _draft_row(status, term, es, variants=""):
    return {"status": status, "term": term, "proposed_es": es, "variants": variants}


def test_entries_from_draft_counts_statuses_case_insensitively():
    rows = [
        _draft_row("Keep", "Canal Zone", "Zona del Canal", "zona canalera; Zona del Canal"),
        _draft_row(" EDIT ", "Casco Antiguo", "Casco Antiguo"),
        _draft_row("drop", "Instead", "En cambio"),
    ]
    entries, counts = entries_from_draft(rows)
    assert counts == {"keep": 1, "edit": 1, "drop": 1}
    assert entries == [
        GlossaryEntry("Canal Zone", "Zona del Canal", ("zona canalera",)),
        GlossaryEntry("Casco Antiguo", "Casco Antiguo", ()),
    ]
    assert format_glossary_tsv(entries) == (
        "en\tes\tvariants\nCanal Zone\tZona del Canal\tzona canalera\n"
        "Casco Antiguo\tCasco Antiguo\n"
    )


@pytest.mark.parametrize(
    "rows",
    [
        [_draft_row("", "Canal Zone", "Zona del Canal")],  # uncurated
        [_draft_row("maybe", "Canal Zone", "Zona del Canal")],  # unknown status
        [_draft_row("keep", "Canal Zone", "")],  # empty target
        [_draft_row("keep", "Canal Zone", "A"), _draft_row("keep", "canal zone", "B")],
    ],
)
def test_entries_from_draft_rejects(rows):
    with pytest.raises(GlossaryFormatError):
        entries_from_draft(rows)


def test_glossary_tsv_main_builds_from_draft(tmp_path, capsys):
    draft = tmp_path / "draft.tsv"
    rows = [_draft_row("Keep", "Canal Zone", "Zona del Canal"), _draft_row("drop", "Within", "x")]
    glossary_candidates.write_draft(
        [{**{c: "" for c in glossary_candidates.DRAFT_COLUMNS}, **r} for r in rows], draft
    )
    out = tmp_path / "glossary.tsv"
    assert glossary_tsv.main(["--from-draft", str(draft), "--out", str(out), "--dry-run"]) == 0
    assert not out.exists()
    assert glossary_tsv.main(["--from-draft", str(draft), "--out", str(out)]) == 0
    assert load_glossary_tsv(out) == [GlossaryEntry("Canal Zone", "Zona del Canal", ())]
    assert "keep 1, edit 0, drop 1 -> 1 entries" in capsys.readouterr().out


def test_parse_glossary_tsv():
    entries = parse_glossary_tsv(FIXTURE_TSV)
    assert entries == [
        GlossaryEntry("Canal Zone", "Zona del Canal", ("zona canalera", "Zona Canalera")),
        GlossaryEntry("Casco Antiguo", "Casco Antiguo", ()),
    ]
    assert entries[1].do_not_translate and not entries[0].do_not_translate


@pytest.mark.parametrize(
    "text",
    [
        "Canal Zone\n",  # one column
        "Canal Zone\t\n",  # empty target
        "Canal Zone \tZona del Canal\n",  # stray whitespace
        "Canal Zone\tA\ncanal zone\tB\n",  # duplicate source (case-insensitive)
        "a\tb\tc\td\n",  # too many columns
    ],
)
def test_parse_glossary_tsv_rejects(text):
    with pytest.raises(GlossaryFormatError):
        parse_glossary_tsv(text)


def test_count_term_is_word_bounded_and_case_insensitive():
    assert count_term("Canal Zone", "the canal  zone and the Canal Zone's Canal Zoners") == 2
    assert count_term("Guna", "Guna Yala, guna") == 2
    assert count_term("x", None) == 0


# --- candidate extraction ---------------------------------------------------


def _docs():
    D = glossary_candidates.CorpusDoc
    return [
        D(
            1,
            "Life in the Canal Zone. The Canal Zone had its own police. In 1904 the Bay of Panama.",
            "La vida en la Zona del Canal. La Zona del Canal tenía su policía. En 1904 la Bahía de Panamá.",
        ),
        D(
            2,
            "Workers left the Canal Zone for Casco Antiguo. Spain and France watched.",
            "Los obreros dejaron la Zona del Canal por el Casco Antiguo. España y Francia miraban.",
        ),
        D(3, "The Bay of Panama was calm.", "La Bahía de Panamá estaba en calma."),
    ]


def test_capitalised_runs_keep_connectors_and_split_on_and():
    runs = glossary_candidates.capitalised_runs(
        "The Bay of Panama met Spain and France. However the Canal Zone grew."
    )
    assert runs["Bay of Panama"] == 1
    assert runs["Canal Zone"] == 1
    assert "Spain and France" not in runs and runs["Spain"] == 1
    assert "The" not in runs and "However" not in runs


def test_strip_markdown_drops_urls_and_code():
    out = glossary_candidates.strip_markdown(
        "See [the Canal](https://x.org/A_B) `Code Thing` **Bold**"
    )
    assert "https" not in out and "Code Thing" not in out and "the Canal" in out


def test_extract_candidates_and_alignment_evidence():
    cands = glossary_candidates.extract_candidates(_docs(), min_docs=2)
    by = {c.term: c for c in cands}
    assert "Canal Zone" in by and "Bay of Panama" in by
    # Casco Antiguo is a KNOWN_TERM: kept although it is in only one article.
    assert "Casco Antiguo" in by and by["Casco Antiguo"].es_verbatim == 1
    cz = by["Canal Zone"]
    assert cz.en_docs == {1, 2} and cz.en_count == 3
    assert cz.es_candidates[0]["text"] == "Zona del Canal"
    assert cz.es_candidates[0]["jaccard"] == 1.0
    assert "Canal Zone" in cz.en_example
    # Known terms sort first.
    assert cands[0].term in glossary_candidates.KNOWN_TERMS


def test_parse_proposals_validates_and_drops_malformed():
    data = [
        {
            "term": "Canal Zone",
            "es": "Zona del Canal",
            "variants": ["zona canalera", 3, "Zona del Canal"],
        },
        {"term": "Casco Antiguo", "do_not_translate": True, "es": "ignored"},
        {"term": "Unknown", "es": "x"},  # not in the batch
        {"term": "Canal Zone", "es": "again"},  # duplicate
        {"term": "Bay of Panama"},  # no es
        "not an object",
        {"term": "Spain", "es": "Espa\tña"},  # tab would break the TSV
    ]
    props, dropped = glossary_candidates.parse_proposals(
        data, ["Canal Zone", "Casco Antiguo", "Bay of Panama", "Spain"]
    )
    assert dropped == 5
    assert [p.term for p in props] == ["Canal Zone", "Casco Antiguo"]
    assert props[0].variants == ("zona canalera",)
    assert props[1].es == "Casco Antiguo" and props[1].do_not_translate


def test_parse_proposals_non_list_drops_whole_batch():
    assert glossary_candidates.parse_proposals("nope", ["A", "B"]) == ([], 2)
    props, dropped = glossary_candidates.parse_proposals(
        {"terms": [{"term": "A", "es": "a"}]}, ["A"]
    )
    assert dropped == 0 and props[0].es == "a"


def test_propose_drops_a_batch_on_malformed_json(monkeypatch):
    msg = SimpleNamespace(content=[SimpleNamespace(type="text", text="not json")])
    monkeypatch.setattr(glossary_candidates, "create_message", lambda **kw: msg)
    batch = glossary_candidates.extract_candidates(_docs())[:2]
    assert glossary_candidates.propose(batch) == ([], 2)


def test_propose_uses_the_seam_with_its_feature(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="..."),
                SimpleNamespace(
                    type="text", text='[{"term": "Canal Zone", "es": "Zona del Canal"}]'
                ),
            ]
        )

    monkeypatch.setattr(glossary_candidates, "create_message", fake)
    cands = [c for c in glossary_candidates.extract_candidates(_docs()) if c.term == "Canal Zone"]
    props, dropped = glossary_candidates.propose(cands)
    assert seen["feature"] == "glossary_candidates"
    assert seen["model"] == glossary_candidates.SONNET_MODEL
    assert dropped == 0 and props[0].es == "Zona del Canal"


def test_draft_file_format(tmp_path):
    docs = _docs()
    cands = glossary_candidates.extract_candidates(docs)
    props, _ = glossary_candidates.parse_proposals(
        [
            {
                "term": "Canal Zone",
                "es": "Zona del Canal",
                "variants": ["zona canalera"],
                "note": "ok",
            },
            {"term": "Casco Antiguo", "do_not_translate": True, "drop": True},
        ],
        [c.term for c in cands],
    )
    rows = glossary_candidates.draft_rows(cands, props, docs)
    out = glossary_candidates.write_draft(rows, tmp_path / "sub" / "draft.tsv")
    with out.open(encoding="utf-8") as fh:
        read = list(csv.DictReader(fh, delimiter="\t"))
    assert tuple(read[0].keys()) == glossary_candidates.DRAFT_COLUMNS
    cz = read[0]
    assert cz["status"] == "" and cz["proposed_es"] == "Zona del Canal"
    assert cz["es_renderings"] == "Zona del Canal: 3; zona canalera: 0"
    assert cz["es_examples"].startswith("[Zona del Canal]")
    assert cz["en_articles"] == "2"
    assert read[1]["do_not_translate"] == "yes"
    assert read[1]["note"].startswith("model suggests drop")
    assert "\t" not in "".join(cz.values())


def test_estimate_cost_counts_batches():
    cands = glossary_candidates.extract_candidates(_docs())
    est = glossary_candidates.estimate_cost(cands, batch_size=2)
    assert est["calls"] == -(-len(cands) // 2)
    assert 0 < est["cost_usd_min"] < est["cost_usd_max"]


# --- read-only + CLI against the test DB -------------------------------------


@pytest.fixture
def bilingual(test_db, article_factory):
    a = article_factory(title="The Canal Zone", content="Life in the Canal Zone and Casco Antiguo.")
    a.title_es = "La Zona del Canal"
    a.content_es = "La vida en la Zona del Canal y el Casco Antiguo."
    b = article_factory(title="Canal Zone police", content="The Canal Zone police.")
    b.content_es = "La policía de la zona canalera."
    draft = article_factory(title="Draft", content="Canal Zone draft.", published_at=None)
    draft.content_es = "Zona del Canal."
    article_factory(title="No ES", content="Canal Zone only English.")  # no content_es
    test_db.commit()
    return a, b, draft


@pytest.mark.parametrize("module", [glossary_candidates, eval_glossary])
def test_readonly_session_refuses_writes(module, test_db, test_engine, monkeypatch):
    import app.core.database as database
    from app.models.verified_claim import VerifiedClaim
    from sqlalchemy.exc import DBAPIError

    monkeypatch.setattr(database, "engine", test_engine)
    db = module._readonly_session()
    try:
        with pytest.raises(DBAPIError, match="read-only"):
            db.add(
                VerifiedClaim(claim_key="x", claim_text="x", claim_type="DATE", verdict="VERIFIED")
            )
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_load_corpus_reads_bilingual_approved(bilingual, test_db):
    docs = glossary_candidates.load_corpus(test_db)
    assert len(docs) == 3  # the draft is APPROVED too; the EN-only one is out
    assert any("Zona del Canal" in d.es for d in docs)


def test_candidates_main_dry_run_makes_no_model_call(
    bilingual, test_db, monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(glossary_candidates, "_readonly_session", lambda: test_db)
    monkeypatch.setattr(
        glossary_candidates, "create_message", MagicMock(side_effect=AssertionError("no call"))
    )
    out = tmp_path / "d.tsv"
    assert glossary_candidates.main(["--dry-run", "--out", str(out)]) == 0
    assert "Dry run" in capsys.readouterr().out and not out.exists()


def test_candidates_main_writes_draft(bilingual, test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(glossary_candidates, "_readonly_session", lambda: test_db)
    monkeypatch.setattr(
        glossary_candidates,
        "propose",
        lambda batch: (
            (
                [
                    glossary_candidates.Proposal(
                        "Canal Zone", "Zona del Canal", False, (), False, ""
                    )
                ],
                0,
            )
            if any(c.term == "Canal Zone" for c in batch)
            else ([], 0)
        ),
    )
    out = tmp_path / "d.tsv"
    assert glossary_candidates.main(["--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8"), delimiter="\t"))
    assert [r["term"] for r in rows] == ["Canal Zone"]


# --- eval before ------------------------------------------------------------


def test_measure_before_metrics():
    entries = parse_glossary_tsv(FIXTURE_TSV)
    E = eval_glossary.EvalDoc
    docs = [
        E(
            1,
            "Canal Zone, Canal Zone and Casco Antiguo.",
            "Zona del Canal y zona canalera, Casco Antiguo.",
        ),
        E(2, "The Canal Zone.", "La Zona del Canal. Zona del Canal."),  # extra ES capped at 1
        E(3, "Nothing here.", "Zona del Canal."),  # no EN mention: not counted
    ]
    m = eval_glossary.measure(entries, docs)
    cz, casco = m["terms"]
    assert cz["articles"] == 2 and cz["en_mentions"] == 3 and cz["canonical"] == 2
    assert cz["canonical_rate"] == round(2 / 3, 4)
    assert cz["variants"] == {"zona canalera": 1, "Zona Canalera": 1}  # case-insensitive count
    assert casco["canonical_rate"] == 1.0
    assert m["micro_rate"] == 0.75 and m["macro_rate"] == round((2 / 3 + 1) / 2, 4)
    md = eval_glossary.render_markdown(
        {"meta": {"mode": "before", "date": "d", "tsv": "t", "entries": 2}, "before": m}
    )
    assert "| Canal Zone | Zona del Canal | 66.7% |" in md


def test_measure_with_no_mentions():
    m = eval_glossary.measure([GlossaryEntry("Guna", "Guna")], [eval_glossary.EvalDoc(1, "x", "y")])
    assert m["micro_rate"] is None and m["terms"][0]["canonical_rate"] is None


def test_select_sample_respects_max_chars():
    docs = [eval_glossary.EvalDoc(i, "x" * 100, "y") for i in range(10)]
    sample = eval_glossary.select_sample(docs, max_chars=350, seed=1)
    assert len(sample) == 3 and sum(len(d.en) for d in sample) <= 350
    assert eval_glossary.select_sample(docs, max_chars=350, seed=1) == sample
    assert eval_glossary.estimate_deepl_usd(1_000_000) == eval_glossary.DEEPL_USD_PER_MILLION_CHARS


def test_eval_main_before_on_published_only(bilingual, test_db, monkeypatch, capsys, tmp_path):
    tsv = tmp_path / "g.tsv"
    tsv.write_text(FIXTURE_TSV, encoding="utf-8")
    monkeypatch.setattr(eval_glossary, "_readonly_session", lambda: test_db)
    out = tmp_path / "before.md"
    assert eval_glossary.main(["--tsv", str(tsv), "--out", str(out)]) == 0
    report = __import__("json").loads(out.with_suffix(".json").read_text())
    cz = report["before"]["terms"][0]
    assert report["before"]["articles"] == 2  # the unpublished draft is excluded
    assert cz["en_mentions"] == 2 and cz["canonical"] == 1
    assert cz["variants"]["zona canalera"] == 1


def test_eval_main_refuses_empty_tsv(capsys, tmp_path):
    empty = tmp_path / "empty.tsv"
    empty.write_text(HEADER_ONLY, encoding="utf-8")
    assert eval_glossary.main(["--tsv", str(empty)]) == 2
    assert "no entries" in capsys.readouterr().out


def test_eval_after_dry_run_makes_no_deepl_call(bilingual, test_db, monkeypatch, capsys, tmp_path):
    tsv = tmp_path / "g.tsv"
    tsv.write_text(FIXTURE_TSV, encoding="utf-8")
    monkeypatch.setattr(eval_glossary, "_readonly_session", lambda: test_db)
    factory = MagicMock(side_effect=AssertionError("no DeepL"))
    rc = eval_glossary.main(
        ["--tsv", str(tsv), "--mode", "after", "--dry-run", "--max-chars", "1000"],
        service_factory=factory,
    )
    assert rc == 0 and "Dry run" in capsys.readouterr().out
    factory.assert_not_called()


def test_eval_after_with_fake_service(bilingual, test_db, monkeypatch, tmp_path):
    tsv = tmp_path / "g.tsv"
    tsv.write_text(FIXTURE_TSV, encoding="utf-8")
    monkeypatch.setattr(eval_glossary, "_readonly_session", lambda: test_db)
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(deepl_api="k", deepl_glossary_id="gid"),
    )

    class Fake:
        async def translate_markdown(self, text):
            return text.replace("Canal Zone", "Zona del Canal")

    out = tmp_path / "after.md"
    rc = eval_glossary.main(
        ["--tsv", str(tsv), "--mode", "after", "--out", str(out)], service_factory=Fake
    )
    assert rc == 0
    report = __import__("json").loads(out.with_suffix(".json").read_text())
    assert report["after"]["terms"][0]["canonical_rate"] == 1.0
    assert report["before"]["terms"][0]["canonical_rate"] < 1.0


# --- sync dry run -----------------------------------------------------------


def _settings(monkeypatch, api="key", gid=""):
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(deepl_api=api, deepl_glossary_id=gid),
    )


def _translator():
    t = MagicMock()
    t.get_glossary_languages.return_value = [
        SimpleNamespace(source_lang="en", target_lang="es"),
        SimpleNamespace(source_lang="de", target_lang="en"),
    ]
    t.create_glossary.return_value = SimpleNamespace(glossary_id="new-id")
    return t


def test_sync_dry_run_lists_pairs_and_creates_nothing(monkeypatch, capsys, tmp_path):
    tsv = tmp_path / "g.tsv"
    tsv.write_text(FIXTURE_TSV, encoding="utf-8")
    _settings(monkeypatch)
    t = _translator()
    assert (
        sync_deepl_glossary.main(["--tsv", str(tsv), "--dry-run"], translator_factory=lambda k: t)
        == 0
    )
    out = capsys.readouterr().out
    assert "2 entries (1 do-not-translate)" in out
    assert "EN->ES supported: yes" in out and "Dry run" in out
    t.get_glossary_languages.assert_called_once()
    t.create_glossary.assert_not_called()
    t.delete_glossary.assert_not_called()
    t.translate_text.assert_not_called()


def test_sync_dry_run_without_key_makes_no_call(monkeypatch, capsys):
    _settings(monkeypatch, api="")
    factory = MagicMock(side_effect=AssertionError("no client"))
    assert sync_deepl_glossary.main(["--dry-run"], translator_factory=factory) == 0
    assert "DEEPL_API is empty" in capsys.readouterr().out


def test_sync_dry_run_survives_a_failed_language_call(monkeypatch, capsys):
    _settings(monkeypatch)
    t = _translator()
    t.get_glossary_languages.side_effect = deepl.DeepLException("boom")
    assert sync_deepl_glossary.main(["--dry-run"], translator_factory=lambda k: t) == 0
    assert "get_glossary_languages failed" in capsys.readouterr().out


def test_sync_refuses_bad_or_empty_tsv(monkeypatch, tmp_path):
    _settings(monkeypatch)
    bad = tmp_path / "bad.tsv"
    bad.write_text("only-one-column\n", encoding="utf-8")
    assert (
        sync_deepl_glossary.main(["--tsv", str(bad)], translator_factory=lambda k: _translator())
        == 2
    )
    empty = tmp_path / "empty.tsv"
    empty.write_text(HEADER_ONLY, encoding="utf-8")
    t = _translator()
    assert sync_deepl_glossary.main(["--tsv", str(empty)], translator_factory=lambda k: t) == 2
    t.create_glossary.assert_not_called()


def test_sync_creates_and_deletes_previous_only_when_asked(monkeypatch, capsys, tmp_path):
    tsv = tmp_path / "g.tsv"
    tsv.write_text(FIXTURE_TSV, encoding="utf-8")
    _settings(monkeypatch, gid="old-id")
    t = _translator()
    assert (
        sync_deepl_glossary.main(["--tsv", str(tsv), "--name", "n"], translator_factory=lambda k: t)
        == 0
    )
    t.create_glossary.assert_called_once_with(
        "n", "EN", "ES", {"Canal Zone": "Zona del Canal", "Casco Antiguo": "Casco Antiguo"}
    )
    t.delete_glossary.assert_not_called()
    assert "DEEPL_GLOSSARY_ID=new-id" in capsys.readouterr().out
    assert (
        sync_deepl_glossary.main(
            ["--tsv", str(tsv), "--delete-previous"], translator_factory=lambda k: t
        )
        == 0
    )
    t.delete_glossary.assert_called_once_with("old-id")
