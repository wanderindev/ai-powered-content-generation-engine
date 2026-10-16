"""Every Haiku call site on Haiku 5.5 (#610).

Haiku 5.5 runs adaptive thinking by default, so a real response starts with a
``thinking`` block. Each test hands its feature a response shaped like that
(thinking block first, then the text) and checks that the feature reads the
text block, and that the request carries the explicit medium effort, a cap with
thinking headroom, and no refusal-fallback opt-in (Haiku 5.5 has none).
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.models.media_candidate import CandidateStatus, MediaCandidate
from app.models.verified_claim import VerifiedClaim
from app.services.article_generation import generate_excerpt_and_summary
from app.services.fact_check import (
    ExtractedClaim,
    _normalize,
    extract_claims,
    match_claims_against_kb,
)
from app.services.llm import HAIKU_MODEL, first_text
from app.services.media_metadata import MediaTextInput, generate_search_metadata
from app.services.media_scoring import score_candidates
from app.services.research_common import (
    _generate_references_section,
    validate_research_document,
)
from app.services.research_summary import generate_research_summary

# Floor for "thinking headroom": a ~40-word excerpt measured 389 output tokens
# at medium effort on Haiku 5.5, so nothing below this survives thinking.
_MIN_CAP = 2000


def _thinking_first(text: str, stop_reason: str = "end_turn"):
    """A Haiku 5.5-shaped response: thinking block at index 0, text after."""
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="sig"),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        usage=None,
    )


def _client(*messages):
    """Mock client serving `messages` in order over both create and stream."""
    queue = list(messages)
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        return queue.pop(0)

    def _stream(**kwargs):
        calls.append(kwargs)
        cm = MagicMock()
        cm.__enter__.return_value.get_final_message.return_value = queue.pop(0)
        cm.__exit__.return_value = False
        return cm

    client = MagicMock()
    client.messages.create.side_effect = _create
    client.messages.stream.side_effect = _stream
    client.calls = calls
    return client


def _assert_haiku_request(kwargs: dict) -> None:
    assert kwargs["model"] == HAIKU_MODEL == "claude-haiku-5-5"
    assert kwargs["output_config"] == {"effort": "medium"}
    assert kwargs["max_tokens"] >= _MIN_CAP
    assert "thinking" not in kwargs  # adaptive is the default; effort steers it
    assert "extra_body" not in kwargs  # no server-side fallback on Haiku 5.5
    assert "extra_headers" not in kwargs


@patch("app.services.llm.get_client")
def test_extract_claims(mock_get_client):
    client = _client(_thinking_first("CLAIM: DATE | Panama Viejo was founded in 1519"))
    mock_get_client.return_value = client

    claims = extract_claims("Panama Viejo was founded in 1519.")

    assert [c.text for c in claims] == ["Panama Viejo was founded in 1519"]
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_kb_match(mock_get_client, test_db):
    text = "The city of Panama Viejo was founded in the year 1519"
    kb = VerifiedClaim(
        claim_key=_normalize(text),
        claim_text=text,
        claim_type="DATE",
        verdict="VERIFIED",
        citation="https://kb.org",
        source_method="WEB_SEARCH",
    )
    test_db.add(kb)
    test_db.flush()
    client = _client(_thinking_first(f"MATCH: 1 | {kb.id}"))
    mock_get_client.return_value = client

    matches = match_claims_against_kb(
        [ExtractedClaim("Panama Viejo was founded in 1519", "DATE")], test_db
    )

    assert matches == {0: kb}
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_media_scoring(mock_get_client, test_db):
    candidate = MediaCandidate(
        source="WIKIMEDIA",
        external_id="File:A.jpg",
        external_url="https://example.com/A.jpg",
        title="A",
        status=CandidateStatus.PENDING.value,
        crawled_at=datetime.now(UTC),
    )
    test_db.add(candidate)
    test_db.commit()
    test_db.refresh(candidate)
    client = _client(
        _thinking_first(json.dumps([{"id": candidate.id, "score": 0.8, "reason": "Panama"}]))
    )
    mock_get_client.return_value = client

    stats = score_candidates(test_db, batch_size=10, max_batches=1)

    test_db.refresh(candidate)
    assert candidate.relevance_score == 0.8
    assert stats["candidates_scored"] == 1
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_article_excerpt_and_summary(mock_get_client):
    client = _client(_thinking_first(" An excerpt. "), _thinking_first(" A summary. "))
    mock_get_client.return_value = client

    excerpt, summary = generate_excerpt_and_summary("Title", "Body")

    assert (excerpt, summary) == ("An excerpt.", "A summary.")
    assert len(client.calls) == 2
    for kwargs in client.calls:
        _assert_haiku_request(kwargs)


@patch("app.services.llm.get_client")
def test_research_validate_subtopics(mock_get_client):
    client = _client(_thinking_first('{"subtopics_found": 3, "subtopics_total": 3}'))
    mock_get_client.return_value = client
    content = ("word " * 4100) + "\n## References\n- A source"

    result = validate_research_document(content, ["a", "b", "c"])

    assert result["checks"]["subtopic_coverage"]["found"] == 3
    assert result["valid"] is True
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_research_references(mock_get_client):
    client = _client(_thinking_first("## References\n- Smith (1990)"))
    mock_get_client.return_value = client

    fixed = _generate_references_section("Body citing (Smith 1990).")

    assert fixed.endswith("## References\n- Smith (1990)")
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_media_search_metadata(mock_get_client):
    payload = [{"id": 7, "search_terms": "canal, canal", "alt_text": "A ship"}]
    client = _client(_thinking_first(json.dumps(payload)))
    mock_get_client.return_value = client

    out = generate_search_metadata(
        [MediaTextInput(id=7, title="Ship", source="WIKIMEDIA")], feature="media_search_terms"
    )

    assert out[7]["alt_text"] == "A ship"
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_research_summary(mock_get_client):
    client = _client(_thinking_first("a summary"))
    mock_get_client.return_value = client

    assert generate_research_summary("content") == "a summary"
    _assert_haiku_request(client.calls[0])


@patch("app.services.llm.get_client")
def test_research_summary_truncated_after_thinking_trims_text(mock_get_client):
    mock_get_client.return_value = _client(
        _thinking_first("## Dates\n- 1903: separation\n- 1914: the ca", stop_reason="max_tokens")
    )

    assert generate_research_summary("content") == "## Dates\n- 1903: separation"


@patch("app.services.llm.get_client")
def test_research_summary_truncated_while_thinking_raises(mock_get_client):
    """A cut-off before any text leaves nothing to trim: a clear ValueError,
    not an AttributeError on the thinking block."""
    thinking_only = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="", signature="sig")],
        stop_reason="max_tokens",
        usage=None,
    )
    mock_get_client.return_value = _client(thinking_only)

    with pytest.raises(ValueError, match="no text block"):
        generate_research_summary("content")


def test_first_text_skips_thinking_block():
    assert first_text(_thinking_first("answer")) == "answer"
