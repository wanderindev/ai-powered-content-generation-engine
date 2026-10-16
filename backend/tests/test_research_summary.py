"""Unit tests for app.services.research_summary.generate_research_summary."""

from unittest.mock import MagicMock, patch

from app.services.research_summary import SYSTEM_PROMPT, generate_research_summary


def _mock_anthropic_response(text: str, stop_reason: str | None = None):
    """Create a mock Anthropic client whose call returns `text`.

    The summary cap (8000 since #610) is at the streaming threshold, so the
    call goes through ``messages.stream``; ``messages.create`` is wired too so
    the helper doesn't depend on which side of the boundary the cap sits.
    The mock records the request kwargs on ``.last_kwargs()``.
    """
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=text)]
    mock_msg.stop_reason = stop_reason
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value.get_final_message.return_value = mock_msg
    stream_cm.__exit__.return_value = False
    mock_client.messages.stream.return_value = stream_cm

    def last_kwargs():
        for method in (mock_client.messages.stream, mock_client.messages.create):
            if method.call_args is not None:
                return method.call_args.kwargs
        raise AssertionError("no LLM call was made")

    mock_client.last_kwargs = last_kwargs
    return mock_client


class TestGenerateResearchSummary:
    """Tests for generate_research_summary."""

    @patch("app.services.llm.get_client")
    def test_returns_text_from_mocked_response(self, mock_get_client):
        mock_get_client.return_value = _mock_anthropic_response("a summary")

        result = generate_research_summary("some research content")

        assert result == "a summary"

    @patch("app.services.llm.get_client")
    def test_calls_haiku_model(self, mock_get_client):
        mock_client = _mock_anthropic_response("ok")
        mock_get_client.return_value = mock_client

        generate_research_summary("content")

        kwargs = mock_client.last_kwargs()
        assert kwargs["model"] == "claude-haiku-5-5"
        assert kwargs["output_config"] == {"effort": "medium"}

    @patch("app.services.llm.get_client")
    def test_uses_max_tokens_headroom(self, mock_get_client):
        """The old 3000-token ramble ceiling plus thinking headroom (#610):
        Haiku 5.5 thinks at medium effort and thinking counts toward the cap."""
        mock_client = _mock_anthropic_response("ok")
        mock_get_client.return_value = mock_client

        generate_research_summary("content")

        kwargs = mock_client.last_kwargs()
        assert kwargs["max_tokens"] == 8000

    @patch("app.services.llm.get_client")
    def test_truncated_response_is_kept_and_trimmed(self, mock_get_client):
        """A summary that overruns max_tokens must not fail the admin action.

        Haiku ignores the prompt's 800-word cap on long research docs; the
        capped response used to raise TruncationError out of create_message and
        502 the endpoint (Sentry PIC-PYTHON-FASTAPI-1T). It is now kept, with
        its dangling partial line dropped.
        """
        mock_get_client.return_value = _mock_anthropic_response(
            "## Key dates\n- 1903: separation from Colombia\n- 1914: the canal op",
            stop_reason="max_tokens",
        )

        result = generate_research_summary("content")

        assert result == "## Key dates\n- 1903: separation from Colombia"

    @patch("app.services.llm.get_client")
    def test_truncated_single_line_response_is_not_emptied(self, mock_get_client):
        """With nothing to trim back to, the partial line is better than nothing."""
        mock_get_client.return_value = _mock_anthropic_response(
            "The canal was opened in 19", stop_reason="max_tokens"
        )

        assert generate_research_summary("content") == "The canal was opened in 19"

    @patch("app.services.llm.get_client")
    def test_passes_system_prompt_and_includes_content_in_user_message(self, mock_get_client):
        mock_client = _mock_anthropic_response("ok")
        mock_get_client.return_value = mock_client

        input_content = "UNIQUE_RESEARCH_BODY_CONTENT_12345"
        generate_research_summary(input_content)

        kwargs = mock_client.last_kwargs()
        assert kwargs["system"] == SYSTEM_PROMPT
        # The user message should contain the input content verbatim.
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        assert len(messages) >= 1
        user_msg = messages[0]
        assert user_msg["role"] == "user"
        assert input_content in user_msg["content"]
