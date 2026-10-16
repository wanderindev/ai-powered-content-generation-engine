"""Tests for the narration post-processing (app.services.narration_postprocess, #547).

The blip-trim and tempo-bound logic is pure Python over pydub's generated
audio and runs everywhere. Anything that shells out to ffmpeg (loudness,
filters, assembly, the end-to-end ``synthesize_article`` run) is skipped when
the binary is missing — CI's backend job has none, so exercise those with
``scripts/run-tests.sh``.
"""

import io
import shutil
from unittest.mock import patch

import pytest
from app.services import narration_postprocess as npp
from app.services.article_audio import manifest_total_duration_ms, synthesize_article
from pydub import AudioSegment
from pydub.generators import Sine

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg binary not available"
)


def _tone(ms: int, dbfs: float = -20.0) -> AudioSegment:
    return Sine(220, sample_rate=44100).to_audio_segment(duration=ms, volume=dbfs).set_channels(1)


def _gap(ms: int) -> AudioSegment:
    return AudioSegment.silent(duration=ms, frame_rate=44100).set_channels(1)


def _mp3(audio: AudioSegment) -> bytes:
    out = io.BytesIO()
    audio.export(out, format="mp3", bitrate="128k")
    return out.getvalue()


class TestTrimBounds:
    def test_tail_blip_after_a_pause_is_cut(self):
        # Speech, a pause, then the next segment's first-word burst at EOF.
        audio = _tone(2000) + _gap(400) + _tone(50) + _gap(30)
        start, end, blips = npp.trim_bounds(audio)
        assert blips == 1
        assert start == 0
        # Ends a short decay after the speech, well before the blip.
        assert 2000 <= end <= 2000 + npp.DECAY_PAD_MS + 10

    def test_long_range_running_into_eof_after_long_pause_is_bleed_in(self):
        audio = _tone(2000) + _gap(500) + _tone(700)
        _, end, blips = npp.trim_bounds(audio)
        assert blips == 1
        assert end <= 2000 + npp.DECAY_PAD_MS + 10

    def test_real_final_word_is_kept(self):
        # A 400 ms range that does not run into EOF is a word, not a blip.
        audio = _tone(2000) + _gap(300) + _tone(400) + _gap(300)
        _, end, blips = npp.trim_bounds(audio)
        assert blips == 0
        assert end >= 2700

    def test_leading_silence_is_trimmed_to_a_short_pad(self):
        audio = _gap(800) + _tone(1500) + _gap(200)
        start, _, _ = npp.trim_bounds(audio)
        assert 800 - npp.LEAD_PAD_MS - 10 <= start <= 800 - npp.LEAD_PAD_MS + 10

    def test_all_silence_is_left_alone(self):
        audio = _gap(1000)
        assert npp.trim_bounds(audio) == (0, 1000, 0)


class TestTempoFactor:
    @pytest.mark.parametrize(
        ("wpm", "expected"),
        [
            (160, 140 / 160),  # fast → slowed to 140 wpm
            (200, npp.TEMPO_MIN),  # 140/200 = 0.70, clamped to 0.80
            (100, 112 / 100),  # slow → sped to 112 wpm
            (80, npp.TEMPO_MAX),  # 112/80 = 1.40, clamped to 1.15
            (130, 1.0),  # in band → untouched
            (150, 1.0),  # boundary is exclusive
        ],
    )
    def test_bounds(self, wpm, expected):
        words = 30
        speech_ms = round(words / wpm * 60000)
        measured, factor = npp.tempo_factor(words, False, speech_ms)
        assert measured == pytest.approx(wpm, rel=1e-3)
        assert factor == pytest.approx(expected, rel=1e-3)

    def test_headings_and_short_lines_are_never_repaced(self):
        assert npp.tempo_factor(30, True, 1000) == (None, 1.0)
        assert npp.tempo_factor(npp.TEMPO_MIN_WORDS - 1, False, 500) == (None, 1.0)

    def test_empty_speech_is_left_alone(self):
        assert npp.tempo_factor(30, False, 0) == (None, 1.0)


@requires_ffmpeg
class TestProcessSegment:
    def test_blip_is_removed_and_tail_padded_uniformly(self, tmp_path):
        mp3 = _mp3(_tone(2000) + _gap(400) + _tone(50) + _gap(30))
        out = str(tmp_path / "s.wav")
        seg = npp.process_segment(mp3, text="Corto.", is_heading=False, out_wav=out)
        assert seg.blips_removed == 1
        assert seg.tempo == 1.0  # under TEMPO_MIN_WORDS
        expected = 2000 + npp.DECAY_PAD_MS + npp.TAIL_PAD_MS
        assert abs(seg.duration_ms - expected) <= 80  # MP3 encoder padding
        tail = AudioSegment.from_wav(out)[-npp.TAIL_PAD_MS + 20 :]
        assert tail.max_dBFS < -60

    @pytest.mark.parametrize("dbfs", [-38.0, -8.0])
    def test_loudness_is_normalized_to_target(self, tmp_path, dbfs):
        mp3 = _mp3(_tone(3000, dbfs) + _gap(200))
        out = str(tmp_path / "s.wav")
        npp.process_segment(mp3, text="Una frase.", is_heading=True, out_wav=out)
        lufs, _ = npp.measure_loudness(out, 0, 3000)
        assert lufs == pytest.approx(npp.TARGET_LUFS, abs=1.0)

    def test_limiter_engages_only_when_the_gain_would_clip(self, tmp_path):
        # Quiet speech with one full-scale click: the +gain would clip it.
        spiky = _tone(1500, -30) + _tone(20, -0.5) + _tone(1500, -30) + _gap(200)
        out = str(tmp_path / "s.wav")
        seg = npp.process_segment(_mp3(spiky), text="Uno.", is_heading=True, out_wav=out)
        assert seg.gain_db > 3
        assert seg.limited is True
        _, peak = npp.measure_loudness(out, 0, seg.duration_ms)
        assert peak <= npp.TRUE_PEAK_DBTP + 0.5

        calm = _tone(3000, -30) + _gap(200)
        seg = npp.process_segment(
            _mp3(calm), text="Uno.", is_heading=True, out_wav=str(tmp_path / "c.wav")
        )
        assert seg.limited is False

    def test_fast_paragraph_is_slowed_within_bounds(self, tmp_path):
        # 30 words over ~4 s of speech ≈ 450 wpm → clamped to the 0.80 floor.
        mp3 = _mp3(_tone(4000) + _gap(100))
        out = str(tmp_path / "s.wav")
        seg = npp.process_segment(
            mp3, text=" ".join(["palabra"] * 30), is_heading=False, out_wav=out
        )
        assert seg.tempo == npp.TEMPO_MIN
        expected = (4000 + npp.DECAY_PAD_MS) / npp.TEMPO_MIN + npp.TAIL_PAD_MS
        assert abs(seg.duration_ms - expected) <= 120


@requires_ffmpeg
class TestAssemble:
    def test_concatenates_with_silences_through_disk(self, tmp_path):
        parts = []
        for i, ms in enumerate((1000, 1500, 800)):
            path = str(tmp_path / f"{i}.wav")
            _tone(ms).export(path, format="wav")
            parts.append((path, 1400 if i == 1 else 1000))
        out = str(tmp_path / "out.mp3")
        npp.assemble(parts, out)
        # First silence is ignored: 1000 + 1400 + 1500 + 1000 + 800.
        assert abs(len(AudioSegment.from_mp3(out)) - 5700) <= 80


@requires_ffmpeg
def test_synthesize_article_end_to_end(tmp_path):
    """Real ffmpeg, mocked TTS + storage: blips cut, manifest = processed lengths."""
    raw = _mp3(_tone(2000) + _gap(400) + _tone(50) + _gap(30))
    with (
        patch("app.services.article_audio.synthesize", return_value=raw),
        patch("app.services.article_audio.ImageStorageService") as storage_cls,
    ):
        result = synthesize_article(
            7, "Título", "Primer párrafo.\n\n## Sección\n\nSegundo párrafo.", mode="all"
        )

    storage = storage_cls.return_value
    # The stored segments are the raw clips, untouched.
    assert all(c.args[0] == raw for c in storage.upload_bytes.call_args_list)
    assert result.segments == result.synthesized == 4
    per_segment = 2000 + npp.DECAY_PAD_MS + npp.TAIL_PAD_MS
    for entry in result.manifest:
        assert abs(entry["duration_ms"] - per_segment) <= 80
    assert result.duration_ms == manifest_total_duration_ms(result.manifest)
    assert abs(len(AudioSegment.from_mp3(io.BytesIO(result.mp3))) - result.duration_ms) <= 150
