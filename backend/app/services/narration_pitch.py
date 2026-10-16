"""Pitch (median F0) of one narration clip, for the narration voice check (#601).

ElevenLabs occasionally returns a paragraph that does not sound like Diego.
Calibration on the whole back catalogue (#600, 3,254 measurable paragraphs and
two blind listening rounds) found every such take at least +16.9 % above its
article's pitch baseline, so the narration job re-takes clips that sit too far
*above* the baseline (see ``article_audio``). This module only measures.

The estimator is a port of ``f0_frames`` in
``scripts/one_off/measure_narration_pitch.py``, kept **exactly as measured** so
the 3 s cutoff and the 15 % threshold calibrated with it still hold: 16 kHz
mono, 40 ms frames with a 10 ms hop, FFT autocorrelation searched over
60-300 Hz; a frame is voiced when its autocorrelation peak is > 0.6 and its RMS
is > 0.3 x the clip RMS. pYIN agreed with it within 1-5 Hz.

Note (PR #603 review): ``ac / ac[0]`` normalises a *biased* autocorrelation,
so the reachable peak shrinks by ``(640 - lag) / 640``. The 0.6 voicing gate is
therefore stricter at low pitch — a 60 Hz frame can never pass, and the ceiling
is ~0.87 at 197 Hz. The medians are unaffected and Diego sits at ~144 Hz, so
the calibration stands; switching to an unbiased / NCCF normalisation would
mean re-running the measurement script before trusting the thresholds.

Memory: one clip is decoded at a time (a 60 s paragraph is ~4 MB of float32 at
16 kHz) and released on return — the narration job's rule that no
whole-article PCM is ever held (``narration_postprocess``) still applies.
The frame matrix and its FFTs are built ``CHUNK_FRAMES`` at a time (#607): all
at once they peaked at 415 MB (tracemalloc) on a 90 s paragraph, 46 MB chunked,
and the job pitch-checks segments in a worker pool on a droplet with no swap.
"""

import math
import subprocess

import numpy as np

SAMPLE_RATE = 16000
FRAME = int(0.04 * SAMPLE_RATE)  # 640 samples
HOP = int(0.01 * SAMPLE_RATE)  # 160 samples -> one voiced frame = 10 ms
F0_MIN, F0_MAX = 60, 300
AC_VOICED = 0.6
RMS_REL = 0.3

# Below this much voicing a segment is not measured: at 3 s the p99 error of a
# window median against the whole-clip median is 14 % (#600), already close to
# the 15 % threshold.
MIN_VOICED_SECONDS = 3.0

# Frames per FFT batch (#607). Memory only — the result does not depend on it.
# Building every frame of a clip at once peaked at 415 MB on a 90 s paragraph;
# 1,000 frames (10 s of audio) holds it at ~46 MB whatever the clip length.
CHUNK_FRAMES = 1000


def decode(mp3: bytes) -> np.ndarray:
    """Decode an MP3 to 16 kHz mono float32 through ffmpeg (stdin -> stdout)."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ],
        input=mp3,
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)


def f0_frames(x: np.ndarray, chunk_frames: int = CHUNK_FRAMES) -> np.ndarray:
    """Per-frame F0 (Hz) of the voiced frames of ``x`` (16 kHz mono).

    Frames are processed ``chunk_frames`` at a time (#607). Every frame is
    estimated independently and the one clip-wide value, ``clip_rms``, is
    computed once over the whole clip, so the result is bit-identical to the
    unchunked estimator for any chunk size.
    """
    if len(x) < FRAME:
        return np.array([])
    n = 1 + (len(x) - FRAME) // HOP
    clip_rms = math.sqrt(float((x.astype(np.float64) ** 2).mean()))
    rms_floor = RMS_REL * clip_rms
    offsets = np.arange(FRAME)[None, :]
    chunks = [
        _f0_chunk(
            x, offsets + HOP * np.arange(start, min(start + chunk_frames, n))[:, None], rms_floor
        )
        for start in range(0, n, chunk_frames)
    ]
    return np.concatenate(chunks)


def _f0_chunk(x: np.ndarray, idx: np.ndarray, rms_floor: float) -> np.ndarray:
    """F0 of the voiced frames among the frames whose sample indices are ``idx``."""
    frames = x[idx].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    rms = np.sqrt((frames**2).mean(axis=1))
    keep = rms >= rms_floor
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


def median_f0_samples(x: np.ndarray, min_voiced_s: float = MIN_VOICED_SECONDS) -> float | None:
    """Median F0 of ``x``, or ``None`` when it has under ``min_voiced_s`` voiced."""
    f0 = f0_frames(x)
    if len(f0) * HOP / SAMPLE_RATE < min_voiced_s:
        return None
    return float(np.median(f0))


def median_f0(mp3: bytes, min_voiced_s: float = MIN_VOICED_SECONDS) -> float | None:
    """Median F0 (Hz) of a raw MP3 clip, or ``None`` when too little is voiced."""
    return median_f0_samples(decode(mp3), min_voiced_s)
