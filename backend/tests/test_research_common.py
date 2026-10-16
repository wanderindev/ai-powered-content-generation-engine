"""Unit tests for app.services.research_common.validate_research_document.

Behaviors verified per the brief:
- Word-count gate (< 4000 short-circuits).
- References regex matches multiple synonyms (Sources/References/Bibliography/Works Cited/Citations).
- References auto-fix on missing section: appends LLM-generated content
  iff response starts with "## References" or "## Sources".
- Sub-topic coverage: skipped when no sub_topics; parses well-formed JSON;
  parses code-fenced JSON; falls back to {} on malformed JSON; threshold is `>= 2`.
- `valid` field is the conjunction of refs_ok AND subtopic_ok.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from app.services.llm import parse_llm_json
from app.services.research_common import read_upload_bytes, validate_research_document
from fastapi import HTTPException
from starlette.datastructures import UploadFile


def _mock_msg(text: str):
    """Build a MagicMock message with `.content[0].text == text`."""
    return MagicMock(content=[MagicMock(text=text)])


def _subtopics_then_refs_client(subtopics_msg, refs_msg):
    """Client for a validate call that also auto-fixes references.

    The sub-topic check (cap 2000) is a plain ``create``; the references call
    (cap 8000 since #610) is at the streaming threshold, so it is streamed.
    """
    client = MagicMock()
    client.messages.create.side_effect = [subtopics_msg]
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = refs_msg
    cm.__exit__.return_value = False
    client.messages.stream.return_value = cm
    return client


def _make_body_with_refs(refs_header: str = "## References") -> str:
    """Build a 4001-word body that includes the given references header."""
    body = "word " * 4001
    return body + f"\n\n{refs_header}\n- Source 1\n"


def _make_body_without_refs() -> str:
    """Build a 4001-word body that includes no references-style header."""
    return "word " * 4001


# --- Word-count gate ---


class TestWordCountGate:
    @patch("app.services.llm.get_client")
    def test_under_4000_words_short_circuits(self, mock_get_client):
        # Mock client should never be called.
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        content = "word " * 100  # 100 words
        result = validate_research_document(content, sub_topics=["A: a"])

        assert result["valid"] is False
        assert result["word_count"] == 100
        assert result["checks"]["word_count"] is False
        mock_client.messages.create.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_4000_words_minimum_still_fails(self, mock_get_client):
        # The brief says < 4000 returns valid=False. 4000 words exactly is the
        # boundary — verify the contract: anything below 4000 fails.
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        content = "word " * 3999
        result = validate_research_document(content, sub_topics=["A: a"])

        assert result["valid"] is False
        assert result["checks"]["word_count"] is False
        mock_client.messages.create.assert_not_called()


# --- References regex ---


class TestReferencesRegex:
    @patch("app.services.llm.get_client")
    def test_references_present_no_autofix_call(self, mock_get_client):
        # Sub-topic coverage call WILL happen but auto-fix MUST NOT.
        mock_client = MagicMock()
        # First (and only) call is sub-topic coverage.
        mock_client.messages.create.return_value = _mock_msg(
            json.dumps({"subtopics_found": 2, "subtopics_total": 2})
        )
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["checks"]["references"] is True
        # Only the sub-topic coverage call — no auto-fix references call.
        assert mock_client.messages.create.call_count == 1

    @pytest.mark.parametrize(
        "header",
        [
            "## Sources",
            "## Bibliography",
            "## Works Cited",
            "## Citations",
            "## references",  # case-insensitive
            "### Sources",  # 1-3 levels of heading
        ],
    )
    @patch("app.services.llm.get_client")
    def test_references_regex_matches_synonyms(self, mock_get_client, header):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_msg(
            json.dumps({"subtopics_found": 2, "subtopics_total": 2})
        )
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs(header)
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["checks"]["references"] is True


# --- References auto-fix ---


class TestReferencesAutoFix:
    @patch("app.services.llm.get_client")
    def test_autofix_appends_generated_references(self, mock_get_client):
        mock_client = MagicMock()
        # Two calls: sub-topic coverage, then auto-fix.
        mock_client.messages.create.side_effect = [
            _mock_msg(json.dumps({"subtopics_found": 2, "subtopics_total": 2})),
            _mock_msg("## References\n- Generated Source 1\n- Generated Source 2"),
        ]
        mock_get_client.return_value = mock_client

        content = _make_body_without_refs()
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["checks"]["references"] is True
        assert result["content"].endswith(
            "\n\n---\n\n## References\n- Generated Source 1\n- Generated Source 2"
        )

    @patch("app.services.llm.get_client")
    def test_autofix_accepts_sources_header(self, mock_get_client):
        mock_get_client.return_value = _subtopics_then_refs_client(
            _mock_msg(json.dumps({"subtopics_found": 2, "subtopics_total": 2})),
            _mock_msg("## Sources\n- Generated Source 1"),
        )

        content = _make_body_without_refs()
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["checks"]["references"] is True
        assert "## Sources" in result["content"]

    @patch("app.services.llm.get_client")
    def test_autofix_rejects_non_conforming_response(self, mock_get_client):
        mock_get_client.return_value = _subtopics_then_refs_client(
            _mock_msg(json.dumps({"subtopics_found": 2, "subtopics_total": 2})),
            _mock_msg("Sorry, no citations were found in the document."),
        )

        content = _make_body_without_refs()
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["checks"]["references"] is False
        # Original content returned unchanged.
        assert result["content"] == content


# --- Sub-topic coverage ---


class TestSubTopicCoverage:
    @patch("app.services.llm.get_client")
    def test_no_subtopics_skips_llm_call(self, mock_get_client):
        # With no sub-topics, the only LLM call should be auto-fix (since
        # the body has no refs section). To isolate, give it a refs section.
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=None)

        assert result["checks"]["subtopic_coverage"]["passed"] is True
        assert result["checks"]["subtopic_coverage"]["found"] == 0
        assert result["checks"]["subtopic_coverage"]["total"] == 0
        # No LLM calls at all (refs section present + no sub-topics).
        mock_client.messages.create.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_empty_subtopics_list_skips_llm_call(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=[])

        assert result["checks"]["subtopic_coverage"]["passed"] is True
        assert result["checks"]["subtopic_coverage"]["found"] == 0
        assert result["checks"]["subtopic_coverage"]["total"] == 0
        mock_client.messages.create.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_valid_json_response_3_of_5(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_msg(
            json.dumps({"subtopics_found": 3, "subtopics_total": 5})
        )
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(
            content, sub_topics=["A: a", "B: b", "C: c", "D: d", "E: e"]
        )

        cov = result["checks"]["subtopic_coverage"]
        assert cov["found"] == 3
        assert cov["total"] == 5
        assert cov["passed"] is True

    @patch("app.services.llm.get_client")
    def test_one_found_fails_threshold(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_msg(
            json.dumps({"subtopics_found": 1, "subtopics_total": 5})
        )
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(
            content, sub_topics=["A: a", "B: b", "C: c", "D: d", "E: e"]
        )

        cov = result["checks"]["subtopic_coverage"]
        assert cov["found"] == 1
        assert cov["passed"] is False

    @patch("app.services.llm.get_client")
    def test_code_fenced_json_response_parsed(self, mock_get_client):
        mock_client = MagicMock()
        # Wrap JSON in markdown code fences.
        fenced = "```json\n" + json.dumps({"subtopics_found": 4, "subtopics_total": 4}) + "\n```"
        mock_client.messages.create.return_value = _mock_msg(fenced)
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        cov = result["checks"]["subtopic_coverage"]
        assert cov["found"] == 4
        assert cov["total"] == 4
        assert cov["passed"] is True

    @patch("app.services.llm.get_client")
    def test_malformed_json_falls_back_to_empty(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_msg("not even close to JSON")
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        cov = result["checks"]["subtopic_coverage"]
        assert cov["found"] == 0
        assert cov["total"] == 0
        assert cov["passed"] is False


# --- parse_llm_json helper (issue #77) ---


def _block(text: str, block_type: str = "text"):
    """Build a mock content block with an explicit `.type` and `.text`."""
    block = MagicMock()
    block.type = block_type
    block.text = text
    return block


def _msg(*blocks):
    """Build a mock Anthropic message from content blocks."""
    msg = MagicMock()
    msg.content = list(blocks)
    return msg


class TestParseLlmJson:
    def test_empty_content_raises_value_error(self):
        with pytest.raises(ValueError, match="no content blocks"):
            parse_llm_json(_msg())

    def test_parses_plain_json(self):
        result = parse_llm_json(_msg(_block('{"a": 1, "b": [2, 3]}')))
        assert result == {"a": 1, "b": [2, 3]}

    def test_parses_code_fenced_json(self):
        fenced = "```json\n" + json.dumps({"x": 42}) + "\n```"
        result = parse_llm_json(_msg(_block(fenced)))
        assert result == {"x": 42}

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError, match="malformed JSON"):
            parse_llm_json(_msg(_block("not json at all")))

    def test_missing_required_key_raises_value_error(self):
        with pytest.raises(ValueError, match="missing required key"):
            parse_llm_json(_msg(_block('{"other": 1}')), required_key="articles")

    def test_required_key_present_returns_dict(self):
        result = parse_llm_json(_msg(_block('{"articles": []}')), required_key="articles")
        assert result == {"articles": []}

    def test_skips_thinking_block_and_uses_text(self):
        msg = _msg(
            _block("internal reasoning", block_type="thinking"),
            _block('{"suggestions": []}'),
        )
        result = parse_llm_json(msg, required_key="suggestions")
        assert result == {"suggestions": []}

    def test_list_result_with_required_key_raises(self):
        # A JSON list cannot contain a dict key — required_key must fail.
        with pytest.raises(ValueError, match="missing required key"):
            parse_llm_json(_msg(_block("[1, 2, 3]")), required_key="articles")


# --- valid is a conjunction of refs_ok AND subtopic_ok ---


class TestValidConjunction:
    """`valid` should be the AND of references_ok and subtopic_ok."""

    @patch("app.services.llm.get_client")
    def test_both_true_valid_true(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_msg(
            json.dumps({"subtopics_found": 2, "subtopics_total": 2})
        )
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["valid"] is True

    @patch("app.services.llm.get_client")
    def test_refs_true_subtopics_false_valid_false(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_msg(
            json.dumps({"subtopics_found": 0, "subtopics_total": 2})
        )
        mock_get_client.return_value = mock_client

        content = _make_body_with_refs("## References")
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["valid"] is False

    @patch("app.services.llm.get_client")
    def test_refs_false_subtopics_true_valid_false(self, mock_get_client):
        # First: sub-topic coverage. Second: auto-fix rejected.
        mock_get_client.return_value = _subtopics_then_refs_client(
            _mock_msg(json.dumps({"subtopics_found": 2, "subtopics_total": 2})),
            _mock_msg("no references could be extracted"),
        )

        content = _make_body_without_refs()
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["valid"] is False

    @patch("app.services.llm.get_client")
    def test_both_false_valid_false(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_msg(json.dumps({"subtopics_found": 0, "subtopics_total": 2})),
            _mock_msg("no references could be extracted"),
        ]
        mock_get_client.return_value = mock_client

        content = _make_body_without_refs()
        result = validate_research_document(content, sub_topics=["A: a", "B: b"])

        assert result["valid"] is False


# --- Upload size-cap helpers (#89) ---


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="x.bin", file=io.BytesIO(data))


class TestReadUploadBytes:
    def test_happy(self):
        assert read_upload_bytes(_upload(b"hello"), max_bytes=1024) == b"hello"

    def test_at_limit_ok(self):
        # Exactly at the cap is allowed (cap is inclusive).
        assert read_upload_bytes(_upload(b"a" * 10), max_bytes=10) == b"a" * 10

    def test_oversize_413(self):
        with pytest.raises(HTTPException) as exc:
            read_upload_bytes(_upload(b"a" * 11), max_bytes=10)
        assert exc.value.status_code == 413

    def test_empty_400(self):
        with pytest.raises(HTTPException) as exc:
            read_upload_bytes(_upload(b""), max_bytes=10)
        assert exc.value.status_code == 400
