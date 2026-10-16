"""Pitch estimator for the narration voice check (#601), on synthetic tones."""

import math
import shutil
import subprocess

import numpy as np
import pytest
from app.services import narration_pitch
from app.services.narration_pitch import (
    AC_VOICED,
    CHUNK_FRAMES,
    F0_MAX,
    F0_MIN,
    FRAME,
    HOP,
    RMS_REL,
    SAMPLE_RATE,
    f0_frames,
    median_f0,
    median_f0_samples,
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg binary not available"
)


def _voice(hz: float, seconds: float, harmonics: int = 4) -> np.ndarray:
    """A voice-like tone: fundamental plus decaying harmonics, float32 16 kHz."""
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    x = sum(np.sin(2 * np.pi * hz * k * t) / k for k in range(1, harmonics + 1))
    return (0.3 * x / np.abs(x).max()).astype(np.float32)


@pytest.mark.parametrize("hz", [110.0, 144.0, 197.5])
def test_median_tracks_the_fundamental(hz):
    est = median_f0_samples(_voice(hz, 4.0))
    # Integer-lag resolution at 16 kHz: within 1 % in Diego's range.
    assert est == pytest.approx(hz, rel=0.01)


def test_a_fifteen_percent_shift_is_resolved():
    base = median_f0_samples(_voice(144.0, 4.0))
    high = median_f0_samples(_voice(144.0 * 1.17, 4.0))
    assert (high - base) / base > 0.15


def test_too_little_voicing_is_not_measured():
    # 2 s of voice: under the 3 s minimum, where the p99 error reaches 14 %.
    assert median_f0_samples(_voice(144.0, 2.0)) is None
    # Same tone padded with silence: silence is not voiced, still too short.
    padded = np.concatenate([_voice(144.0, 2.0), np.zeros(SAMPLE_RATE * 3, np.float32)])
    assert median_f0_samples(padded) is None


def test_silence_and_noise_are_unvoiced():
    assert len(f0_frames(np.zeros(SAMPLE_RATE * 4, np.float32))) == 0
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.1, SAMPLE_RATE * 4).astype(np.float32)
    # White noise has no autocorrelation peak above 0.6.
    assert median_f0_samples(noise) is None


def test_shorter_than_one_frame():
    assert len(f0_frames(np.zeros(10, np.float32))) == 0


def test_biased_normalisation_is_kept_as_measured():
    """The PR #603 note: the biased AC caps the reachable peak at
    (640 - lag) / 640, so a 60 Hz frame can never pass the 0.6 gate. The
    estimator is kept exactly as calibrated in #600; this pins that choice."""
    assert len(f0_frames(_voice(60.0, 4.0, harmonics=1))) == 0


@requires_ffmpeg
def test_median_f0_decodes_an_mp3(tmp_path):
    wav = tmp_path / "tone.f32"
    wav.write_bytes(_voice(150.0, 4.0).tobytes())
    mp3 = tmp_path / "tone.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-f",
            "f32le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-i",
            str(wav),
            "-ar",
            "44100",
            str(mp3),
        ],
        check=True,
    )
    assert median_f0(mp3.read_bytes()) == pytest.approx(150.0, rel=0.01)


@requires_ffmpeg
def test_undecodable_bytes_raise():
    with pytest.raises(subprocess.CalledProcessError):
        narration_pitch.decode(b"not an mp3")


def _unchunked_f0_frames(x: np.ndarray) -> np.ndarray:
    """``f0_frames`` exactly as it was before #607 chunked it (the calibrated
    estimator, whole frame matrix at once) — the reference for equality."""
    if len(x) < FRAME:
        return np.array([])
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    rms = np.sqrt((frames**2).mean(axis=1))
    clip_rms = math.sqrt(float((x.astype(np.float64) ** 2).mean()))
    keep = rms >= RMS_REL * clip_rms
    frames = frames[keep]
    if not len(frames):
        return np.array([])
    spec = np.fft.rfft(frames, n=2 * FRAME, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :FRAME]
    ac /= ac[:, :1] + 1e-12
    lo, hi = SAMPLE_RATE // F0_MAX, SAMPLE_RATE // F0_MIN
    lag = lo + np.argmax(ac[:, lo:hi], axis=1)
    peak = ac[np.arange(len(ac)), lag]
    return SAMPLE_RATE / lag[peak > AC_VOICED]


def _long_varied_signal() -> np.ndarray:
    """Several chunks of audio whose loudness and pitch vary, with silences and
    noise, so the voiced mask (frame RMS vs the *clip* RMS) differs per chunk."""
    rng = np.random.default_rng(607)
    parts = []
    for i, hz in enumerate([110.0, 144.0, 160.0, 197.5, 130.0, 175.0, 120.0]):
        parts.append(_voice(hz, 4.3) * (0.2 + 0.4 * (i % 3)))
        parts.append(np.zeros(int(0.37 * SAMPLE_RATE), np.float32))
        parts.append(rng.normal(0, 0.02, int(0.8 * SAMPLE_RATE)).astype(np.float32))
    return np.concatenate(parts)


_FIXTURES = [
    _voice(110.0, 4.0),
    _voice(144.0, 4.0),
    _voice(197.5, 4.0),
    _voice(144.0 * 1.17, 4.0),
    _voice(144.0, 2.0),
    np.concatenate([_voice(144.0, 2.0), np.zeros(SAMPLE_RATE * 3, np.float32)]),
    np.zeros(SAMPLE_RATE * 4, np.float32),
    np.random.default_rng(0).normal(0, 0.1, SAMPLE_RATE * 4).astype(np.float32),
    np.zeros(10, np.float32),
    _voice(60.0, 4.0, harmonics=1),
]


@pytest.mark.parametrize("x", _FIXTURES)
def test_chunking_matches_the_calibrated_estimator_on_the_fixtures(x):
    """#607: chunking the FFT must not move a single frame's estimate."""
    np.testing.assert_array_equal(f0_frames(x), _unchunked_f0_frames(x))


def test_chunked_equals_unchunked_on_a_signal_longer_than_one_chunk():
    x = _long_varied_signal()
    n_frames = 1 + (len(x) - FRAME) // HOP
    assert n_frames > 3 * CHUNK_FRAMES
    reference = _unchunked_f0_frames(x)
    assert len(reference) > 0
    # Default chunking, one chunk spanning the clip, and an awkward chunk size
    # that splits voiced runs and leaves a short final chunk: all identical.
    for chunk in (CHUNK_FRAMES, n_frames, 37):
        got = f0_frames(x, chunk_frames=chunk)
        assert got.dtype == reference.dtype
        np.testing.assert_array_equal(got, reference)
