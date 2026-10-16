"""Tests for the Spanish accuracy check against the English (#557)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.grammar_check import GrammarCheckFix
from app.services.es_accuracy import render_segments, run_es_accuracy_check

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")

URL = "/api/v1/admin/articles/{id}/es-accuracy-check"

EN_BODY = (
    "The canal opened in 1914 after ten years of work.\n\n"
    "Roughly 5,600 workers died during the American construction.\n\n"
    "The French did not finish the canal."
)
ES_BODY = (
    "El canal abrió en 1941 tras diez años de trabajo.\n\n"
    "Unos 5,600 trabajadores murieron durante la construcción estadounidense.\n\n"
    "Los franceses terminaron el canal."
)
ABOUT_ES = "**Acerca de este artículo**\n\nParte 1 de una serie.\n\n---\n\n"
CONTINUE_ES = "\n\n---\n\n**Continuar leyendo**\n\nSiguiente: Parte 2."


def _make_client(*messages, calls=None):
    """Mock Anthropic client consuming `messages` in order across both the
    create and stream seams; records each call's kwargs into `calls`."""
    queue = list(messages)

    def _take(kwargs):
        if calls is not None:
            calls.append(kwargs)
        return queue.pop(0)

    def _create(**kwargs):
        return _take(kwargs)

    def _stream(**kwargs):
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = _take(kwargs)
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    return client


def _msg(text: str):
    return MagicMock(stop_reason="end_turn", content=[MagicMock(text=text)])


def _fix_line(find: str, replace: str) -> str:
    return f"FIND: {find} ||| REPLACE: {replace}"


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    from app.api.dependencies import (
        validate_admin_or_allowlisted_driver_token,
        validate_admin_token,
    )
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    app.dependency_overrides[validate_admin_or_allowlisted_driver_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)
    app.dependency_overrides.pop(validate_admin_or_allowlisted_driver_token, None)


@pytest.fixture
def article(test_db, article_factory):
    """A not-yet-live translated article whose Spanish carries DeepL mistakes."""
    row = article_factory(
        title="The Canal Opens",
        title_es="El canal cierra",
        excerpt="How the canal was finished.",
        content=EN_BODY,
        status="PENDING",
        published_at=None,
    )
    row.excerpt_es = "Cómo se terminó el canal."
    row.content_es = ES_BODY
    test_db.commit()
    test_db.refresh(row)
    return row


def _rows_for(test_db, article_id: int) -> list[GrammarCheckFix]:
    return (
        test_db.query(GrammarCheckFix)
        .filter(GrammarCheckFix.article_id == article_id)
        .order_by(GrammarCheckFix.id)
        .all()
    )


# --- Segment rendering ---------------------------------------------------------


class TestRenderSegments:
    def test_pairs_title_excerpt_and_paragraphs(self, article):
        text = render_segments(article, EN_BODY, ES_BODY)
        # title, excerpt, three paragraphs
        assert "[5] ENGLISH\nThe French did not finish the canal." in text
        assert "[5] SPANISH\nLos franceses terminaron el canal." in text
        assert "[1] SPANISH\nEl canal cierra" in text
        assert "[6]" not in text

    def test_mismatched_paragraph_counts_send_whole_bodies(self, article):
        es_merged = ES_BODY.replace("\n\n", " ", 1)
        text = render_segments(article, EN_BODY, es_merged)
        assert "[3] ENGLISH\n" + EN_BODY in text
        assert "[3] SPANISH\n" + es_merged in text
        assert "[4]" not in text


# --- Service --------------------------------------------------------------------


class TestRunEsAccuracyCheck:
    @patch("app.services.llm.get_client")
    def test_applies_spanish_fixes_and_audits(self, mock_get_client, test_db, article):
        calls: list[dict] = []
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("El canal cierra", "El canal abre"),
                        _fix_line("abrió en 1941", "abrió en 1914"),
                        _fix_line("Los franceses terminaron", "Los franceses no terminaron"),
                        _fix_line("el canal", "el Canal"),  # ambiguous: skipped
                        _fix_line("not in the Spanish", "nope"),  # absent: skipped
                        "this line is not a fix",  # malformed: dropped, no row
                    ]
                )
            ),
            calls=calls,
        )

        fixes = run_es_accuracy_check(article, test_db)
        test_db.commit()

        assert [f.applied for f in fixes] == [True, True, True, False, False]
        assert "occurs" in fixes[3].skipped_reason
        assert "not found" in fixes[4].skipped_reason
        test_db.refresh(article)
        assert article.title_es == "El canal abre"
        assert "abrió en 1914" in article.content_es
        assert "Los franceses no terminaron el canal." in article.content_es
        # The English is never touched.
        assert article.content == EN_BODY
        assert article.title == "The Canal Opens"

        rows = _rows_for(test_db, article.id)
        assert len(rows) == 5
        assert {r.source for r in rows} == {"ES_ACCURACY"}
        assert {r.language for r in rows} == {"es"}

        # The call goes through the seam as specified by the ticket.
        assert len(calls) == 1
        call = calls[0]
        assert call["output_config"] == {"effort": "low"}
        assert "[1] ENGLISH\nThe Canal Opens" in call["messages"][0]["content"]

    @patch("app.services.llm.get_client")
    def test_none_is_success_with_no_rows(self, mock_get_client, test_db, article):
        mock_get_client.return_value = _make_client(_msg("NONE"))
        before = article.content_es

        assert run_es_accuracy_check(article, test_db) == []
        test_db.commit()
        test_db.refresh(article)
        assert article.content_es == before
        assert _rows_for(test_db, article.id) == []

    @patch("app.services.llm.get_client")
    def test_series_sections_are_kept_and_never_edited(self, mock_get_client, test_db, article):
        article.content_es = ABOUT_ES + ES_BODY + CONTINUE_ES
        test_db.commit()
        calls: list[dict] = []
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("abrió en 1941", "abrió en 1914"),
                        # Only in the About section — outside the checked body.
                        _fix_line("Parte 1 de una serie", "Parte uno"),
                    ]
                )
            ),
            calls=calls,
        )

        fixes = run_es_accuracy_check(article, test_db)
        test_db.commit()
        test_db.refresh(article)

        assert [f.applied for f in fixes] == [True, False]
        assert article.content_es == ABOUT_ES + ES_BODY.replace("1941", "1914") + CONTINUE_ES
        assert "Acerca de este artículo" not in calls[0]["messages"][0]["content"]

    @patch("app.services.llm.get_client")
    def test_replace_is_normalised_to_comma_thousands(self, mock_get_client, test_db, article):
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("diez años", "diez años y 1.500 días"),
                        # Becomes a no-op after normalisation: dropped, no row.
                        _fix_line("Unos 5,600", "Unos 5.600"),
                    ]
                )
            )
        )

        fixes = run_es_accuracy_check(article, test_db)
        test_db.commit()
        test_db.refresh(article)

        assert len(fixes) == 1
        assert fixes[0].replace == "diez años y 1,500 días"
        assert "diez años y 1,500 días" in article.content_es

    @patch("app.services.llm.get_client")
    def test_service_does_not_commit(self, mock_get_client, test_db, article):
        mock_get_client.return_value = _make_client(
            _msg(_fix_line("abrió en 1941", "abrió en 1914"))
        )
        run_es_accuracy_check(article, test_db)
        test_db.rollback()
        test_db.refresh(article)
        assert "1941" in article.content_es
        assert _rows_for(test_db, article.id) == []


# --- Endpoint -------------------------------------------------------------------


class TestEsAccuracyEndpoint:
    @patch("app.services.llm.get_client")
    def test_happy_path(self, mock_get_client, test_client, test_db, article):
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("abrió en 1941", "abrió en 1914"),
                        _fix_line("not in the Spanish", "nope"),
                    ]
                )
            )
        )
        response = test_client.post(URL.format(id=article.id))

        assert response.status_code == 200
        body = response.json()
        assert body["article_id"] == article.id
        assert body["applied_count"] == 1
        assert body["skipped_count"] == 1
        assert body["fixes"][0] == {
            "find": "abrió en 1941",
            "replace": "abrió en 1914",
            "applied": True,
            "skipped_reason": None,
        }
        test_db.expire_all()
        assert "abrió en 1914" in article.content_es
        assert len(_rows_for(test_db, article.id)) == 2

    @patch("app.services.llm.get_client")
    def test_replay_converges(self, mock_get_client, test_client, test_db, article):
        """#495: a re-run after a lost response is a 200, not an error — the
        applied fix's FIND is gone (skipped row) and accurate Spanish is NONE."""
        fix = _msg(_fix_line("abrió en 1941", "abrió en 1914"))
        mock_get_client.return_value = _make_client(
            fix, _msg(_fix_line("abrió en 1941", "abrió en 1914")), _msg("NONE")
        )

        first = test_client.post(URL.format(id=article.id))
        second = test_client.post(URL.format(id=article.id))
        third = test_client.post(URL.format(id=article.id))

        assert [r.status_code for r in (first, second, third)] == [200, 200, 200]
        assert first.json()["applied_count"] == 1
        assert second.json()["applied_count"] == 0
        assert second.json()["skipped_count"] == 1
        assert third.json()["fixes"] == []
        test_db.expire_all()
        assert article.content_es.count("1914") == 1

    def test_unknown_article_404(self, test_client):
        assert test_client.post(URL.format(id=999999)).status_code == 404

    def test_without_translation_400(self, test_client, test_db, article):
        article.content_es = None
        test_db.commit()
        response = test_client.post(URL.format(id=article.id))
        assert response.status_code == 400
        assert "Spanish translation" in response.json()["detail"]

    def test_without_english_400(self, test_client, test_db, article):
        article.content = ""
        test_db.commit()
        response = test_client.post(URL.format(id=article.id))
        assert response.status_code == 400
        assert "English content" in response.json()["detail"]
