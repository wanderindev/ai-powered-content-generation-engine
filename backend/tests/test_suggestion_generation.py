"""Unit tests for app.services.suggestion_generation.generate_historical_suggestions.

The service uses Anthropic adaptive thinking — responses contain multiple
content blocks (thinking + text). Behaviors verified per the brief.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from app.models.article_suggestion import ArticleSuggestion
from app.services.suggestion_generation import generate_historical_suggestions


def _text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _thinking_block():
    block = MagicMock()
    block.type = "thinking"
    return block


def _mock_anthropic_response(payload_text: str, with_thinking: bool = True):
    """Build a mock client returning a message with optional thinking + text
    blocks. Suggestion generation streams (max_tokens over the 8k threshold), so
    both the stream and create seams are wired and resolve to the same message.
    """
    blocks = []
    if with_thinking:
        blocks.append(_thinking_block())
    blocks.append(_text_block(payload_text))
    mock_msg = MagicMock()
    mock_msg.content = blocks
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = mock_msg
    cm.__exit__.return_value = False
    mock_client.messages.stream.return_value = cm
    return mock_client


SUGGESTION_PAYLOAD = {
    "suggestions": [
        {
            "title": "Suggestion One",
            "main_topic": "Topic one description.",
            "sub_topics": [
                {"title": "T", "description": "D"},
            ],
            "point_of_view": "Point of view one.",
        }
    ]
}


class TestGenerateHistoricalSuggestions:
    @patch("app.services.llm.get_client")
    def test_returns_one_row_per_suggestion(self, mock_get_client, test_db, category_factory):
        category = category_factory()
        # Use two suggestions to verify counting.
        payload = {
            "suggestions": [
                {
                    "title": "S1",
                    "main_topic": "MT1",
                    "sub_topics": [],
                    "point_of_view": "POV1",
                },
                {
                    "title": "S2",
                    "main_topic": "MT2",
                    "sub_topics": [],
                    "point_of_view": "POV2",
                },
            ]
        }
        mock_get_client.return_value = _mock_anthropic_response(json.dumps(payload))

        result = generate_historical_suggestions(
            category=category,
            taxonomy=category.taxonomy if hasattr(category, "taxonomy") else None,
            num_suggestions=2,
            db=test_db,
        )

        assert len(result) == 2
        for row in result:
            assert isinstance(row, ArticleSuggestion)

    @patch("app.services.llm.get_client")
    def test_sets_category_id_on_each_row(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        mock_get_client.return_value = _mock_anthropic_response(json.dumps(SUGGESTION_PAYLOAD))

        result = generate_historical_suggestions(
            category=category,
            taxonomy=tax,
            num_suggestions=1,
            db=test_db,
        )

        assert len(result) == 1
        assert result[0].category_id == category.id

    @patch("app.services.llm.get_client")
    def test_sub_topics_flattened_to_title_colon_description(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        payload = {
            "suggestions": [
                {
                    "title": "S",
                    "main_topic": "MT",
                    "sub_topics": [
                        {"title": "T", "description": "D"},
                        {"title": "U", "description": "E"},
                    ],
                    "point_of_view": "POV",
                }
            ]
        }
        mock_get_client.return_value = _mock_anthropic_response(json.dumps(payload))

        result = generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=1, db=test_db
        )

        assert result[0].sub_topics == ["T: D", "U: E"]

    @patch("app.services.llm.get_client")
    def test_handles_response_with_only_text_block(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        # No thinking block this time.
        mock_get_client.return_value = _mock_anthropic_response(
            json.dumps(SUGGESTION_PAYLOAD), with_thinking=False
        )

        result = generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=1, db=test_db
        )

        assert len(result) == 1

    @patch("app.services.llm.get_client")
    def test_uses_adaptive_thinking_and_sonnet_model(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        mock_client = _mock_anthropic_response(json.dumps(SUGGESTION_PAYLOAD))
        mock_get_client.return_value = mock_client

        generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=1, db=test_db
        )

        # Streamed call (max_tokens over the 8k threshold) lands on messages.stream.
        kwargs = mock_client.messages.stream.call_args.kwargs
        assert kwargs["model"] == "claude-sonnet-5-5"
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}

    @patch("app.services.llm.get_client")
    def test_max_tokens_scales_with_num_suggestions(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        # The output budget must grow with the request so the JSON does not
        # truncate at the top of the allowed range (#101). Compare a small and a
        # large request and assert the larger one reserves more output room.
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)

        from app.services.suggestion_generation import _max_tokens_for

        # The sizing is monotonic and leaves output room above the thinking budget.
        small = _max_tokens_for(1)
        large = _max_tokens_for(20)
        assert large > small
        assert small - 10_000 >= 1_500  # output budget for 1 suggestion
        assert large - 10_000 >= 20 * 600  # output budget for 20 suggestions

        mock_client = _mock_anthropic_response(json.dumps(SUGGESTION_PAYLOAD))
        mock_get_client.return_value = mock_client
        generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=20, db=test_db
        )
        kwargs = mock_client.messages.stream.call_args.kwargs
        assert kwargs["max_tokens"] == _max_tokens_for(20)

    @patch("app.services.llm.get_client")
    def test_prompt_includes_existing_titles(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)

        # Pre-create some suggestions in this category.
        for title in ("Existing One", "Existing Two"):
            test_db.add(
                ArticleSuggestion(
                    title=title,
                    category_id=category.id,
                    status="APPROVED",
                    main_topic="mt",
                    sub_topics=[],
                    point_of_view="pov",
                )
            )
        test_db.commit()

        mock_client = _mock_anthropic_response(json.dumps(SUGGESTION_PAYLOAD))
        mock_get_client.return_value = mock_client

        generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=1, db=test_db
        )

        # Inspect the prompt that was sent (streamed call → messages.stream).
        kwargs = mock_client.messages.stream.call_args.kwargs
        prompt = kwargs["messages"][0]["content"]
        assert "Existing One" in prompt
        assert "Existing Two" in prompt

    @patch("app.services.llm.get_client")
    def test_handles_code_fenced_json_response(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        fenced = "```json\n" + json.dumps(SUGGESTION_PAYLOAD) + "\n```"
        mock_get_client.return_value = _mock_anthropic_response(fenced)

        result = generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=1, db=test_db
        )

        assert len(result) == 1
        assert result[0].title == "Suggestion One"

    @patch("app.services.llm.get_client")
    def test_empty_suggestions_list_returns_empty(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        mock_get_client.return_value = _mock_anthropic_response(json.dumps({"suggestions": []}))

        result = generate_historical_suggestions(
            category=category, taxonomy=tax, num_suggestions=0, db=test_db
        )

        assert result == []

    @patch("app.services.llm.get_client")
    def test_malformed_json_raises_value_error(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        # parse_llm_json normalizes bad JSON to ValueError (issue #77).
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        mock_get_client.return_value = _mock_anthropic_response("not json")

        with pytest.raises(ValueError, match="malformed JSON"):
            generate_historical_suggestions(
                category=category, taxonomy=tax, num_suggestions=1, db=test_db
            )

    @patch("app.services.llm.get_client")
    def test_missing_suggestions_key_raises_value_error(
        self, mock_get_client, test_db, taxonomy_factory, category_factory
    ):
        tax = taxonomy_factory()
        category = category_factory(taxonomy=tax)
        mock_get_client.return_value = _mock_anthropic_response(json.dumps({"wrong_key": []}))

        with pytest.raises(ValueError, match="missing required key"):
            generate_historical_suggestions(
                category=category, taxonomy=tax, num_suggestions=1, db=test_db
            )
