"""ElevenLabs text-to-speech wrapper.

A thin seam over the ElevenLabs SDK that synthesizes one text segment to MP3
bytes. The narrated-slide pipeline (issue #311 / epic #316) builds on this:
per-slide segment caching, stitching, and video assembly live in higher layers.

Mirrors the Anthropic access pattern in ``app/services/llm.py`` (one cached
client, model/voice constants in one place) and ports the retry/backoff from
feliu-dev's ``audio_generation.py``.
"""

import logging
import time
from functools import lru_cache

from elevenlabs import ElevenLabs

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Synthesis defaults. Multilingual model so Diego's Spanish narration (epic #316)
# renders correctly; 44.1 kHz / 128 kbps MP3 is the pipeline's audio format.
TTS_MODEL = "eleven_multilingual_v2"
TTS_OUTPUT_FORMAT = "mp3_44100_128"


@lru_cache(maxsize=1)
def get_client() -> ElevenLabs:
    """Return the process-wide ElevenLabs client (one shared instance)."""
    return ElevenLabs(api_key=get_settings().elevenlabs_api_key)


def synthesize(
    text: str,
    *,
    voice_id: str | None = None,
    retries: int = 3,
    voice_settings=None,
    previous_text: str | None = None,
    next_text: str | None = None,
) -> bytes:
    """Synthesize ``text`` to MP3 bytes via ElevenLabs (Diego's voice by default).

    ``voice_id`` defaults to ``settings.elevenlabs_voice_diego``. Transient
    failures are retried with linear backoff (5s, 10s, …), mirroring feliu-dev's
    ``audio_generation.py``; the last exception is re-raised if every attempt
    fails.

    ``voice_settings`` (an ``elevenlabs.VoiceSettings``) overrides the voice's
    stored defaults; ``previous_text`` / ``next_text`` are request-stitching
    context so per-paragraph generations keep continuous prosody (the article
    narration technique — long single generations flatten out).
    """
    voice = voice_id or get_settings().elevenlabs_voice_diego
    if not voice:
        raise ValueError("No ElevenLabs voice configured (elevenlabs_voice_diego is unset)")
    if retries < 1:
        raise ValueError("retries must be >= 1")

    # The SDK distinguishes "omitted" from an explicit null — only pass what
    # the caller actually set.
    extra: dict = {}
    if voice_settings is not None:
        extra["voice_settings"] = voice_settings
    if previous_text is not None:
        extra["previous_text"] = previous_text
    if next_text is not None:
        extra["next_text"] = next_text

    client = get_client()
    for attempt in range(retries):
        try:
            audio_iter = client.text_to_speech.convert(
                voice_id=voice,
                text=text,
                model_id=TTS_MODEL,
                output_format=TTS_OUTPUT_FORMAT,
                **extra,
            )
            # convert() returns an iterator of byte chunks.
            return b"".join(audio_iter)
        except Exception as exc:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                logger.warning(
                    "ElevenLabs synthesis failed (attempt %d/%d): %s; retrying in %ds",
                    attempt + 1,
                    retries,
                    exc,
                    wait,
                )
                time.sleep(wait)
            else:
                raise
    # Unreachable (retries >= 1 guarantees a return or raise above), but keeps
    # type-checkers happy about the function's bytes return.
    raise RuntimeError("unreachable: synthesize exhausted retries without raising")
