"""Unit tests for app.services.audio_tts (ElevenLabs TTS wrapper).

ElevenLabs is fully mocked — no network, no real sleeps.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import audio_tts


def _mock_client(chunks=(b"audio-", b"bytes")):
    """A mock ElevenLabs client whose convert() returns an iterator of chunks."""
    client = MagicMock()
    client.text_to_speech.convert.return_value = list(chunks)
    return client


class TestSynthesize:
    @patch("app.services.audio_tts.get_client")
    def test_joins_chunks_and_passes_model_format(self, mock_get_client):
        client = _mock_client((b"ab", b"cd"))
        mock_get_client.return_value = client

        result = audio_tts.synthesize("hola", voice_id="v1")

        assert result == b"abcd"
        client.text_to_speech.convert.assert_called_once_with(
            voice_id="v1",
            text="hola",
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )

    @patch("app.services.audio_tts.get_client")
    @patch("app.services.audio_tts.get_settings")
    def test_defaults_to_diego_voice(self, mock_settings, mock_get_client):
        mock_settings.return_value = SimpleNamespace(
            elevenlabs_voice_diego="diego-123", elevenlabs_api_key="k"
        )
        client = _mock_client()
        mock_get_client.return_value = client

        audio_tts.synthesize("hola")

        assert client.text_to_speech.convert.call_args.kwargs["voice_id"] == "diego-123"

    @patch("app.services.audio_tts.get_settings")
    def test_raises_when_no_voice_configured(self, mock_settings):
        mock_settings.return_value = SimpleNamespace(
            elevenlabs_voice_diego="", elevenlabs_api_key="k"
        )
        with pytest.raises(ValueError, match="No ElevenLabs voice"):
            audio_tts.synthesize("hola")

    @patch("app.services.audio_tts.time.sleep")
    @patch("app.services.audio_tts.get_client")
    def test_retries_then_succeeds(self, mock_get_client, mock_sleep):
        client = MagicMock()
        client.text_to_speech.convert.side_effect = [RuntimeError("boom"), [b"ok"]]
        mock_get_client.return_value = client

        result = audio_tts.synthesize("hola", voice_id="v1")

        assert result == b"ok"
        assert client.text_to_speech.convert.call_count == 2
        mock_sleep.assert_called_once()  # backed off once between the two attempts

    @patch("app.services.audio_tts.time.sleep")
    @patch("app.services.audio_tts.get_client")
    def test_raises_after_exhausting_retries(self, mock_get_client, mock_sleep):
        client = MagicMock()
        client.text_to_speech.convert.side_effect = RuntimeError("always")
        mock_get_client.return_value = client

        with pytest.raises(RuntimeError, match="always"):
            audio_tts.synthesize("hola", voice_id="v1", retries=3)

        assert client.text_to_speech.convert.call_count == 3

    @patch("app.services.audio_tts.get_client")
    def test_rejects_bad_retries(self, mock_get_client):
        with pytest.raises(ValueError, match="retries must be"):
            audio_tts.synthesize("hola", voice_id="v1", retries=0)
