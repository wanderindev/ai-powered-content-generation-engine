"""Tests for the Grammarly proofread flow: section splitter + grammar-source /
grammar-apply endpoints (driven by the PIC Companion extension)."""

import pytest
from app.models.enums import GrammarFixSource
from app.models.grammar_check import GrammarCheckFix
from app.services.series_sections import split_series_sections
from app.utils.markdown import markdown_to_html
from sqlalchemy.exc import IntegrityError


@pytest.fixture(autouse=True)
def _meaning_check_off():
    """These tests cover the write path itself; the #518 meaning check has its
    own suite (test_grammar_verdicts.py) and must not reach a model here."""
    from types import SimpleNamespace
    from unittest.mock import patch

    settings = SimpleNamespace(grammar_meaning_check_enabled=False, anthropic_api_key="")
    with patch("app.services.grammar_verdicts.get_settings", return_value=settings):
        yield


ABOUT_EN = (
    "**About this Article**\n\n"
    "This article is part 2 of a 3-part series about Liberal Tide.\n\n"
    "Articles in this series:\n\n"
    "- [Part one](/en/articles/part-one)\n"
    "- Part two (You are here)\n\n"
    "---\n\n"
)
ABOUT_ES = (
    "**Acerca de este artículo**\n\n"
    "Este artículo es la parte 2 de una serie de 3 partes sobre La Pepa.\n\n"
    "Artículos de esta serie:\n\n"
    "- [Parte uno](/es/articulos/parte-uno)\n"
    "- Parte dos (Estás aquí)\n\n"
    "---\n\n"
)
BODY_EN = (
    "## Introduction: Designing Impregnability\n\n"
    "In the last article we left Panama City in ashes. The **old capital** never recovered.\n\n"
    "### The Bastioned System\n\n"
    "To understand it, go back to Italy and read [the treatise](/en/articles/treatise)."
)
BODY_ES = (
    "## Introducción\n\n"
    "En el último artículo dejamos la ciudad en cenizas. La **vieja capital** nunca se recuperó.\n\n"
    "### El sistema abaluartado\n\n"
    "Para entenderlo, hay que volver a Italia."
)
CONT_EN = (
    "\n\n---\n\n**Continue Reading**\n\n"
    "Ready for the next part? Continue to Part 3: [Part three](/en/articles/part-three)"
)
CONT_ES = (
    "\n\n---\n\n**Continuar leyendo**\n\n"
    "¿Listo para la siguiente parte? Continúa a la Parte 3: [Parte tres](/es/articulos/parte-tres)"
)
CONT_LEGACY_NO_RULE = "\n\n**Continue Reading**\n\nReady for the next part? Continue to Part 3: [Part three](/en/articles/p3)"


class TestSplitSeriesSections:
    @pytest.mark.parametrize(
        ("about", "body", "cont"),
        [
            (ABOUT_EN, BODY_EN, CONT_EN),
            (ABOUT_ES, BODY_ES, CONT_ES),
            (ABOUT_EN, BODY_EN, CONT_LEGACY_NO_RULE),
            (ABOUT_EN, BODY_EN, ""),  # last member of a series
            ("", BODY_EN, ""),  # standalone article
            (ABOUT_EN.replace("---", "* * *"), BODY_EN, CONT_EN.replace("---", "* * *")),
            (
                ABOUT_EN.replace("**About", "***About").replace("Article**", "Article***"),
                BODY_EN,
                CONT_EN,
            ),
            (ABOUT_EN.replace("---\n\n", "---\n"), BODY_EN, CONT_EN),  # single newline after rule
        ],
    )
    def test_round_trips_exactly(self, about, body, cont):
        content = about + body + cont
        got_about, got_body, got_cont = split_series_sections(content)
        assert got_about == about
        assert got_body == body
        assert got_cont == cont
        assert got_about + got_body + got_cont == content

    def test_empty(self):
        assert split_series_sections(None) == ("", "", "")
        assert split_series_sections("") == ("", "", "")

    def test_body_never_contains_markers(self):
        _, body, _ = split_series_sections(ABOUT_EN + BODY_EN + CONT_EN)
        assert "About this Article" not in body
        assert "Continue Reading" not in body
        assert body.startswith("## Introduction")


@pytest.fixture
def series_article(test_db, article_factory):
    row = article_factory(title="Bastioned Walls", content=ABOUT_EN + BODY_EN + CONT_EN)
    row.title_es = "Murallas abaluartadas"
    row.content_es = ABOUT_ES + BODY_ES + CONT_ES
    test_db.commit()
    test_db.refresh(row)
    return row


def _source(test_client, article_id, lang="en"):
    resp = test_client.get(
        f"/api/v1/admin/dashboard/articles/{article_id}/grammar-source?language={lang}"
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _apply(test_client, article_id, payload, lang="en"):
    return test_client.post(
        f"/api/v1/admin/dashboard/articles/{article_id}/grammar-apply?language={lang}", json=payload
    )


class TestGrammarSource:
    def test_strips_sections_and_renders_body(self, test_client, admin_token, series_article):
        data = _source(test_client, series_article.id)
        assert data["has_about"] is True
        assert data["has_continue"] is True
        assert data["title"] == "Bastioned Walls"
        assert data["block_count"] == 4
        assert "About this Article" not in data["body_html"]
        assert "Continue Reading" not in data["body_html"]
        assert "<h2" in data["body_html"] and "<strong>old capital</strong>" in data["body_html"]
        assert len(data["fingerprint"]) == 64

    def test_spanish(self, test_client, admin_token, series_article):
        data = _source(test_client, series_article.id, "es")
        assert data["language"] == "es"
        assert data["title"] == "Murallas abaluartadas"
        assert "Acerca de este" not in data["body_html"]
        assert "vieja capital" in data["body_html"]

    def test_standalone_article(self, test_client, admin_token, article_factory):
        row = article_factory(content=BODY_EN)
        data = _source(test_client, row.id)
        assert data["has_about"] is False and data["has_continue"] is False

    def test_missing_language_400(self, test_client, admin_token, article_factory):
        row = article_factory(content=BODY_EN)
        resp = test_client.get(
            f"/api/v1/admin/dashboard/articles/{row.id}/grammar-source?language=es"
        )
        assert resp.status_code == 400

    def test_404(self, test_client, admin_token):
        resp = test_client.get("/api/v1/admin/dashboard/articles/999999/grammar-source?language=en")
        assert resp.status_code == 404


class TestGrammarApply:
    def test_dry_run_round_trip_is_unchanged(
        self, test_client, admin_token, test_db, series_article
    ):
        """The serializer acceptance test: source html posted back verbatim is a no-op."""
        src = _source(test_client, series_article.id)
        resp = _apply(
            test_client,
            series_article.id,
            {"body_html": src["body_html"], "fingerprint": src["fingerprint"], "dry_run": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["changed"] is False
        assert data["written"] is False
        assert data["checked_at"] is None
        assert data["diff_preview"] == []
        test_db.refresh(series_article)
        assert series_article.grammar_checked_en_at is None
        assert series_article.content == ABOUT_EN + BODY_EN + CONT_EN

    def test_apply_writes_body_and_keeps_sections_byte_identical(
        self, test_client, admin_token, test_db, series_article
    ):
        src = _source(test_client, series_article.id)
        corrected = src["body_html"].replace(
            "In the last article we left", "In the last article, we left"
        )
        resp = _apply(
            test_client,
            series_article.id,
            {
                "body_html": corrected,
                "fingerprint": src["fingerprint"],
                "accepted": [
                    {
                        "category": "Grammar",
                        "kind": "Add a comma",
                        "before": "In the last article we left",
                        "after": "In the last article, we left",
                    }
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["changed"] is True and data["written"] is True
        assert data["accepted_count"] == 1
        assert data["checked_at"] is not None
        assert any("In the last article, we left" in line for line in data["diff_preview"])

        test_db.refresh(series_article)
        about, body, cont = split_series_sections(series_article.content)
        assert about == ABOUT_EN
        assert cont == CONT_EN
        assert "In the last article, we left" in body
        assert body.startswith("## Introduction: Designing Impregnability")
        assert "**old capital**" in body
        assert "[the treatise](/en/articles/treatise)" in body
        assert series_article.grammar_checked_en_at is not None
        assert series_article.grammar_checked_es_at is None
        # Spanish column untouched.
        assert series_article.content_es == ABOUT_ES + BODY_ES + CONT_ES

        rows = test_db.query(GrammarCheckFix).filter_by(article_id=series_article.id).all()
        assert len(rows) == 1
        assert rows[0].source == GrammarFixSource.GRAMMARLY.value
        assert rows[0].applied is True
        assert rows[0].language == "en"
        assert rows[0].find_text == "In the last article we left"

    def test_apply_spanish(self, test_client, admin_token, test_db, series_article):
        src = _source(test_client, series_article.id, "es")
        corrected = src["body_html"].replace("dejamos la ciudad", "dejamos a la ciudad")
        resp = _apply(
            test_client,
            series_article.id,
            {
                "body_html": corrected,
                "fingerprint": src["fingerprint"],
                "accepted": [{"before": "dejamos la ciudad", "after": "dejamos a la ciudad"}],
            },
            lang="es",
        )
        assert resp.status_code == 200, resp.text
        test_db.refresh(series_article)
        about, body, cont = split_series_sections(series_article.content_es)
        assert about == ABOUT_ES and cont == CONT_ES
        assert "dejamos a la ciudad" in body
        assert series_article.grammar_checked_es_at is not None
        assert series_article.grammar_checked_en_at is None
        assert series_article.content == ABOUT_EN + BODY_EN + CONT_EN

    def test_stamp_false_writes_body_but_leaves_article_queued(
        self, test_client, admin_token, test_db, series_article
    ):
        # The extension sends stamp=False when Grammarly's final score is still
        # below the operator's bar: keep the corrections, keep the article in
        # the queue for another pass.
        src = _source(test_client, series_article.id)
        corrected = src["body_html"].replace(
            "In the last article we left", "In the last article, we left"
        )
        resp = _apply(
            test_client,
            series_article.id,
            {
                "body_html": corrected,
                "fingerprint": src["fingerprint"],
                "stamp": False,
                "score": 93,
                "accepted": [
                    {
                        "before": "In the last article we left",
                        "after": "In the last article, we left",
                    }
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["written"] is True
        assert data["stamped"] is False
        assert data["checked_at"] is None
        test_db.refresh(series_article)
        _, body, _ = split_series_sections(series_article.content)
        assert "In the last article, we left" in body
        assert series_article.grammar_checked_en_at is None
        assert test_db.query(GrammarCheckFix).filter_by(article_id=series_article.id).count() == 1

    def test_log_line_labels_match_values(
        self, test_client, admin_token, test_db, series_article, caplog
    ):
        # #727: changed/stamped/score were printed under each other's labels,
        # and the score only ever appears in this line.
        src = _source(test_client, series_article.id)
        corrected = src["body_html"].replace(
            "In the last article we left", "In the last article, we left"
        )
        with caplog.at_level("INFO", logger="app"):
            resp = _apply(
                test_client,
                series_article.id,
                {
                    "body_html": corrected,
                    "fingerprint": src["fingerprint"],
                    "stamp": False,
                    "score": 93,
                    "accepted": [
                        {
                            "before": "In the last article we left",
                            "after": "In the last article, we left",
                        }
                    ],
                },
            )
        assert resp.status_code == 200, resp.text
        line = next(
            r.getMessage() for r in caplog.records if "grammar-apply article=" in r.getMessage()
        )
        assert "written=True changed=True stamped=False score=93" in line

    def test_stamp_false_with_nothing_accepted_is_a_no_op(
        self, test_client, admin_token, test_db, series_article
    ):
        src = _source(test_client, series_article.id)
        resp = _apply(
            test_client,
            series_article.id,
            {"body_html": src["body_html"], "fingerprint": src["fingerprint"], "stamp": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stamped"] is False
        assert resp.json()["written"] is False
        test_db.refresh(series_article)
        assert series_article.grammar_checked_en_at is None

    def test_default_stamps_and_reports_it(self, test_client, admin_token, series_article):
        src = _source(test_client, series_article.id)
        resp = _apply(
            test_client,
            series_article.id,
            {"body_html": src["body_html"], "fingerprint": src["fingerprint"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stamped"] is True
        assert resp.json()["checked_at"] is not None

    def test_score_out_of_range_422(self, test_client, admin_token, series_article):
        src = _source(test_client, series_article.id)
        resp = _apply(
            test_client,
            series_article.id,
            {"body_html": src["body_html"], "fingerprint": src["fingerprint"], "score": 101},
        )
        assert resp.status_code == 422

    def test_empty_accepted_stamps_without_writing(
        self, test_client, admin_token, test_db, series_article
    ):
        src = _source(test_client, series_article.id)
        # Even a differing body is not written when nothing was accepted.
        corrected = src["body_html"].replace("go back to Italy", "go back to Italy quickly")
        resp = _apply(
            test_client,
            series_article.id,
            {"body_html": corrected, "fingerprint": src["fingerprint"], "accepted": []},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["written"] is False
        assert data["changed"] is True
        assert data["checked_at"] is not None
        test_db.refresh(series_article)
        assert series_article.content == ABOUT_EN + BODY_EN + CONT_EN
        assert series_article.grammar_checked_en_at is not None
        assert test_db.query(GrammarCheckFix).count() == 0

    def test_fingerprint_mismatch_409(self, test_client, admin_token, test_db, series_article):
        src = _source(test_client, series_article.id)
        series_article.content = series_article.content + "\n\nEdited meanwhile."
        test_db.commit()
        resp = _apply(
            test_client,
            series_article.id,
            {"body_html": src["body_html"], "fingerprint": src["fingerprint"], "accepted": []},
        )
        assert resp.status_code == 409

    def test_block_count_mismatch_400(self, test_client, admin_token, test_db, series_article):
        src = _source(test_client, series_article.id)
        # Drop the last paragraph entirely.
        html = markdown_to_html(BODY_EN.rsplit("\n\n", 1)[0])
        resp = _apply(
            test_client,
            series_article.id,
            {
                "body_html": html,
                "fingerprint": src["fingerprint"],
                "accepted": [{"before": "x", "after": "y"}],
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Paragraph count changed" in detail["message"]
        assert detail["diff_preview"]
        test_db.refresh(series_article)
        assert series_article.content == ABOUT_EN + BODY_EN + CONT_EN
        assert series_article.grammar_checked_en_at is None

    def test_empty_html_400(self, test_client, admin_token, series_article):
        src = _source(test_client, series_article.id)
        resp = _apply(
            test_client, series_article.id, {"body_html": "", "fingerprint": src["fingerprint"]}
        )
        assert resp.status_code == 400

    def test_unknown_tool_422(self, test_client, admin_token, series_article):
        src = _source(test_client, series_article.id)
        resp = _apply(
            test_client,
            series_article.id,
            {
                "body_html": src["body_html"],
                "fingerprint": src["fingerprint"],
                "tool": "languagetool",
            },
        )
        assert resp.status_code == 422

    def test_requires_admin(self, test_client, series_article):
        resp = test_client.get(
            f"/api/v1/admin/dashboard/articles/{series_article.id}/grammar-source?language=en"
        )
        assert resp.status_code == 401


class TestGrammarFixSourceConstraint:
    def test_default_is_llm(self, test_db, series_article):
        row = GrammarCheckFix(
            article_id=series_article.id,
            language="en",
            find_text="a",
            replace_text="b",
            applied=True,
        )
        test_db.add(row)
        test_db.commit()
        test_db.refresh(row)
        assert row.source == "LLM"

    def test_rejects_unknown_source(self, test_db, series_article):
        test_db.add(
            GrammarCheckFix(
                article_id=series_article.id,
                language="en",
                find_text="a",
                replace_text="b",
                applied=True,
                source="WORD",
            )
        )
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()

    def test_migration_0032_declared_the_original_vocabulary(self):
        """0032 historically created the constraint with LLM + GRAMMARLY; 0039
        (#557) later widened it with ES_ACCURACY. Present-tense agreement
        between the newest migration and ``GrammarFixSource`` is
        ``tests/test_migration_check_vocabularies.py`` (#572), not this one."""
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "alembic/versions/0032_grammar_fix_source.py"
        spec = importlib.util.spec_from_file_location("migration_0032", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert set(module._SOURCES) == {"LLM", "GRAMMARLY"}
        assert set(module._SOURCES) <= set(GrammarFixSource.values())
