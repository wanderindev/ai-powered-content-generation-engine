"""Tests for app.services.media_scoring — LLM-based relevance scoring of media candidates."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
from app.models.media_candidate import CandidateStatus, MediaCandidate
from app.services.media_scoring import score_candidates


def _make_candidate(
    title: str = "Panama Canal Photo",
    source: str = "WIKIMEDIA",
    external_id: str | None = None,
    status: str = CandidateStatus.PENDING.value,
    relevance_score: float | None = None,
) -> MediaCandidate:
    """Helper to build a MediaCandidate with the minimum required fields."""
    ext_id = external_id or f"File:{title.replace(' ', '_')}.jpg"
    return MediaCandidate(
        source=source,
        external_id=ext_id,
        external_url=f"https://example.com/{ext_id}.jpg",
        title=title,
        status=status,
        relevance_score=relevance_score,
        crawled_at=datetime.now(UTC),
    )


def _build_mock_anthropic(mock_anthropic_class, response_payload):
    """Wire up the anthropic.Anthropic() mock to return a fake message.

    response_payload may be a list of dicts (will be json.dumps'd) or a raw string
    (used as-is, e.g. to test markdown fencing).
    """
    if isinstance(response_payload, str):
        response_text = response_payload
    else:
        response_text = json.dumps(response_payload)

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_content_block = MagicMock()
    mock_content_block.text = response_text
    mock_message.content = [mock_content_block]
    mock_client.messages.create.return_value = mock_message
    mock_anthropic_class.return_value = mock_client
    return mock_client


_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _rate_limit_error() -> anthropic.RateLimitError:
    response = httpx.Response(429, request=_REQUEST)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


def _terminal_api_error() -> anthropic.APIError:
    # Parent class instance — represents auth/permission-class terminal errors.
    return anthropic.APIError("invalid api key", request=_REQUEST, body=None)


def _build_failing_anthropic(mock_anthropic_class, side_effect):
    """Wire the anthropic mock so messages.create raises `side_effect`.

    `side_effect` may be a single exception instance (raised every call) or a
    list of values (per-call). Returns the mock client for call-count asserts.
    """
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = side_effect
    mock_anthropic_class.return_value = mock_client
    return mock_client


class TestScoreCandidates:
    """Covers score_candidates(db, batch_size, max_batches)."""

    @patch("app.services.llm.get_client")
    def test_scores_pending_unscored_candidates(self, mock_anthropic_class, test_db):
        c1 = _make_candidate(title="A", external_id="File:A.jpg")
        c2 = _make_candidate(title="B", external_id="File:B.jpg")
        test_db.add_all([c1, c2])
        test_db.commit()
        test_db.refresh(c1)
        test_db.refresh(c2)

        _build_mock_anthropic(
            mock_anthropic_class,
            [
                {"id": c1.id, "score": 0.85, "reason": "Clearly Panama"},
                {"id": c2.id, "score": 0.42, "reason": "Tangential"},
            ],
        )

        stats = score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c1)
        test_db.refresh(c2)
        assert c1.relevance_score == 0.85
        assert c1.relevance_notes == "Clearly Panama"
        assert c2.relevance_score == 0.42
        assert c2.relevance_notes == "Tangential"
        assert stats["candidates_scored"] == 2
        assert stats["batches_processed"] == 1

    @patch("app.services.llm.get_client")
    def test_skips_non_pending_candidates(self, mock_anthropic_class, test_db):
        pending = _make_candidate(title="P", external_id="File:P.jpg")
        approved = _make_candidate(
            title="A",
            external_id="File:A.jpg",
            status=CandidateStatus.APPROVED.value,
        )
        test_db.add_all([pending, approved])
        test_db.commit()
        test_db.refresh(pending)
        test_db.refresh(approved)

        # Mock only returns a score for the PENDING one (the APPROVED won't be in prompt)
        _build_mock_anthropic(
            mock_anthropic_class,
            [{"id": pending.id, "score": 0.7, "reason": "ok"}],
        )

        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(pending)
        test_db.refresh(approved)
        assert pending.relevance_score == 0.7
        assert approved.relevance_score is None

    @patch("app.services.llm.get_client")
    def test_skips_already_scored_candidates(self, mock_anthropic_class, test_db):
        already = _make_candidate(
            title="Already",
            external_id="File:Already.jpg",
            relevance_score=0.5,
        )
        test_db.add(already)
        test_db.commit()
        test_db.refresh(already)

        _build_mock_anthropic(mock_anthropic_class, [])

        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(already)
        # Score is unchanged
        assert already.relevance_score == 0.5
        # And the LLM was never invoked because the query returned no candidates
        mock_anthropic_class.return_value.messages.create.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_clamps_score_above_one_to_one(self, mock_anthropic_class, test_db):
        c = _make_candidate(title="C", external_id="File:C.jpg")
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)

        _build_mock_anthropic(
            mock_anthropic_class,
            [{"id": c.id, "score": 1.5, "reason": "x"}],
        )
        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c)
        assert c.relevance_score == 1.0

    @patch("app.services.llm.get_client")
    def test_clamps_negative_score_to_zero(self, mock_anthropic_class, test_db):
        c = _make_candidate(title="C", external_id="File:C2.jpg")
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)

        _build_mock_anthropic(
            mock_anthropic_class,
            [{"id": c.id, "score": -0.3, "reason": "x"}],
        )
        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c)
        assert c.relevance_score == 0.0

    @patch("app.services.llm.get_client")
    def test_rounds_score_to_two_decimals(self, mock_anthropic_class, test_db):
        c = _make_candidate(title="C", external_id="File:C3.jpg")
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)

        _build_mock_anthropic(
            mock_anthropic_class,
            [{"id": c.id, "score": 0.123456, "reason": "x"}],
        )
        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c)
        assert c.relevance_score == 0.12

    @patch("app.services.llm.get_client")
    def test_truncates_reason_to_500_chars(self, mock_anthropic_class, test_db):
        c = _make_candidate(title="C", external_id="File:C4.jpg")
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)

        long_reason = "a" * 600
        _build_mock_anthropic(
            mock_anthropic_class,
            [{"id": c.id, "score": 0.5, "reason": long_reason}],
        )
        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c)
        assert c.relevance_notes is not None
        assert len(c.relevance_notes) == 500

    @patch("app.services.llm.get_client")
    def test_handles_markdown_fenced_json_response(self, mock_anthropic_class, test_db):
        c = _make_candidate(title="C", external_id="File:C5.jpg")
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)

        payload = json.dumps([{"id": c.id, "score": 0.9, "reason": "fenced"}])
        fenced_text = f"Here you go:\n```json\n{payload}\n```\nThat's all."
        _build_mock_anthropic(mock_anthropic_class, fenced_text)

        score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c)
        assert c.relevance_score == 0.9
        assert c.relevance_notes == "fenced"

    @patch("app.services.llm.get_client")
    def test_respects_max_batches(self, mock_anthropic_class, test_db):
        candidates = []
        for i in range(50):
            c = _make_candidate(title=f"Img{i}", external_id=f"File:Img{i}.jpg")
            candidates.append(c)
        test_db.add_all(candidates)
        test_db.commit()
        for c in candidates:
            test_db.refresh(c)

        # Each call returns scores keyed to the candidate IDs the function asks for.
        # We can't know IDs in advance for both batches, so build the mock to look at
        # the prompt — but easier: return scores for all ids the function has in scope
        # using a side_effect that inspects the call's input.
        def fake_create(**kwargs):
            content = kwargs["messages"][0]["content"]
            # Pull [ID=N] tokens out of the prompt
            import re

            ids = [int(m) for m in re.findall(r"\[ID=(\d+)\]", content)]
            payload = json.dumps([{"id": i, "score": 0.5, "reason": "ok"} for i in ids])
            msg = MagicMock()
            block = MagicMock()
            block.text = payload
            msg.content = [block]
            return msg

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create
        mock_anthropic_class.return_value = mock_client

        stats = score_candidates(test_db, batch_size=10, max_batches=2)

        # Two batches of 10 = 20 candidates scored, batches_processed == 2
        assert stats["batches_processed"] == 2
        assert stats["candidates_scored"] == 20
        assert mock_client.messages.create.call_count == 2

    @patch("app.services.llm.get_client")
    def test_returns_early_with_no_candidates(self, mock_anthropic_class, test_db):
        _build_mock_anthropic(mock_anthropic_class, [])

        stats = score_candidates(test_db, batch_size=10, max_batches=5)

        assert stats == {
            "candidates_scored": 0,
            "batches_processed": 0,
            "batches_skipped": 0,
        }
        mock_anthropic_class.return_value.messages.create.assert_not_called()

    @patch("app.services.llm.get_client")
    def test_unexpected_exception_propagates(self, mock_anthropic_class, test_db):
        # New contract: a bare/unexpected Exception is NOT caught — it propagates
        # so it reaches Sentry as a real bug rather than being buried per-batch.
        candidates = []
        for i in range(5):
            c = _make_candidate(title=f"X{i}", external_id=f"File:X{i}.jpg")
            candidates.append(c)
        test_db.add_all(candidates)
        test_db.commit()

        mock_client = _build_failing_anthropic(mock_anthropic_class, Exception("boom"))

        with pytest.raises(Exception, match="boom"):
            score_candidates(test_db, batch_size=10, max_batches=1)

        # The LLM was attempted (the path is actually exercised).
        mock_client.messages.create.assert_called_once()

    @patch("app.services.media_scoring.sentry_sdk")
    @patch("app.services.llm.get_client")
    def test_transient_error_skips_batch_and_does_not_loop_forever(
        self, mock_anthropic_class, mock_sentry, test_db
    ):
        # Two batches worth of unscored candidates; every call raises a transient
        # RateLimitError. The run must NOT raise, must skip, and must terminate
        # (bounded create() calls — proving the .offset() steps past skipped rows).
        candidates = [
            _make_candidate(title=f"R{i}", external_id=f"File:R{i}.jpg")
            for i in range(15)
        ]
        test_db.add_all(candidates)
        test_db.commit()

        mock_client = _build_failing_anthropic(
            mock_anthropic_class, _rate_limit_error()
        )

        stats = score_candidates(test_db, batch_size=10)

        assert stats["candidates_scored"] == 0
        assert stats["batches_processed"] == 0
        assert stats["batches_skipped"] >= 1
        # Bounded calls: with 15 rows and batch_size 10, offset advances past the
        # skipped batches and the query empties out — no infinite loop.
        assert mock_client.messages.create.call_count <= 3
        # Transient errors are warning-only; no Sentry capture.
        mock_sentry.capture_exception.assert_not_called()

    @patch("app.services.media_scoring.sentry_sdk")
    @patch("app.services.llm.get_client")
    def test_malformed_response_skips_batch_and_captures_sentry(
        self, mock_anthropic_class, mock_sentry, test_db
    ):
        candidates = [
            _make_candidate(title=f"M{i}", external_id=f"File:M{i}.jpg")
            for i in range(5)
        ]
        test_db.add_all(candidates)
        test_db.commit()

        # Non-JSON text triggers json.JSONDecodeError inside the try.
        _build_mock_anthropic(mock_anthropic_class, "this is not json at all")

        stats = score_candidates(test_db, batch_size=10)

        assert stats["candidates_scored"] == 0
        assert stats["batches_skipped"] >= 1
        mock_sentry.capture_exception.assert_called()

    @patch("app.services.media_scoring.sentry_sdk")
    @patch("app.services.llm.get_client")
    def test_terminal_api_error_stops_and_captures_sentry(
        self, mock_anthropic_class, mock_sentry, test_db
    ):
        candidates = [
            _make_candidate(title=f"T{i}", external_id=f"File:T{i}.jpg")
            for i in range(15)
        ]
        test_db.add_all(candidates)
        test_db.commit()

        mock_client = _build_failing_anthropic(
            mock_anthropic_class, _terminal_api_error()
        )

        # Should not raise — terminal errors break the loop cleanly.
        stats = score_candidates(test_db, batch_size=10)

        assert stats["candidates_scored"] == 0
        assert stats["batches_processed"] == 0
        assert stats["batches_skipped"] == 0
        # Broke out after the first terminal error.
        mock_client.messages.create.assert_called_once()
        mock_sentry.capture_exception.assert_called_once()

    @patch("app.services.llm.get_client")
    def test_silently_drops_unknown_candidate_ids(self, mock_anthropic_class, test_db):
        c = _make_candidate(title="Known", external_id="File:Known.jpg")
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)

        # Return a score for the real candidate AND a phantom id 999999
        _build_mock_anthropic(
            mock_anthropic_class,
            [
                {"id": c.id, "score": 0.8, "reason": "real"},
                {"id": 999999, "score": 0.1, "reason": "phantom"},
            ],
        )

        stats = score_candidates(test_db, batch_size=10, max_batches=1)

        test_db.refresh(c)
        assert c.relevance_score == 0.8
        # Only the real candidate was updated — the phantom was silently ignored
        assert stats["candidates_scored"] == 1
