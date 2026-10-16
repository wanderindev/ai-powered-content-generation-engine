"""Tests for the automated grammar-check stage (#409)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.enums import Language
from app.models.grammar_check import GrammarCheckFix
from app.services.grammar_check import parse_fix_lines, run_grammar_check
from sqlalchemy.exc import IntegrityError

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


def _make_client(*messages):
    """Mock Anthropic client consuming `messages` in order across BOTH the
    create (short) and stream (long) seams create_message uses."""
    queue = list(messages)

    def _create(**kwargs):
        return queue.pop(0)

    def _stream(**kwargs):
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = queue.pop(0)
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    return client


def _msg(text: str):
    return MagicMock(stop_reason="end_turn", content=[MagicMock(text=text)])


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Bypass admin token validation via dependency override (#54)."""
    from app.api.dependencies import (
        validate_admin_or_allowlisted_driver_token,
        validate_admin_token,
    )
    from app.main import app

    # dependency_overrides keys on the exact callable a router declares: the
    # dashboard/instagram/admin routers now declare the allowlisted-driver
    # variant (#444), other admin routers still the plain one. Override both.
    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    app.dependency_overrides[validate_admin_or_allowlisted_driver_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)
    app.dependency_overrides.pop(validate_admin_or_allowlisted_driver_token, None)


def _fix_line(find: str, replace: str) -> str:
    return f"FIND: {find} ||| REPLACE: {replace}"


@pytest.fixture
def article(test_db, article_factory):
    """An APPROVED article with EN body text and no Spanish translation yet."""
    row = article_factory(
        title="The 600 ton Ship",
        excerpt="A so called marvel of engineering.",
        content="The British owned canal opened after a forty day delay.",
    )
    return row


def _rows_for(test_db, article_id: int) -> list[GrammarCheckFix]:
    return (
        test_db.query(GrammarCheckFix)
        .filter(GrammarCheckFix.article_id == article_id)
        .order_by(GrammarCheckFix.id)
        .all()
    )


# --- Parser -------------------------------------------------------------------


class TestParseFixLines:
    def test_none_means_no_fixes(self):
        assert parse_fix_lines("NONE") == []
        assert parse_fix_lines("  NONE\n") == []

    def test_parses_well_formed_lines(self):
        raw = "\n".join([_fix_line("600 ton ship", "600-ton ship"), _fix_line("so called", "so-called")])
        assert parse_fix_lines(raw) == [
            ("600 ton ship", "600-ton ship"),
            ("so called", "so-called"),
        ]

    def test_preserves_inner_whitespace_verbatim(self):
        """The whitespace can *be* the fix (Europa.las -> Europa. Las)."""
        assert parse_fix_lines(_fix_line("Europa.las", "Europa. Las")) == [
            ("Europa.las", "Europa. Las")
        ]

    def test_drops_line_without_separator(self):
        assert parse_fix_lines("FIND: something is wrong") == []

    def test_drops_line_without_find_prefix(self):
        assert parse_fix_lines("Here are the fixes: ||| REPLACE: nope") == []

    def test_drops_empty_find_or_replace(self):
        assert parse_fix_lines(_fix_line("", "x")) == []
        assert parse_fix_lines(_fix_line("x", "")) == []

    def test_drops_no_op_pair(self):
        assert parse_fix_lines(_fix_line("same text", "same text")) == []

    def test_drops_pair_carrying_protocol_marker(self):
        """A swallowed second pair is parser fallout, not content (#434)."""
        swallowed = "FIND: a b ||| REPLACE: a-b FIND: c d ||| REPLACE: c-d"
        assert parse_fix_lines(swallowed) == []

    def test_ignores_blank_and_prose_lines(self):
        raw = "\n".join(["Here is what I found:", "", _fix_line("a b", "a-b"), "", "Done."])
        assert parse_fix_lines(raw) == [("a b", "a-b")]


# --- Service ------------------------------------------------------------------


class TestRunGrammarCheck:
    @patch("app.services.llm.get_client")
    def test_applies_fixes_and_stamps(self, mock_get_client, test_db, article):
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("600 ton Ship", "600-ton Ship"),
                        _fix_line("so called marvel", "so-called marvel"),
                        _fix_line("British owned canal", "British-owned canal"),
                    ]
                )
            )
        )
        fixes = run_grammar_check(article, Language.EN, test_db)

        assert [f.applied for f in fixes] == [True, True, True]
        assert article.title == "The 600-ton Ship"
        assert article.excerpt == "A so-called marvel of engineering."
        assert article.content == "The British-owned canal opened after a forty day delay."
        assert article.grammar_checked_en_at is not None
        assert article.grammar_checked_es_at is None

        rows = _rows_for(test_db, article.id)
        assert len(rows) == 3
        assert all(row.applied and row.skipped_reason is None for row in rows)
        assert all(row.language == "en" for row in rows)

    @patch("app.services.llm.get_client")
    def test_none_response_still_stamps(self, mock_get_client, test_db, article):
        mock_get_client.return_value = _make_client(_msg("NONE"))
        original = article.content

        fixes = run_grammar_check(article, Language.EN, test_db)

        assert fixes == []
        assert article.content == original
        assert article.grammar_checked_en_at is not None
        assert _rows_for(test_db, article.id) == []

    @patch("app.services.llm.get_client")
    def test_non_unique_find_is_skipped(self, mock_get_client, test_db, article):
        article.content = "The gate gate opened. The gate gate closed."
        test_db.flush()
        mock_get_client.return_value = _make_client(_msg(_fix_line("gate gate", "gate")))

        fixes = run_grammar_check(article, Language.EN, test_db)

        assert len(fixes) == 1
        assert fixes[0].applied is False
        assert fixes[0].skipped_reason == "FIND snippet occurs 2 times in the content"
        assert article.content == "The gate gate opened. The gate gate closed."

        rows = _rows_for(test_db, article.id)
        assert len(rows) == 1
        assert rows[0].applied is False
        assert "occurs 2 times" in rows[0].skipped_reason

    @patch("app.services.llm.get_client")
    def test_absent_find_is_skipped(self, mock_get_client, test_db, article):
        original = article.content
        mock_get_client.return_value = _make_client(
            _msg(_fix_line("a phrase that is not there", "something else"))
        )

        fixes = run_grammar_check(article, Language.EN, test_db)

        assert fixes[0].applied is False
        assert fixes[0].skipped_reason == "FIND snippet not found in the content"
        assert article.content == original
        assert _rows_for(test_db, article.id)[0].applied is False

    @patch("app.services.llm.get_client")
    def test_malformed_lines_produce_no_rows(self, mock_get_client, test_db, article):
        original = article.content
        mock_get_client.return_value = _make_client(
            _msg("\n".join(["FIND: no separator here", _fix_line("x", "x"), _fix_line("y", "")]))
        )

        fixes = run_grammar_check(article, Language.EN, test_db)

        assert fixes == []
        assert article.content == original
        assert _rows_for(test_db, article.id) == []
        # The check still ran, so the stamp is still set.
        assert article.grammar_checked_en_at is not None

    @patch("app.services.llm.get_client")
    def test_sequential_apply_invalidates_later_find(self, mock_get_client, test_db, article):
        article.content = "The forty day siege ended the forty day wait."
        article.title = "Siege"
        article.excerpt = "Short."
        test_db.flush()
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        # Applies: unique across the fields.
                        _fix_line("forty day siege", "forty-day siege"),
                        # Depended on the pre-edit text; now zero occurrences.
                        _fix_line("The forty day siege ended", "The forty day siege has ended"),
                    ]
                )
            )
        )

        fixes = run_grammar_check(article, Language.EN, test_db)

        assert fixes[0].applied is True
        assert fixes[1].applied is False
        assert fixes[1].skipped_reason == "FIND snippet not found in the content"
        assert article.content == "The forty-day siege ended the forty day wait."

    @patch("app.services.llm.get_client")
    def test_title_length_guard(self, mock_get_client, test_db, article):
        article.title = "A" * 250
        test_db.flush()
        mock_get_client.return_value = _make_client(_msg(_fix_line("A" * 250, "B" * 256)))

        fixes = run_grammar_check(article, Language.EN, test_db)

        assert fixes[0].applied is False
        assert fixes[0].skipped_reason == "corrected title exceeds 255 characters"
        assert article.title == "A" * 250

    @patch("app.services.llm.get_client")
    def test_spanish_pass_edits_es_fields(self, mock_get_client, test_db, article):
        article.title_es = "El barco"
        article.excerpt_es = "Un impuestoimpuesto colonial."
        article.content_es = "Sonaron las campañas de la catedral en Europa.las naves llegaron."
        test_db.flush()
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("impuestoimpuesto", "impuesto"),
                        _fix_line("las campañas de la catedral", "las campanas de la catedral"),
                        _fix_line("Europa.las", "Europa. Las"),
                    ]
                )
            )
        )

        fixes = run_grammar_check(article, Language.ES, test_db)

        assert [f.applied for f in fixes] == [True, True, True]
        assert article.excerpt_es == "Un impuesto colonial."
        assert (
            article.content_es
            == "Sonaron las campanas de la catedral en Europa. Las naves llegaron."
        )
        assert article.grammar_checked_es_at is not None
        assert article.grammar_checked_en_at is None
        assert {row.language for row in _rows_for(test_db, article.id)} == {"es"}

    @patch("app.services.llm.get_client")
    def test_rerun_appends_rows_and_restamps(self, mock_get_client, test_db, article):
        mock_get_client.return_value = _make_client(
            _msg(_fix_line("600 ton Ship", "600-ton Ship")), _msg("NONE")
        )

        run_grammar_check(article, Language.EN, test_db)
        first_stamp = article.grammar_checked_en_at
        run_grammar_check(article, Language.EN, test_db)

        assert len(_rows_for(test_db, article.id)) == 1  # NONE added nothing
        assert article.grammar_checked_en_at >= first_stamp

    @patch("app.services.llm.get_client")
    def test_service_does_not_commit(self, mock_get_client, test_db, article):
        """The endpoint owns the transaction; the service only flushes."""
        mock_get_client.return_value = _make_client(_msg("NONE"))
        with patch.object(test_db, "commit") as mock_commit:
            run_grammar_check(article, Language.EN, test_db)
        mock_commit.assert_not_called()


# --- Constraints and migration agreement ---------------------------------------


class TestGrammarCheckFixConstraints:
    def test_language_check_rejects_unknown_value(self, test_db, article):
        test_db.add(
            GrammarCheckFix(
                article_id=article.id,
                language="fr",
                find_text="a",
                replace_text="b",
                applied=True,
            )
        )
        with pytest.raises(IntegrityError):
            test_db.flush()
        test_db.rollback()

    def test_migration_0024_matches_enum(self):
        """The 0024 migration's language values agree with the Language enum."""
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "alembic"
            / "versions"
            / "0024_grammar_check.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0024", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert set(module._LANGUAGE_VALUES) == set(Language.values())


# --- Endpoint -------------------------------------------------------------------


class TestGrammarCheckEndpoint:
    @patch("app.services.llm.get_client")
    def test_happy_path(self, mock_get_client, test_client, test_db, article):
        mock_get_client.return_value = _make_client(
            _msg(
                "\n".join(
                    [
                        _fix_line("600 ton Ship", "600-ton Ship"),
                        _fix_line("not in the article at all", "nope"),
                    ]
                )
            )
        )
        response = test_client.post(f"/api/v1/admin/articles/{article.id}/grammar-check?language=en")

        assert response.status_code == 200
        body = response.json()
        assert body["article_id"] == article.id
        assert body["language"] == "en"
        assert body["applied_count"] == 1
        assert body["skipped_count"] == 1
        assert body["checked_at"] is not None
        assert body["fixes"][0] == {
            "find": "600 ton Ship",
            "replace": "600-ton Ship",
            "applied": True,
            "skipped_reason": None,
        }
        assert body["fixes"][1]["applied"] is False

        test_db.expire_all()
        assert article.title == "The 600-ton Ship"
        assert article.grammar_checked_en_at is not None
        assert len(_rows_for(test_db, article.id)) == 2

    def test_unknown_article_404(self, test_client):
        response = test_client.post("/api/v1/admin/articles/999999/grammar-check?language=en")
        assert response.status_code == 404

    def test_spanish_without_translation_400(self, test_client, article):
        response = test_client.post(
            f"/api/v1/admin/articles/{article.id}/grammar-check?language=es"
        )
        assert response.status_code == 400
        assert "Spanish translation" in response.json()["detail"]

    def test_english_without_content_400(self, test_client, test_db, article):
        article.content = ""
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/articles/{article.id}/grammar-check?language=en"
        )
        assert response.status_code == 400
        assert "English content" in response.json()["detail"]

    def test_invalid_language_422(self, test_client, article):
        response = test_client.post(
            f"/api/v1/admin/articles/{article.id}/grammar-check?language=fr"
        )
        assert response.status_code == 422

    @patch("app.services.llm.get_client")
    def test_rerun_restamps(self, mock_get_client, test_client, test_db, article):
        mock_get_client.return_value = _make_client(_msg("NONE"), _msg("NONE"))

        first = test_client.post(f"/api/v1/admin/articles/{article.id}/grammar-check?language=en")
        second = test_client.post(f"/api/v1/admin/articles/{article.id}/grammar-check?language=en")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["checked_at"] >= first.json()["checked_at"]
        test_db.expire_all()
        assert article.grammar_checked_en_at is not None
