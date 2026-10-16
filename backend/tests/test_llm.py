"""Unit tests for app.services.llm — the unified Anthropic seam (issues #76, #78).

Covers create_message kwarg forwarding, per-call usage accumulation, the
in-process runaway budget backstop, and mock-safe usage extraction.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.services import llm
from app.services.llm import (
    HAIKU_MODEL,
    OPUS_MODEL,
    SONNET_MODEL,
    BudgetExceededError,
    LLMUsage,
    RefusalError,
    TruncationError,
    create_message,
    get_session_usage,
    reset_session_usage,
)


@pytest.fixture(autouse=True)
def _reset_usage():
    """Isolate the process-lifetime accumulator between tests."""
    reset_session_usage()
    yield
    reset_session_usage()


def _msg(
    *, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0, text="ok", stop_reason=None
):
    """Build a message stub with a realistic usage object."""
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=usage,
        stop_reason=stop_reason,
    )


def _client_returning(message):
    """Mock client wiring BOTH seams (create + stream) to return `message`, so
    the test works whichever path create_message takes for the given max_tokens."""
    client = MagicMock()
    client.messages.create.return_value = message
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = message
    cm.__exit__.return_value = False
    client.messages.stream.return_value = cm
    return client


def _settings(cap, *, stream_min_tokens=8000, timeout=120.0, stream_timeout=600.0):
    return SimpleNamespace(
        anthropic_budget_input_tokens=cap,
        anthropic_stream_min_tokens=stream_min_tokens,
        anthropic_timeout_seconds=timeout,
        anthropic_stream_timeout_seconds=stream_timeout,
    )


class TestCreateMessage:
    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_forwards_kwargs_and_strips_feature(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)  # cap disabled
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(
            feature="article_outline",
            model="claude-x",
            max_tokens=42,
            messages=[{"role": "user", "content": "hi"}],
        )

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-x"
        assert kwargs["max_tokens"] == 42
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        # feature is a logging label, never sent to the API
        assert "feature" not in kwargs

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_returns_underlying_message(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        message = _msg(text="the answer")
        mock_get_client.return_value = _client_returning(message)

        assert create_message(feature="x", model="m", max_tokens=1, messages=[]) is message


class TestStreaming:
    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_streams_at_or_above_threshold(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0, stream_min_tokens=8000)
        client = _client_returning(_msg(text="long"))
        mock_get_client.return_value = client

        result = create_message(
            feature="x", model="claude-sonnet-5-5", max_tokens=8000, messages=[]
        )

        client.messages.stream.assert_called_once()
        client.messages.create.assert_not_called()
        assert result.content[0].text == "long"

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_does_not_stream_below_threshold(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0, stream_min_tokens=8000, timeout=120.0)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(feature="x", model="claude-haiku", max_tokens=7999, messages=[])

        client.messages.create.assert_called_once()
        client.messages.stream.assert_not_called()
        assert client.messages.create.call_args.kwargs["timeout"] == 120.0

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_explicit_stream_flag_overrides_threshold(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0, stream_min_tokens=8000)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        # Small request, but stream=True forces the stream path.
        create_message(feature="x", model="m", max_tokens=10, messages=[], stream=True)
        client.messages.stream.assert_called_once()
        client.messages.create.assert_not_called()

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_streamed_call_uses_safety_net_timeout(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0, stream_min_tokens=1, stream_timeout=600.0)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(feature="x", model="m", max_tokens=16000, messages=[])

        assert client.messages.stream.call_args.kwargs["timeout"] == 600.0


class TestUsageTracking:
    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_accumulates_across_calls(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        mock_get_client.return_value = _client_returning(
            _msg(input_tokens=100, output_tokens=20, cache_read=5, cache_write=7)
        )

        create_message(feature="x", model="m", max_tokens=1, messages=[])
        create_message(feature="x", model="m", max_tokens=1, messages=[])

        usage = get_session_usage()
        assert usage.input_tokens == 200
        assert usage.output_tokens == 40
        assert usage.cache_read_input_tokens == 10
        assert usage.cache_creation_input_tokens == 14

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_mock_message_without_real_usage_extracts_zeros(
        self, mock_get_client, mock_get_settings
    ):
        mock_get_settings.return_value = _settings(0)
        # A bare MagicMock message — .usage.input_tokens is a MagicMock, not int.
        mock_get_client.return_value = _client_returning(MagicMock())

        create_message(feature="x", model="m", max_tokens=1, messages=[])

        assert get_session_usage() == LLMUsage(0, 0, 0, 0)

    def test_reset_clears_accumulator(self):
        with (
            patch("app.services.llm.get_settings", return_value=_settings(0)),
            patch(
                "app.services.llm.get_client", return_value=_client_returning(_msg(input_tokens=9))
            ),
        ):
            create_message(feature="x", model="m", max_tokens=1, messages=[])
        assert get_session_usage().input_tokens == 9
        reset_session_usage()
        assert get_session_usage() == LLMUsage(0, 0, 0, 0)


class TestBudgetCap:
    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_raises_once_cumulative_reaches_cap(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(50)
        client = _client_returning(_msg(input_tokens=60))
        mock_get_client.return_value = client

        # First call proceeds (accumulator 0 < 50) and pushes total to 60.
        create_message(feature="x", model="m", max_tokens=1, messages=[])
        # Second call is refused before spending (60 >= 50).
        with pytest.raises(BudgetExceededError):
            create_message(feature="x", model="m", max_tokens=1, messages=[])

        assert client.messages.create.call_count == 1

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_cap_disabled_when_zero(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        client = _client_returning(_msg(input_tokens=10_000))
        mock_get_client.return_value = client

        for _ in range(3):
            create_message(feature="x", model="m", max_tokens=1, messages=[])

        assert client.messages.create.call_count == 3
        assert get_session_usage().input_tokens == 30_000


class TestLLMUsage:
    def test_add(self):
        total = LLMUsage(1, 2, 3, 4) + LLMUsage(10, 20, 30, 40)
        assert total == LLMUsage(11, 22, 33, 44)

    def test_defaults_zero(self):
        assert LLMUsage() == LLMUsage(0, 0, 0, 0)


class TestTruncationGuard:
    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_raises_when_stopped_at_max_tokens(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        mock_get_client.return_value = _client_returning(_msg(stop_reason="max_tokens"))

        with pytest.raises(TruncationError):
            create_message(feature="x", model="m", max_tokens=10, messages=[])

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_usage_still_recorded_before_raising(self, mock_get_client, mock_get_settings):
        # The wasted spend on a truncated call must still count toward the budget.
        mock_get_settings.return_value = _settings(0)
        mock_get_client.return_value = _client_returning(
            _msg(input_tokens=123, output_tokens=10, stop_reason="max_tokens")
        )

        with pytest.raises(TruncationError):
            create_message(feature="x", model="m", max_tokens=10, messages=[])
        assert get_session_usage().input_tokens == 123

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_opt_out_returns_truncated_message(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        message = _msg(stop_reason="max_tokens", text="cut off")
        mock_get_client.return_value = _client_returning(message)

        result = create_message(
            feature="x", model="m", max_tokens=10, messages=[], reject_on_truncation=False
        )
        assert result is message

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_normal_stop_reason_does_not_raise(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        mock_get_client.return_value = _client_returning(_msg(stop_reason="end_turn"))

        assert create_message(feature="x", model="m", max_tokens=10, messages=[]) is not None

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_magicmock_message_does_not_trip_guard(self, mock_get_client, mock_get_settings):
        # A bare MagicMock .stop_reason is a MagicMock, never == "max_tokens".
        mock_get_settings.return_value = _settings(0)
        mock_get_client.return_value = _client_returning(MagicMock())

        # Must not raise TruncationError.
        create_message(feature="x", model="m", max_tokens=1, messages=[])


def test_module_exposes_seam():
    """Guard the public surface other services import."""
    assert callable(llm.create_message)
    assert callable(llm.get_client)
    assert callable(llm.parse_llm_json)


class TestRefusalFallback:
    """Sonnet/Opus calls opt into server-side refusal fallback; Haiku does not."""

    @pytest.mark.parametrize("model", [SONNET_MODEL, OPUS_MODEL])
    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_5_5_models_send_default_fallback(self, mock_get_client, mock_get_settings, model):
        mock_get_settings.return_value = _settings(0)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(feature="x", model=model, max_tokens=100, messages=[])

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["extra_body"] == {"fallbacks": "default"}
        assert kwargs["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_streamed_calls_send_fallback_too(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0, stream_min_tokens=8000)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(feature="x", model=OPUS_MODEL, max_tokens=16000, messages=[])

        assert client.messages.stream.call_args.kwargs["extra_body"] == {"fallbacks": "default"}

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_haiku_is_sent_without_fallback(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(feature="x", model=HAIKU_MODEL, max_tokens=100, messages=[])

        kwargs = client.messages.create.call_args.kwargs
        assert "extra_body" not in kwargs
        assert "extra_headers" not in kwargs

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_haiku_5_5_effort_passes_through_without_fallback(
        self, mock_get_client, mock_get_settings
    ):
        """#610: Haiku 5.5 has no server-side fallback; its explicit medium
        effort reaches the API untouched, streamed or not."""
        assert HAIKU_MODEL == "claude-haiku-5-5"
        mock_get_settings.return_value = _settings(0, stream_min_tokens=8000)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        for max_tokens, method in ((2000, "create"), (8000, "stream")):
            create_message(
                feature="x",
                model=HAIKU_MODEL,
                max_tokens=max_tokens,
                output_config={"effort": "medium"},
                messages=[],
            )
            kwargs = getattr(client.messages, method).call_args.kwargs
            assert kwargs["output_config"] == {"effort": "medium"}
            assert "extra_body" not in kwargs
            assert "extra_headers" not in kwargs

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_existing_beta_header_is_kept(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        client = _client_returning(_msg())
        mock_get_client.return_value = client

        create_message(
            feature="x",
            model=SONNET_MODEL,
            max_tokens=100,
            messages=[],
            extra_headers={"anthropic-beta": "some-beta"},
        )

        headers = client.messages.create.call_args.kwargs["extra_headers"]
        assert headers["anthropic-beta"] == "some-beta,server-side-fallback-2026-07-01"

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_refusal_raises(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        message = _msg(stop_reason="refusal")
        message.content = []
        message.stop_details = SimpleNamespace(type="refusal", category="bio")
        mock_get_client.return_value = _client_returning(message)

        with pytest.raises(RefusalError, match="bio"):
            create_message(feature="x", model=SONNET_MODEL, max_tokens=100, messages=[])

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_refusal_is_a_value_error(self, mock_get_client, mock_get_settings):
        # Callers already map ValueError to "the LLM step failed".
        mock_get_settings.return_value = _settings(0)
        message = _msg(stop_reason="refusal")
        message.stop_details = None
        mock_get_client.return_value = _client_returning(message)

        with pytest.raises(ValueError):
            create_message(feature="x", model=OPUS_MODEL, max_tokens=100, messages=[])

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_fallback_served_response_is_returned(self, mock_get_client, mock_get_settings, caplog):
        mock_get_settings.return_value = _settings(0)
        message = _msg(text="served by fallback")
        message.model = "claude-opus-5"
        message.content = [
            SimpleNamespace(type="fallback"),
            SimpleNamespace(type="text", text="served by fallback"),
        ]
        message.usage.iterations = [
            SimpleNamespace(type="message"),
            SimpleNamespace(type="fallback_message"),
        ]
        mock_get_client.return_value = _client_returning(message)

        result = create_message(feature="x", model=OPUS_MODEL, max_tokens=100, messages=[])

        assert llm.first_text(result) == "served by fallback"
        assert "fell back after a refusal" in caplog.text


class TestFirstText:
    def test_skips_leading_thinking_block(self):
        message = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="", signature="s"),
                SimpleNamespace(type="text", text="answer"),
            ]
        )
        assert llm.first_text(message) == "answer"

    def test_thinking_only_response_raises_value_error(self):
        """#610: a truncation-tolerant call cut off while thinking has no text
        block; that must be a ValueError, not an AttributeError on the block."""
        message = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="", signature="s")]
        )
        with pytest.raises(ValueError, match="no text block"):
            llm.first_text(message)

    def test_untyped_mock_block_still_falls_back(self):
        message = MagicMock(content=[MagicMock(text="mocked")])
        assert llm.first_text(message) == "mocked"

    def test_empty_content_raises(self):
        with pytest.raises(ValueError, match="no content blocks"):
            llm.first_text(SimpleNamespace(content=[]))


class TestPromptCaching:
    """The seam's cache markers (#716) and how caching meets the backstop."""

    def test_cache_control_default_is_5m_ephemeral(self):
        assert llm.cache_control() == {"type": "ephemeral"}

    def test_cache_control_1h(self):
        assert llm.cache_control("1h") == {"type": "ephemeral", "ttl": "1h"}

    def test_cache_control_rejects_unknown_ttl(self):
        with pytest.raises(ValueError, match="ttl"):
            llm.cache_control("10m")

    def test_cached_text_block(self):
        assert llm.cached_text("PREFIX") == {
            "type": "text",
            "text": "PREFIX",
            "cache_control": {"type": "ephemeral"},
        }
        assert llm.cached_text("P", ttl="1h")["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_markers_reach_the_client_unchanged(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(0)
        client = _client_returning(_msg())
        mock_get_client.return_value = client
        content = [llm.cached_text("shared"), {"type": "text", "text": "varying"}]

        create_message(
            feature="x",
            model=SONNET_MODEL,
            max_tokens=10,
            system=[llm.cached_text("SYSTEM")],
            messages=[{"role": "user", "content": content}],
            cache_control=llm.cache_control(),
        )

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["system"] == [llm.cached_text("SYSTEM")]
        assert kwargs["messages"][0]["content"] == content
        assert kwargs["cache_control"] == {"type": "ephemeral"}
        assert "feature" not in kwargs

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_usage_log_reports_cache_read_and_write(
        self, mock_get_client, mock_get_settings, caplog
    ):
        mock_get_settings.return_value = _settings(0)
        mock_get_client.return_value = _client_returning(
            _msg(input_tokens=10, output_tokens=5, cache_read=3000, cache_write=700)
        )

        with caplog.at_level("INFO", logger="app.services.llm"):
            create_message(feature="cached_feature", model=SONNET_MODEL, max_tokens=10, messages=[])

        assert "feature=cached_feature" in caplog.text
        assert "cache_read=3000 cache_write=700" in caplog.text
        # The process total is what the backstop counts: input + writes, not reads.
        assert "process_total_input=710" in caplog.text

    def test_backstop_tokens_count_writes_not_reads(self):
        usage = LLMUsage(
            input_tokens=100,
            output_tokens=9,
            cache_read_input_tokens=5000,
            cache_creation_input_tokens=40,
        )
        assert llm.backstop_tokens(usage) == 140

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_cache_writes_trip_the_cap(self, mock_get_client, mock_get_settings):
        # A loop writing a fresh cache entry every call (marker after the varying
        # part) has ~0 uncached input; the writes alone must still trip the cap.
        mock_get_settings.return_value = _settings(50)
        client = _client_returning(_msg(input_tokens=1, cache_write=60))
        mock_get_client.return_value = client

        create_message(feature="x", model="m", max_tokens=1, messages=[])
        with pytest.raises(BudgetExceededError):
            create_message(feature="x", model="m", max_tokens=1, messages=[])
        assert client.messages.create.call_count == 1

    @patch("app.services.llm.get_settings")
    @patch("app.services.llm.get_client")
    def test_cache_reads_do_not_trip_the_cap(self, mock_get_client, mock_get_settings):
        mock_get_settings.return_value = _settings(50)
        client = _client_returning(_msg(input_tokens=1, cache_read=10_000))
        mock_get_client.return_value = client

        for _ in range(3):
            create_message(feature="x", model="m", max_tokens=1, messages=[])
        assert client.messages.create.call_count == 3
