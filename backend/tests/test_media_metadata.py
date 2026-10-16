"""Tests for app.services.media_metadata (#589) — no network, LLM mocked."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.services.llm import HAIKU_MODEL
from app.services.media_metadata import (
    MediaTextInput,
    alt_is_default,
    build_item_text,
    generate_search_metadata,
    merge_search_terms,
    parse_search_terms_response,
    try_generate_search_metadata,
)


def _message(text: str):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class TestMergeSearchTerms:
    def test_dedupes_case_insensitively_keeping_first_order(self):
        assert (
            merge_search_terms("Casco, wall , ", "casco antiguo, WALL, muralla")
            == "Casco, wall, casco antiguo, muralla"
        )

    def test_none_and_empty_parts(self):
        assert merge_search_terms(None, "", " , ") is None
        assert merge_search_terms(None, "a") == "a"


class TestParseResponse:
    def test_plain_array(self):
        assert parse_search_terms_response('[{"id": 1, "search_terms": "a"}]') == [
            {"id": 1, "search_terms": "a"}
        ]

    def test_fenced_json(self):
        text = '```json\n[{"id": 2, "search_terms": "b", "alt_text": "x"}]\n```'
        assert parse_search_terms_response(text)[0]["id"] == 2

    def test_regex_repair_of_truncated_array(self):
        text = '[{"id": 1, "search_terms": "a"}, {"id": 2, "search_terms": "b"}, {"id": 3, "sea'
        out = parse_search_terms_response(text)
        assert [o["id"] for o in out] == [1, 2]

    def test_unrepairable_raises(self):
        with pytest.raises(ValueError):
            parse_search_terms_response("no json here")


class TestBuildItemText:
    def test_includes_credit_and_keywords(self):
        text = build_item_text(
            MediaTextInput(
                id=7,
                title="Wall",
                source="LOCAL",
                description="d",
                credit="Jane",
                keywords="casco",
                categories=["c1"],
                tags=["t1"],
                relevance_notes="r",
            )
        )
        assert text.startswith("[ID=7] Title: Wall | Description: d | Credit: Jane")
        assert "Keywords: casco" in text and "Categories: c1" in text and "Tags: t1" in text
        assert text.endswith("Source: LOCAL")


class TestGenerate:
    def test_calls_haiku_with_feature_and_filters_ids(self):
        items = [MediaTextInput(id=1, title="A", source="LOCAL")]
        reply = (
            '[{"id": 1, "search_terms": " a, b ", "alt_text": " Alt "},'
            ' {"id": 99, "search_terms": "x"}]'
        )
        with patch(
            "app.services.media_metadata.create_message", return_value=_message(reply)
        ) as cm:
            out = generate_search_metadata(items, feature="media_search_terms")
        kwargs = cm.call_args.kwargs
        assert kwargs["model"] == HAIKU_MODEL
        assert kwargs["feature"] == "media_search_terms"
        assert kwargs["reject_on_truncation"] is False
        assert out == {1: {"search_terms": "a, b", "alt_text": "Alt"}}

    def test_blank_terms_are_omitted(self):
        items = [MediaTextInput(id=1, title="A", source="LOCAL")]
        with patch(
            "app.services.media_metadata.create_message",
            return_value=_message('[{"id": 1, "search_terms": "  "}]'),
        ):
            assert generate_search_metadata(items, feature="f") == {}


class TestTryGenerate:
    ITEM = MediaTextInput(id=5, title="A", source="LOCAL")

    def test_no_key_returns_none_without_calling(self):
        with (
            patch(
                "app.services.media_metadata.get_settings",
                return_value=MagicMock(anthropic_api_key=""),
            ),
            patch("app.services.media_metadata.create_message") as cm,
        ):
            assert try_generate_search_metadata(self.ITEM) is None
        cm.assert_not_called()

    def test_failure_returns_none(self):
        with (
            patch(
                "app.services.media_metadata.get_settings",
                return_value=MagicMock(anthropic_api_key="k"),
            ),
            patch("app.services.media_metadata.create_message", side_effect=RuntimeError("boom")),
        ):
            assert try_generate_search_metadata(self.ITEM) is None

    def test_success_returns_item_result(self):
        with (
            patch(
                "app.services.media_metadata.get_settings",
                return_value=MagicMock(anthropic_api_key="k"),
            ),
            patch(
                "app.services.media_metadata.create_message",
                return_value=_message('[{"id": 5, "search_terms": "a", "alt_text": "b"}]'),
            ),
        ):
            assert try_generate_search_metadata(self.ITEM) == {
                "search_terms": "a",
                "alt_text": "b",
            }


class TestAltIsDefault:
    def test_cases(self):
        assert alt_is_default(None, "T")
        assert alt_is_default("  ", "T")
        assert alt_is_default("T", "Old", "T")
        assert not alt_is_default("Custom alt", "T")
