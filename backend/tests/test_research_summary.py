"""Unit tests for app.services.research_summary.generate_research_summary."""

from unittest.mock import MagicMock, patch

from app.services.research_summary import SYSTEM_PROMPT, generate_research_summary


def _mock_anthropic_response(text: str):
    """Create a mock Anthropic client whose messages.create returns `text`."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
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

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku-4-5-20251001"

    @patch("app.services.llm.get_client")
    def test_uses_max_tokens_headroom(self, mock_get_client):
        """max_tokens must stay well above an ~800-word summary so the response
        never brushes create_message's truncation guard (truncation-regression fix)."""
        mock_client = _mock_anthropic_response("ok")
        mock_get_client.return_value = mock_client

        generate_research_summary("content")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 3000

    @patch("app.services.llm.get_client")
    def test_passes_system_prompt_and_includes_content_in_user_message(self, mock_get_client):
        mock_client = _mock_anthropic_response("ok")
        mock_get_client.return_value = mock_client

        input_content = "UNIQUE_RESEARCH_BODY_CONTENT_12345"
        generate_research_summary(input_content)

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"] == SYSTEM_PROMPT
        # The user message should contain the input content verbatim.
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        assert len(messages) >= 1
        user_msg = messages[0]
        assert user_msg["role"] == "user"
        assert input_content in user_msg["content"]
