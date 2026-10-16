"""Per-segment clean-up of the Spanish article narration, before concatenation.

The raw per-paragraph ElevenLabs clips carry three defects, measured on the
raw stored segments of all 75 narrated articles (2026-10-05, issue #547;
first found on 2026-09-04 and fixed then by a one-off back-catalogue rerun,
PRs #505/#506/#509):

1. **Tail blips** in 13 % of segments, every article affected: a short burst
   at the very end of a clip, after a pause. It is the start of the *next*
   segment's first word, let through by ``next_text`` request stitching
   (headings, which get no ``next_text``, are exempt).
2. **Uneven loudness**: a 10 dB median spread across one article's segments.
3. **Pace outliers**: 8.5 % of paragraphs above 150 wpm against a ~129 wpm
   median. Paragraph *length* does not predict it (r = -0.05), so it cannot be
   avoided at segmentation time.

:func:`process_segment` fixes all three on one clip and writes a WAV;
:func:`assemble` concatenates the WAVs with the narration's silences into the
final MP3. Both go through disk on purpose: holding a whole article as PCM
plus the growing concatenation OOM-killed the first back-catalogue run on
article 69 (132 segments) on the 4 GB, no-swap droplet. The raw MP3 is never
modified — it stays on Spaces under its hash key, so a ``mode="changed"``
regeneration re-processes it from the original.

Slide-deck narration is deliberately *not* processed: it has no ``next_text``
stitching (no blips), half the loudness spread, and runs at the voice's
default speed, so these pace thresholds do not apply to it (#547).
"""

import io
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment
from pydub.silence import detect_nonsilent

# --- tuning (measured on articles 30/58/227/235, 226 segments) ---------------
SILENCE_THRESH_DBFS = -40
MIN_SILENCE_MS = 100
BLIP_MAX_MS = 150  # a "word start" burst; real final words in the sample were >= 180 ms
BLIP_GAP_MS = 150  # silence that must precede a blip for it to count as one
RUN_INTO_EOF_MS = 30  # a range ending this close to EOF after a long pause is bleed-in
RUN_INTO_EOF_GAP_MS = 300
RUN_INTO_EOF_MAX_MS = 1200
LEAD_PAD_MS = 60
TAIL_PAD_MS = 350  # uniform trailing silence; the concat silences go on top
DECAY_PAD_MS = 120  # keep a little natural decay after the last voiced range

# The raw median is ~-23.7 LUFS with high peaks, so the limiter engages on
# about half the segments (46 % of 4,391) — it is part of the normal path,
# and the 2026-09-05 rebuild built this way passed listening.
TARGET_LUFS = -20.0
TRUE_PEAK_DBTP = -1.0

FAST_WPM = 150.0  # ~15 % over the trimmed-speech median
FAST_TARGET_WPM = 140.0
SLOW_WPM = 105.0
SLOW_TARGET_WPM = 112.0
TEMPO_MIN = 0.80
TEMPO_MAX = 1.15
TEMPO_MIN_WORDS = 8

SAMPLE_RATE = 44100

_LUFS_RE = re.compile(r"I:\s+(-?[\d.]+) LUFS")
_PEAK_RE = re.compile(r"Peak:\s+(-?[\d.]+) dBFS")


@dataclass
class ProcessedSegment:
    duration_ms: int
    blips_removed: int
    gain_db: float
    limited: bool
    wpm: float | None
    tempo: float


def _ffmpeg(args: list[str]) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-nostats", "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-800:]}")
    return proc.stderr


def trim_bounds(audio: AudioSegment) -> tuple[int, int, int]:
    """Return ``(start_ms, end_ms, blips_removed)`` for ``audio``.

    ``end_ms`` excludes end-of-file blips (short bursts after a silence gap,
    or a range running into EOF after a long pause) and keeps a short decay.
    """
    ranges = detect_nonsilent(
        audio,
        min_silence_len=MIN_SILENCE_MS,
        silence_thresh=SILENCE_THRESH_DBFS,
        seek_step=5,
    )
    total = len(audio)
    if not ranges:
        return 0, total, 0
    blips = 0
    while len(ranges) > 1:
        s, e = ranges[-1]
        gap = s - ranges[-2][1]
        short_blip = (e - s) <= BLIP_MAX_MS and gap >= BLIP_GAP_MS
        runs_into_eof = (
            (total - e) <= RUN_INTO_EOF_MS
            and gap >= RUN_INTO_EOF_GAP_MS
            and (e - s) <= RUN_INTO_EOF_MAX_MS
        )
        if not (short_blip or runs_into_eof):
            break
        ranges.pop()
        blips += 1
    start = max(0, ranges[0][0] - LEAD_PAD_MS)
    # A removed blip sat >= BLIP_GAP_MS after the last range, beyond the pad.
    end = min(total, ranges[-1][1] + DECAY_PAD_MS)
    return start, end, blips


def measure_loudness(path: str, start_ms: int, end_ms: int) -> tuple[float | None, float | None]:
    """Integrated loudness (LUFS) and true peak (dBFS) of ``path[start:end]``."""
    log = _ffmpeg(
        [
            "-i",
            path,
            "-af",
            f"atrim=start={start_ms / 1000:.3f}:end={end_ms / 1000:.3f},ebur128=peak=true",
            "-f",
            "null",
            "-",
        ]
    )
    summary = log.split("Summary:")[-1]
    lufs = _LUFS_RE.search(summary)
    peak = _PEAK_RE.search(summary)
    return (
        float(lufs.group(1)) if lufs else None,
        float(peak.group(1)) if peak else None,
    )


def tempo_factor(words: int, is_heading: bool, speech_ms: int) -> tuple[float | None, float]:
    """``(wpm, atempo factor)``; factor 1.0 leaves the segment's pace alone.

    Only paragraphs of ``TEMPO_MIN_WORDS``+ are re-paced (a short line's wpm
    is dominated by its pauses), and the factor is clamped so a mis-measured
    segment cannot be stretched into an obvious artefact.
    """
    if is_heading or words < TEMPO_MIN_WORDS or speech_ms <= 0:
        return None, 1.0
    wpm = words / (speech_ms / 60000)
    if wpm > FAST_WPM:
        factor = FAST_TARGET_WPM / wpm
    elif wpm < SLOW_WPM:
        factor = SLOW_TARGET_WPM / wpm
    else:
        return wpm, 1.0
    return wpm, max(TEMPO_MIN, min(TEMPO_MAX, factor))


def wav_duration_ms(path: str) -> int:
    with wave.open(path, "rb") as wav:
        return round(wav.getnframes() * 1000 / wav.getframerate())


def process_segment(mp3: bytes, *, text: str, is_heading: bool, out_wav: str) -> ProcessedSegment:
    """Trim, normalize and re-pace one raw segment into ``out_wav`` (mono 44.1 kHz).

    Nothing decoded outlives the call: the MP3 is decoded once for the silence
    analysis and released, and the processed audio goes straight to disk.
    """
    raw = AudioSegment.from_file(io.BytesIO(mp3), format="mp3")
    start, end, blips = trim_bounds(raw)
    del raw
    with tempfile.TemporaryDirectory() as tmp:
        src = f"{tmp}/in.mp3"
        Path(src).write_bytes(mp3)
        lufs, peak = measure_loudness(src, start, end)
        gain = TARGET_LUFS - lufs if lufs is not None and lufs > -70 else 0.0
        limited = peak is not None and (peak + gain) > TRUE_PEAK_DBTP
        wpm, tempo = tempo_factor(len(text.split()), is_heading, end - start - DECAY_PAD_MS)

        filters = [
            f"atrim=start={start / 1000:.3f}:end={end / 1000:.3f}",
            "asetpts=PTS-STARTPTS",
            f"volume={gain:.2f}dB",
        ]
        if limited:
            # level=0: alimiter's default auto-level scales the output back up
            # so the limit lands at 0 dBFS — limited segments then came out
            # ~1 dB over target and peaking at full scale (the 2026-09-05
            # back-catalogue rebuild has this; measured -0.4 dB peaks, no
            # clipping, so it was not re-run).
            filters.append(
                f"alimiter=limit={10 ** (TRUE_PEAK_DBTP / 20):.4f}:level=0:attack=3:release=60"
            )
        if tempo != 1.0:
            filters.append(f"atempo={tempo:.4f}")
        filters.append(f"apad=pad_dur={TAIL_PAD_MS / 1000:.3f}")
        _ffmpeg(["-i", src, "-af", ",".join(filters), "-ar", str(SAMPLE_RATE), "-ac", "1", out_wav])

    return ProcessedSegment(
        duration_ms=wav_duration_ms(out_wav),
        blips_removed=blips,
        gain_db=round(gain, 2),
        limited=limited,
        wpm=round(wpm, 1) if wpm else None,
        tempo=round(tempo, 3),
    )


def assemble(parts: list[tuple[str, int]], out_mp3: str) -> None:
    """Concatenate processed WAVs into a 128 kbps MP3, streaming through disk.

    ``parts`` is ``[(wav_path, silence_before_ms)]`` in reading order; the
    first entry's silence is ignored. ffmpeg's concat demuxer reads one file at
    a time, so peak memory does not grow with the segment count.
    """
    workdir = Path(out_mp3).parent
    silences: dict[int, str] = {}
    lines: list[str] = []
    for i, (wav, silence_ms) in enumerate(parts):
        if i > 0 and silence_ms > 0:
            if silence_ms not in silences:
                path = str(workdir / f"silence_{silence_ms}.wav")
                _ffmpeg(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        f"anullsrc=r={SAMPLE_RATE}:cl=mono",
                        "-t",
                        f"{silence_ms / 1000:.3f}",
                        path,
                    ]
                )
                silences[silence_ms] = path
            lines.append(f"file '{silences[silence_ms]}'")
        lines.append(f"file '{wav}'")
    concat = workdir / "concat.txt"
    concat.write_text("\n".join(lines) + "\n")
    _ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            out_mp3,
        ]
    )
