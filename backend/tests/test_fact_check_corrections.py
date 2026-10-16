"""Tests for applying fact-check corrections to content (#387)."""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.crawl_job import CrawlJob, CrawlJobType
from app.models.enums import FactCheckSubject, FactCheckVerdict, FactClaimType
from app.models.fact_check import FactCheckClaim
from app.models.research import Research
from app.services.fact_check_corrections import (
    EDIT_SKIPPED,
    CorrectionEdit,
    apply_corrections,
    build_preview,
    correctable_claims,
    parse_edit_blocks,
)

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


def _truncated():
    """A response cut off at max_tokens — create_message returns it (with
    reject_on_truncation=False) so the caller can bisect the chunk."""
    return MagicMock(stop_reason="max_tokens", content=[MagicMock(text="")])


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


def _edit_block(claim_id: int, find: str, replace: str) -> str:
    return f"EDIT: {claim_id}\nFIND:\n{find}\nREPLACE:\n{replace}\nEND EDIT"


_PROTOCOL_MARKER = re.compile(r"^(EDIT: \d+|FIND:|REPLACE:|END EDIT|SKIP: \d+)$")


def _assert_no_protocol_markers(edits):
    """#433 fingerprint guard: no parsed snippet may carry a protocol line."""
    for _claim_id, find, replace in edits:
        for snippet in (find, replace):
            for line in snippet.splitlines():
                assert not _PROTOCOL_MARKER.match(line.strip()), f"protocol marker leaked: {line!r}"


def _inaccurate_claim(subject_type, subject_id, text, correction, **overrides):
    base = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "claim_text": text,
        "claim_type": FactClaimType.DATE.value,
        "verdict": FactCheckVerdict.INACCURATE.value,
        "correction": correction,
        "checked_via": "WEB_SEARCH",
    }
    base.update(overrides)
    return FactCheckClaim(**base)


def _research_with_claims(test_db, category_factory, content, claims):
    category = category_factory()
    suggestion = ArticleSuggestion(
        title="Test Suggestion",
        category_id=category.id,
        status="APPROVED",
        main_topic="Panama Viejo",
        point_of_view="narrative",
    )
    test_db.add(suggestion)
    test_db.flush()
    research = Research(suggestion_id=suggestion.id, content=content, status="APPROVED")
    test_db.add(research)
    test_db.flush()
    rows = [
        _inaccurate_claim(FactCheckSubject.RESEARCH.value, research.id, text, correction)
        for text, correction in claims
    ]
    test_db.add_all(rows)
    test_db.flush()
    return research, rows


# --- parse_edit_blocks ----------------------------------------------------------


class TestParseEditBlocks:
    def test_parses_well_formed_blocks_and_skips(self):
        raw = "\n".join(
            [
                _edit_block(7, "founded in 1520", "founded in 1519"),
                "SKIP: 9 | the content does not assert this",
                _edit_block(11, "by Morgan alone", "by Morgan's expedition"),
            ]
        )
        edits, skips = parse_edit_blocks(raw)
        assert edits == [
            (7, "founded in 1520", "founded in 1519"),
            (11, "by Morgan alone", "by Morgan's expedition"),
        ]
        assert skips == {9: "the content does not assert this"}

    def test_multiline_snippets_preserved(self):
        raw = "EDIT: 3\nFIND:\nline one\nline two\nREPLACE:\nline one\nline 2\nEND EDIT"
        edits, _ = parse_edit_blocks(raw)
        assert edits == [(3, "line one\nline two", "line one\nline 2")]

    def test_malformed_and_noop_blocks_dropped(self):
        raw = "\n".join(
            [
                "Sure, here are the edits:",  # prose is ignored
                "EDIT: 1",  # no FIND: follows
                _edit_block(2, "same text", "same text"),  # no-op
                "EDIT: 4\nFIND:\nnever closed",  # truncated block
            ]
        )
        edits, skips = parse_edit_blocks(raw)
        assert edits == []
        assert skips == {}

    def test_missing_end_edit_does_not_swallow_next_block(self):
        """#433: the research-72 shape — block A lost its END EDIT, so the whole
        next block used to end up inside A's REPLACE."""
        raw = "\n".join(
            [
                "EDIT: 7257",
                "FIND:",
                "the treaty of 1903",
                "REPLACE:",
                "the treaty of 1904",
                _edit_block(7272, "sacked in 1671", "sacked in 1670"),
            ]
        )
        edits, skips = parse_edit_blocks(raw)
        assert edits == [
            (7257, "the treaty of 1903", "the treaty of 1904"),
            (7272, "sacked in 1671", "sacked in 1670"),
        ]
        assert skips == {}
        _assert_no_protocol_markers(edits)

    def test_missing_end_edit_before_skip_line(self):
        raw = "\n".join(
            [
                "EDIT: 7",
                "FIND:",
                "founded in 1520",
                "REPLACE:",
                "founded in 1519",
                "SKIP: 9 | the content does not assert this",
            ]
        )
        edits, skips = parse_edit_blocks(raw)
        assert edits == [(7, "founded in 1520", "founded in 1519")]
        assert skips == {9: "the content does not assert this"}
        _assert_no_protocol_markers(edits)

    def test_two_consecutive_blocks_missing_end_edit(self):
        """#433: the article-68 shape — two blocks swallowed at once."""
        raw = "\n".join(
            [
                "EDIT: 5095",
                "FIND:",
                "Drake died in 1595",
                "REPLACE:",
                "Drake died in 1596",
                "EDIT: 5101",
                "FIND:",
                "Nombre de Dios was burned",
                "REPLACE:",
                "Nombre de Dios was sacked",
                _edit_block(5110, "in 1572", "in 1573"),
            ]
        )
        edits, _ = parse_edit_blocks(raw)
        assert edits == [
            (5095, "Drake died in 1595", "Drake died in 1596"),
            (5101, "Nombre de Dios was burned", "Nombre de Dios was sacked"),
            (5110, "in 1572", "in 1573"),
        ]
        _assert_no_protocol_markers(edits)

    def test_new_block_during_find_phase_drops_the_open_block(self):
        raw = "\n".join(
            [
                "EDIT: 1",
                "FIND:",
                "no replace ever came",
                _edit_block(2, "founded in 1520", "founded in 1519"),
            ]
        )
        edits, _ = parse_edit_blocks(raw)
        assert edits == [(2, "founded in 1520", "founded in 1519")]


# --- correctable_claims ---------------------------------------------------------


class TestCorrectableClaims:
    def test_only_inaccurate_with_correction(self, test_db):
        rows = [
            _inaccurate_claim(FactCheckSubject.RESEARCH.value, 1, "a", "fix a"),
            _inaccurate_claim(FactCheckSubject.RESEARCH.value, 1, "b", None),
            _inaccurate_claim(
                FactCheckSubject.RESEARCH.value,
                1,
                "c",
                "positions",
                verdict=FactCheckVerdict.DISPUTED.value,
            ),
            _inaccurate_claim(
                FactCheckSubject.RESEARCH.value,
                1,
                "d",
                "already fixed",
                verdict=FactCheckVerdict.CORRECTED.value,
            ),
        ]
        test_db.add_all(rows)
        test_db.flush()
        found = correctable_claims(FactCheckSubject.RESEARCH.value, 1, test_db)
        assert [c.claim_text for c in found] == ["a"]


# --- build_preview ---------------------------------------------------------------


class TestBuildPreview:
    @patch("app.services.llm.get_client")
    def test_validates_edits_against_content(self, mock_get_client, test_db, category_factory):
        content = "The city was founded in 1520. It repeats repeats twice."
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            content,
            [
                ("Founded in 1520", "Founded in 1519"),
                ("Something absent", "irrelevant"),
                ("Repeats", "should be ambiguous"),
                ("No edit for this one", "correction"),
            ],
        )
        response = "\n".join(
            [
                _edit_block(rows[0].id, "founded in 1520", "founded in 1519"),
                _edit_block(rows[1].id, "not in the content at all", "whatever"),
                _edit_block(rows[2].id, "repeats", "recurs"),
                f"SKIP: {rows[3].id} | inaccuracy not asserted",
            ]
        )
        mock_get_client.return_value = _make_client(_msg(response))

        edits = build_preview(FactCheckSubject.RESEARCH.value, research, rows)
        by_claim = {e.claim_id: e for e in edits}
        assert by_claim[rows[0].id].status == "ok"
        assert by_claim[rows[1].id].status == "not_found"
        assert by_claim[rows[2].id].status == "ambiguous"
        assert by_claim[rows[3].id].status == "skipped"
        assert all(e.language == "en" for e in edits)

    @patch("app.services.llm.get_client")
    def test_missing_edit_reported(self, mock_get_client, test_db, category_factory):
        research, rows = _research_with_claims(
            test_db, category_factory, "Some content.", [("claim", "fix")]
        )
        mock_get_client.return_value = _make_client(_msg("no blocks here"))
        edits = build_preview(FactCheckSubject.RESEARCH.value, research, rows)
        assert [e.status for e in edits] == ["missing"]

    @patch("app.services.llm.get_client")
    def test_many_claims_chunked_across_llm_calls(self, mock_get_client, test_db, category_factory):
        # 9 claims > _CLAIMS_PER_CALL (8) → two Sonnet calls, edits merged
        # (19 claims in one 4000-token call hit TruncationError in prod).
        content = " ".join(f"Fact number {n} is stated wrong here." for n in range(9))
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            content,
            [(f"claim {n}", f"fix {n}") for n in range(9)],
        )
        first = "\n".join(
            _edit_block(
                row.id,
                f"Fact number {n} is stated wrong here.",
                f"Fact number {n} is now corrected.",
            )
            for n, row in enumerate(rows[:8])
        )
        second = _edit_block(
            rows[8].id,
            "Fact number 8 is stated wrong here.",
            "Fact number 8 is now corrected.",
        )
        client = _make_client(_msg(first), _msg(second))
        mock_get_client.return_value = client

        edits = build_preview(FactCheckSubject.RESEARCH.value, research, rows)

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 2
        assert [e.status for e in edits] == ["ok"] * 9

    @patch("app.services.llm.get_client")
    def test_truncated_chunk_bisects_and_recovers(self, mock_get_client, test_db, category_factory):
        # A chunk whose combined output overflows max_tokens is split in half
        # and each half retried — a research doc with long snippets hit this at
        # max_tokens=8000 in prod (RESEARCH/34, 2026-07-15).
        content = " ".join(f"Fact number {n} is stated wrong here." for n in range(2))
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            content,
            [(f"claim {n}", f"fix {n}") for n in range(2)],
        )
        halves = [
            _edit_block(
                row.id,
                f"Fact number {n} is stated wrong here.",
                f"Fact number {n} is now corrected.",
            )
            for n, row in enumerate(rows)
        ]
        # First call truncates; bisection retries each single claim, both OK.
        client = _make_client(_truncated(), _msg(halves[0]), _msg(halves[1]))
        mock_get_client.return_value = client

        edits = build_preview(FactCheckSubject.RESEARCH.value, research, rows)

        calls = client.messages.create.call_count + client.messages.stream.call_count
        assert calls == 3
        assert [e.status for e in edits] == ["ok", "ok"]

    @patch("app.services.llm.get_client")
    def test_single_claim_truncation_degrades_to_not_correctable(
        self, mock_get_client, test_db, category_factory
    ):
        # A lone claim that overflows even by itself is dropped (surfaced as
        # not-correctable), never 500-ing the whole preview.
        research, rows = _research_with_claims(
            test_db, category_factory, "Some content.", [("claim", "fix")]
        )
        mock_get_client.return_value = _make_client(_truncated())
        edits = build_preview(FactCheckSubject.RESEARCH.value, research, rows)
        assert [e.status for e in edits] == ["missing"]

    @patch("app.services.llm.get_client")
    def test_article_generates_both_languages(self, mock_get_client, test_db, category_factory):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="s", category_id=category.id, status="APPROVED", point_of_view="n"
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="r", status="APPROVED")
        test_db.add(research)
        test_db.flush()
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="a",
            outline="o",
            outline_status="APPROVED",
            status="PENDING",
            content="Founded in 1520 by settlers.",
            content_es="Fundada en 1520 por colonos.",
        )
        test_db.add(article)
        test_db.flush()
        row = _inaccurate_claim(
            FactCheckSubject.ARTICLE.value, article.id, "Founded in 1520", "Founded in 1519"
        )
        test_db.add(row)
        test_db.flush()

        mock_get_client.return_value = _make_client(
            _msg(_edit_block(row.id, "in 1520 by", "in 1519 by")),
            _msg(_edit_block(row.id, "en 1520 por", "en 1519 por")),
        )
        edits = build_preview(FactCheckSubject.ARTICLE.value, article, [row])
        assert {(e.language, e.status) for e in edits} == {("en", "ok"), ("es", "ok")}


# --- apply_corrections -----------------------------------------------------------


class TestApplyCorrections:
    def test_applies_and_flips_to_corrected(self, test_db, category_factory):
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            "The city was founded in 1520 by Pedrarias.",
            [("Founded in 1520", "Founded in 1519")],
        )
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [CorrectionEdit(rows[0].id, "en", "founded in 1520", "founded in 1519")],
            test_db,
        )
        assert result["corrected_claim_ids"] == [rows[0].id]
        assert result["failed"] == []
        assert "founded in 1519" in research.content
        assert rows[0].verdict == FactCheckVerdict.CORRECTED.value
        # The correction/citation stay on the row (terminal green state).
        assert rows[0].correction == "Founded in 1519"

    def test_stale_find_fails_and_stays_inaccurate(self, test_db, category_factory):
        research, rows = _research_with_claims(
            test_db, category_factory, "Content without the snippet.", [("c", "fix")]
        )
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [CorrectionEdit(rows[0].id, "en", "not there anymore", "x")],
            test_db,
        )
        assert result["corrected_claim_ids"] == []
        assert result["failed"] == [
            (rows[0].id, "en", "FIND snippet no longer matches the content")
        ]
        assert rows[0].verdict == FactCheckVerdict.INACCURATE.value
        assert research.content == "Content without the snippet."

    def test_edit_with_protocol_markers_rejected(self, test_db, category_factory):
        """#433: apply re-validates the round-tripped payload, so a REPLACE
        carrying raw protocol text never reaches the content."""
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            "The city was founded in 1520 by Pedrarias.",
            [("Founded in 1520", "Founded in 1519")],
        )
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [
                CorrectionEdit(
                    rows[0].id,
                    "en",
                    "founded in 1520",
                    "founded in 1519\nEDIT: 99\nFIND:\nx\nREPLACE:\ny",
                )
            ],
            test_db,
        )
        assert result["corrected_claim_ids"] == []
        assert result["failed"] == [(rows[0].id, "en", "edit contains raw edit-protocol markers")]
        assert research.content == "The city was founded in 1520 by Pedrarias."
        assert rows[0].verdict == FactCheckVerdict.INACCURATE.value

    def test_non_correctable_claim_rejected(self, test_db, category_factory):
        research, _ = _research_with_claims(test_db, category_factory, "text", [("c", "fix")])
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [CorrectionEdit(99999, "en", "text", "texto")],
            test_db,
        )
        assert result["corrected_claim_ids"] == []
        assert result["failed"] == [(99999, "en", "claim is not correctable")]

    def test_article_requires_both_languages(self, test_db, category_factory):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="s", category_id=category.id, status="APPROVED", point_of_view="n"
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="r", status="APPROVED")
        test_db.add(research)
        test_db.flush()
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="a",
            outline="o",
            outline_status="APPROVED",
            status="PENDING",
            content="Founded in 1520.",
            content_es="Fundada en 1520.",
        )
        test_db.add(article)
        test_db.flush()
        row = _inaccurate_claim(FactCheckSubject.ARTICLE.value, article.id, "c", "fix")
        test_db.add(row)
        test_db.flush()

        # EN edit only — the ES half is missing, so nothing is applied.
        result = apply_corrections(
            FactCheckSubject.ARTICLE.value,
            article,
            [CorrectionEdit(row.id, "en", "in 1520", "in 1519")],
            test_db,
        )
        assert result["corrected_claim_ids"] == []
        assert result["failed"] == [(row.id, "es", "no edit provided for this language")]
        assert article.content == "Founded in 1520."

        # Both languages — applied together, claim flips.
        result = apply_corrections(
            FactCheckSubject.ARTICLE.value,
            article,
            [
                CorrectionEdit(row.id, "en", "in 1520", "in 1519"),
                CorrectionEdit(row.id, "es", "en 1520", "en 1519"),
            ],
            test_db,
        )
        assert result["corrected_claim_ids"] == [row.id]
        assert article.content == "Founded in 1519."
        assert article.content_es == "Fundada en 1519."
        assert row.verdict == FactCheckVerdict.CORRECTED.value

    def test_article_without_spanish_needs_english_only(self, test_db, category_factory):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="s", category_id=category.id, status="APPROVED", point_of_view="n"
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="r", status="APPROVED")
        test_db.add(research)
        test_db.flush()
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="a",
            outline="o",
            outline_status="APPROVED",
            status="PENDING",
            content="Founded in 1520.",
        )
        test_db.add(article)
        test_db.flush()
        row = _inaccurate_claim(FactCheckSubject.ARTICLE.value, article.id, "c", "fix")
        test_db.add(row)
        test_db.flush()

        result = apply_corrections(
            FactCheckSubject.ARTICLE.value,
            article,
            [CorrectionEdit(row.id, "en", "in 1520", "in 1519")],
            test_db,
        )
        assert result["corrected_claim_ids"] == [row.id]
        assert article.content == "Founded in 1519."
        assert article.content_es is None  # untranslated article stays untranslated

    def test_suggestion_edits_match_across_fields(self, test_db, category_factory):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="The 1520 founding of Panama Viejo",
            category_id=category.id,
            status="APPROVED",
            main_topic="Urban history",
            sub_topics=["Founding date 1520", "Morgan's raid"],
            point_of_view="narrative",
        )
        test_db.add(suggestion)
        test_db.flush()
        row_title = _inaccurate_claim(FactCheckSubject.SUGGESTION.value, suggestion.id, "t", "1519")
        row_topic = _inaccurate_claim(FactCheckSubject.SUGGESTION.value, suggestion.id, "s", "1519")
        test_db.add_all([row_title, row_topic])
        test_db.flush()

        result = apply_corrections(
            FactCheckSubject.SUGGESTION.value,
            suggestion,
            [
                CorrectionEdit(row_title.id, "en", "The 1520 founding", "The 1519 founding"),
                CorrectionEdit(row_topic.id, "en", "Founding date 1520", "Founding date 1519"),
            ],
            test_db,
        )
        assert sorted(result["corrected_claim_ids"]) == sorted([row_title.id, row_topic.id])
        assert suggestion.title == "The 1519 founding of Panama Viejo"
        assert suggestion.sub_topics == ["Founding date 1519", "Morgan's raid"]

    @pytest.mark.parametrize(
        ("find", "replace", "cleared"),
        [
            ("The 1520 founding", "The 1519 founding", True),  # title
            ("Urban history", "Urban history of 1519", True),  # main topic
            ("Founding date 1520", "Founding date 1519", False),  # sub-topic only
        ],
    )
    def test_suggestion_correction_clears_dedup_vector(
        self, test_db, category_factory, find, replace, cleared
    ):
        """The #630 dedup vector embeds title + main topic: a change there drops it."""
        from sqlalchemy import select, update

        table = ArticleSuggestion.__table__
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="The 1520 founding of Panama Viejo",
            category_id=category.id,
            status="APPROVED",
            main_topic="Urban history",
            sub_topics=["Founding date 1520"],
            point_of_view="narrative",
        )
        test_db.add(suggestion)
        test_db.flush()
        test_db.execute(
            update(table).where(table.c.id == suggestion.id).values(embedding=[1.0] + [0.0] * 1023)
        )
        row = _inaccurate_claim(FactCheckSubject.SUGGESTION.value, suggestion.id, "t", "1519")
        test_db.add(row)
        test_db.flush()

        result = apply_corrections(
            FactCheckSubject.SUGGESTION.value,
            suggestion,
            [CorrectionEdit(row.id, "en", find, replace)],
            test_db,
        )
        assert result["corrected_claim_ids"] == [row.id]
        stored = test_db.execute(
            select(table.c.embedding).where(table.c.id == suggestion.id)
        ).scalar_one()
        assert (stored is None) is cleared

    def test_suggestion_ambiguous_across_fields_fails(self, test_db, category_factory):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="Panama Viejo in 1520",
            category_id=category.id,
            status="APPROVED",
            main_topic="Panama Viejo in 1520",
            point_of_view="narrative",
        )
        test_db.add(suggestion)
        test_db.flush()
        row = _inaccurate_claim(FactCheckSubject.SUGGESTION.value, suggestion.id, "t", "1519")
        test_db.add(row)
        test_db.flush()

        result = apply_corrections(
            FactCheckSubject.SUGGESTION.value,
            suggestion,
            [CorrectionEdit(row.id, "en", "in 1520", "in 1519")],
            test_db,
        )
        assert result["corrected_claim_ids"] == []
        assert result["failed"] == [(row.id, "en", "FIND snippet occurs 2 times in the content")]

    def test_sequential_edits_same_claim(self, test_db, category_factory):
        """Two edits for one claim apply in order, each validated at its turn."""
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            "Founded in 1520. Destroyed in 1670.",
            [("dates wrong", "1519 and 1671")],
        )
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [
                CorrectionEdit(rows[0].id, "en", "in 1520", "in 1519"),
                CorrectionEdit(rows[0].id, "en", "in 1670", "in 1671"),
            ],
            test_db,
        )
        assert result["corrected_claim_ids"] == [rows[0].id]
        assert research.content == "Founded in 1519. Destroyed in 1671."

    def test_skipped_entry_resolves_as_already_correct(self, test_db, category_factory):
        """A skipped edit (content never asserts the inaccuracy) flips the
        claim to CORRECTED without touching the content."""
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            "Betín was an engineer involved in the fortifications.",
            [("Betín was the chief engineer", "He was an engineer, not the chief")],
        )
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [CorrectionEdit(rows[0].id, "en", "", "", EDIT_SKIPPED, "content matches correction")],
            test_db,
        )
        assert result["corrected_claim_ids"] == []
        assert result["already_correct_claim_ids"] == [rows[0].id]
        assert result["failed"] == []
        assert research.content == "Betín was an engineer involved in the fortifications."
        assert rows[0].verdict == FactCheckVerdict.CORRECTED.value

    def test_mixed_skip_and_edit_claims(self, test_db, category_factory):
        research, rows = _research_with_claims(
            test_db,
            category_factory,
            "Founded in 1520. Betín was an engineer.",
            [("Founded in 1520", "Founded in 1519"), ("chief engineer", "an engineer")],
        )
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [
                CorrectionEdit(rows[0].id, "en", "in 1520", "in 1519"),
                CorrectionEdit(rows[1].id, "en", "", "", EDIT_SKIPPED, "already correct"),
            ],
            test_db,
        )
        assert result["corrected_claim_ids"] == [rows[0].id]
        assert result["already_correct_claim_ids"] == [rows[1].id]
        assert research.content == "Founded in 1519. Betín was an engineer."
        assert rows[0].verdict == FactCheckVerdict.CORRECTED.value
        assert rows[1].verdict == FactCheckVerdict.CORRECTED.value

    def test_skipped_non_correctable_claim_rejected(self, test_db, category_factory):
        research, _ = _research_with_claims(test_db, category_factory, "text", [("c", "fix")])
        result = apply_corrections(
            FactCheckSubject.RESEARCH.value,
            research,
            [CorrectionEdit(99999, "en", "", "", EDIT_SKIPPED, "already correct")],
            test_db,
        )
        assert result["already_correct_claim_ids"] == []
        assert result["failed"] == [(99999, "en", "claim is not correctable")]

    def test_article_skip_one_language_edit_other(self, test_db, category_factory):
        """EN already correct (skipped) + ES needs the edit — the claim flips
        as corrected and only the Spanish content changes."""
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="s", category_id=category.id, status="APPROVED", point_of_view="n"
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="r", status="APPROVED")
        test_db.add(research)
        test_db.flush()
        article = Article(
            research_id=research.id,
            category_id=category.id,
            title="a",
            outline="o",
            outline_status="APPROVED",
            status="PENDING",
            content="Founded in 1519.",
            content_es="Fundada en 1520.",
        )
        test_db.add(article)
        test_db.flush()
        row = _inaccurate_claim(FactCheckSubject.ARTICLE.value, article.id, "c", "fix")
        test_db.add(row)
        test_db.flush()

        result = apply_corrections(
            FactCheckSubject.ARTICLE.value,
            article,
            [
                CorrectionEdit(row.id, "en", "", "", EDIT_SKIPPED, "EN already says 1519"),
                CorrectionEdit(row.id, "es", "en 1520", "en 1519"),
            ],
            test_db,
        )
        assert result["corrected_claim_ids"] == [row.id]
        assert result["already_correct_claim_ids"] == []
        assert article.content == "Founded in 1519."
        assert article.content_es == "Fundada en 1519."
        assert row.verdict == FactCheckVerdict.CORRECTED.value


# --- CORRECTED constraint / KB behavior -------------------------------------------


class TestCorrectedVerdict:
    def test_corrected_row_accepted_by_check_constraint(self, test_db):
        test_db.add(
            _inaccurate_claim(
                FactCheckSubject.RESEARCH.value,
                1,
                "x",
                "fix",
                verdict=FactCheckVerdict.CORRECTED.value,
            )
        )
        test_db.flush()  # must not raise

    def test_corrected_rows_not_fed_to_kb(self, test_db):
        from app.models.verified_claim import VerifiedClaim
        from app.services.fact_check import upsert_verified_claims

        row = _inaccurate_claim(
            FactCheckSubject.RESEARCH.value,
            1,
            "x",
            "fix",
            verdict=FactCheckVerdict.CORRECTED.value,
        )
        test_db.add(row)
        test_db.flush()
        assert upsert_verified_claims([row], test_db) == 0
        assert test_db.query(VerifiedClaim).count() == 0

    def test_migration_0022_matches_enum(self):
        """The 0022 migration's value set agrees with FactCheckVerdict."""
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "alembic"
            / "versions"
            / "0022_corrected_verdict.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0022", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert set(module._NEW_VALUES) == set(FactCheckVerdict.values())


# --- Endpoints --------------------------------------------------------------------


class TestCorrectionEndpoints:
    def _research(self, test_db, category_factory, content="The city was founded in 1520."):
        return _research_with_claims(
            test_db, category_factory, content, [("Founded in 1520", "Founded in 1519")]
        )

    @patch("app.services.llm.get_client")
    def test_preview_returns_edits(self, mock_get_client, test_client, test_db, category_factory):
        research, rows = self._research(test_db, category_factory)
        test_db.commit()
        mock_get_client.return_value = _make_client(
            _msg(_edit_block(rows[0].id, "founded in 1520", "founded in 1519"))
        )
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/preview",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["correctable_claims"] == 1
        assert data["edits"] == [
            {
                "claim_id": rows[0].id,
                "language": "en",
                "find": "founded in 1520",
                "replace": "founded in 1519",
                "status": "ok",
                "reason": None,
            }
        ]

    def test_preview_without_correctable_claims_400(self, test_client, test_db, category_factory):
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="s", category_id=category.id, status="APPROVED", point_of_view="n"
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="r", status="APPROVED")
        test_db.add(research)
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/preview",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 400
        assert "run a fact-check first" in response.json()["detail"]

    def test_preview_pending_is_400_but_resolved_clean_is_success(
        self, test_client, test_db, category_factory
    ):
        """Verdicts still in flight stay a 400 (park for a human / retry), but
        a resolved-clean check previews as an empty SUCCESS so the driver's
        corrections task converges DONE (#481 follow-up, 2026-09-03 drive)."""
        category = category_factory()
        suggestion = ArticleSuggestion(
            title="s", category_id=category.id, status="APPROVED", point_of_view="n"
        )
        test_db.add(suggestion)
        test_db.flush()
        research = Research(suggestion_id=suggestion.id, content="r", status="APPROVED")
        test_db.add(research)
        test_db.flush()
        url = f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/preview"
        claim = FactCheckClaim(
            subject_type="RESEARCH",
            subject_id=research.id,
            claim_text="c",
            verdict="NEEDS_DEEP_RESEARCH",
        )
        test_db.add(claim)
        test_db.commit()

        response = test_client.post(url, headers={"Authorization": "Bearer x"})
        assert response.status_code == 400
        assert response.json()["detail"].startswith("Fact-check verdicts still pending (1 ")

        # Resolved clean: SUCCESS with nothing to apply, not an error — the
        # driver's corrections task must converge DONE on a clean subject
        # (the 2026-09-03 drive burned its retry budget 400ing here), and the
        # empty edit list piped to apply is an equal no-op success.
        claim.verdict = "VERIFIED"
        test_db.commit()
        response = test_client.post(url, headers={"Authorization": "Bearer x"})
        assert response.status_code == 200
        body = response.json()
        assert body["correctable_claims"] == 0
        assert body["edits"] == []

        apply_url = url.replace("/preview", "/apply")
        response = test_client.post(
            apply_url, json={"edits": []}, headers={"Authorization": "Bearer x"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["corrected_claim_ids"] == []
        assert body["failed"] == []
        assert body["counts"] == {"VERIFIED": 1}

    def test_preview_unknown_subject_400_and_missing_404(self, test_client):
        response = test_client.post(
            "/api/v1/admin/dashboard/fact-check/podcast/1/corrections/preview",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 400
        response = test_client.post(
            "/api/v1/admin/dashboard/fact-check/research/999999/corrections/preview",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 404

    def test_preview_409_while_job_running(self, test_client, test_db, category_factory):
        research, _ = self._research(test_db, category_factory)
        test_db.add(
            CrawlJob(
                job_type=CrawlJobType.FACT_CHECK.value,
                params={"subject_type": "RESEARCH", "subject_id": research.id},
            )
        )
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/preview",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 409

    def test_apply_writes_content_and_counts(self, test_client, test_db, category_factory):
        research, rows = self._research(test_db, category_factory)
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/apply",
            headers={"Authorization": "Bearer x"},
            json={
                "edits": [
                    {
                        "claim_id": rows[0].id,
                        "language": "en",
                        "find": "founded in 1520",
                        "replace": "founded in 1519",
                    }
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["corrected_claim_ids"] == [rows[0].id]
        assert data["failed"] == []
        assert data["counts"] == {"CORRECTED": 1}
        test_db.expire_all()
        assert "founded in 1519" in test_db.get(Research, research.id).content

    def test_apply_skipped_entry_resolves_claim(self, test_client, test_db, category_factory):
        research, rows = self._research(
            test_db, category_factory, content="The city was founded in 1519."
        )
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/apply",
            headers={"Authorization": "Bearer x"},
            json={
                "edits": [
                    {
                        "claim_id": rows[0].id,
                        "language": "en",
                        "find": "",
                        "replace": "",
                        "status": "skipped",
                    }
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["corrected_claim_ids"] == []
        assert data["already_correct_claim_ids"] == [rows[0].id]
        assert data["failed"] == []
        assert data["counts"] == {"CORRECTED": 1}
        test_db.expire_all()
        assert test_db.get(Research, research.id).content == "The city was founded in 1519."

    def test_apply_rejects_unknown_status(self, test_client, test_db, category_factory):
        research, rows = self._research(test_db, category_factory)
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/apply",
            headers={"Authorization": "Bearer x"},
            json={
                "edits": [
                    {
                        "claim_id": rows[0].id,
                        "language": "en",
                        "find": "x",
                        "replace": "y",
                        "status": "not_found",
                    }
                ]
            },
        )
        assert response.status_code == 422

    def test_apply_reports_failures(self, test_client, test_db, category_factory):
        research, rows = self._research(test_db, category_factory)
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/apply",
            headers={"Authorization": "Bearer x"},
            json={
                "edits": [
                    {
                        "claim_id": rows[0].id,
                        "language": "en",
                        "find": "stale snippet",
                        "replace": "x",
                    }
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["corrected_claim_ids"] == []
        assert data["failed"] == [
            {
                "claim_id": rows[0].id,
                "language": "en",
                "reason": "FIND snippet no longer matches the content",
            }
        ]
        assert data["counts"] == {"INACCURATE": 1}

    def test_apply_empty_edits_is_a_no_op_success(self, test_client, test_db, category_factory):
        """The driver pipes the previewed edits verbatim, so a clean preview's
        empty list must apply as a no-op success, not a 400."""
        research, _ = self._research(test_db, category_factory)
        test_db.commit()
        response = test_client.post(
            f"/api/v1/admin/dashboard/fact-check/research/{research.id}/corrections/apply",
            headers={"Authorization": "Bearer x"},
            json={"edits": []},
        )
        assert response.status_code == 200
        assert response.json()["corrected_claim_ids"] == []
