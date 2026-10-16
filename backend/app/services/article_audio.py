"""Spanish article narration — Diego's voice reading the whole article.

The technique is the one validated on article 227 (2026-08-31): ElevenLabs
``eleven_multilingual_v2`` with **one generation per paragraph**, stitched with
``previous_text``/``next_text`` so prosody flows across segments. Long
single-shot generations start energetic and flatten into a monotone; short
per-paragraph generations re-energize the voice the way the slide pipeline's
per-slide segments do. Pauses are real silence inserted at concat time —
1.0s between paragraphs, 1.4s before a section heading — not ``<break>`` tags.

eleven_v3 was A/B-tested and rejected: better acting, but it reproduces the
cloned voice too loosely to pass as Diego.

Selective regeneration: every synthesized segment is also uploaded to Spaces
under a content-hash key, and the ordered list of ``{hash, key, is_heading,
duration_ms}`` dicts (the *manifest*) is persisted on
``articles.audio_manifest_es``. A ``mode="changed"`` run reuses the stored MP3
for every segment whose text hash is still in the manifest and pays ElevenLabs
only for edited/new segments, then re-concatenates the whole read. Headings are
synthesized WITHOUT ``next_text``: feeding the section opener as next-context
made the voice lean forward into an unfinished, mid-sentence inflection even
with a terminal period.

Post-processing (#547): before concatenation every segment is trimmed of
tail blips, loudness-normalized and pace-corrected by
``narration_postprocess``, and the read is assembled through disk. The
segments stored on Spaces stay **raw**, so each run processes from the
originals, and the manifest's ``duration_ms`` is the *processed* length.

Voice check (#601): every non-heading raw clip is pitch-measured
(``narration_pitch``) while the run proceeds. After the first pass, a newly
synthesized clip more than ``PITCH_SUSPECT_THRESHOLD`` above the article's own
baseline is re-requested from ElevenLabs (up to ``MAX_RETAKES`` times) when
suspects are under ``REVIEW_CUTOFF`` of the measurable segments; at or above
the cutoff nothing is retried and the run is flagged for the operator. The
accepted take overwrites the raw segment at its hash key and is the one that
gets post-processed, so the final read and the manifest reflect it. If that
post-processing or upload fails, the take is dropped and the original stays
(#608): the paid run still succeeds and the paragraph is reported unresolved
with a ``reason``. The
result is returned as ``NarrationResult.qa`` and persisted on
``articles.audio_qa_es``.

The synthesis functions take plain strings and touch no DB session — the
endpoint closes its session before calling (a full article is several minutes
of external I/O, and a pinned connection for that long is exactly what the
pool-budget rules in CLAUDE.md forbid).
"""

import hashlib
import logging
import re
import statistics
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import sentry_sdk
from elevenlabs import VoiceSettings

from app.services.audio_tts import synthesize
from app.services.image_storage import ImageStorageService
from app.services.narration_pitch import median_f0
from app.services.narration_postprocess import assemble, process_segment

logger = logging.getLogger(__name__)

# --- voice check (#601) -------------------------------------------------------
# A newly synthesized paragraph more than 15 % ABOVE the article's pitch
# baseline is re-taken. Calibrated in #600: every take the operator labelled
# "not Diego" was at least +16.9 % above its baseline (the closest bad take),
# so don't raise it above 0.15; at 0.12 the catalogue would re-take 95 clips
# instead of 38. Downward deviation is not a signal (the corpus tail runs
# upward only, and the low clips checked by ear were fine).
PITCH_SUSPECT_THRESHOLD = 0.15
# At or above this share of suspects among the measurable segments nothing is
# re-taken and the narration is flagged for review — an anomaly alarm (no
# back-catalogue article reached it at any threshold >= 15 %), not a routine
# path.
REVIEW_CUTOFF = 0.10
MAX_RETAKES = 2

QA_OK = "OK"
QA_FIXED = "FIXED"
QA_NEEDS_REVIEW = "NEEDS_REVIEW"

_QA_PREVIEW_CHARS = 120

# Measured-good settings from the article 227 test: stability lowered for
# intonation variety, mild style, 0.9 speed for a calm read.
ARTICLE_VOICE_SETTINGS = VoiceSettings(stability=0.40, similarity_boost=0.75, style=0.30, speed=0.9)

PARAGRAPH_SILENCE_MS = 1000
HEADING_SILENCE_MS = 1400

# Two ffmpeg workers: the droplet has 2 vCPUs, and a 132-segment article
# processes in ~30 s at 2 CPUs (46 s single-threaded) — measured for #547.
POSTPROCESS_WORKERS = 2

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"\*([^*]+)\*")
_CTA_HEADER = re.compile(r"^\**\s*(Continuar leyendo|Continue Reading)\s*\**\s*$", re.I)

# Narration-only respellings: the article text keeps the correct form, the
# voice gets the one that makes ElevenLabs say it the Panamanian way. Matched
# on the word start so ``Ngäbe-Buglé`` / ``ngäbes`` are covered too.
_PRONUNCIATION = (
    (re.compile(r"\bNgäbe"), "Nobe"),
    (re.compile(r"\bngäbe"), "nobe"),
)

# Section headings that are not numbered "Parte N": the intro and conclusion
# of a series member (``## Introducción: …`` / ``## Conclusión: …``).
_UNNUMBERED_HEADING = re.compile(r"^(Introducci[oó]n|Conclusi[oó]n(es)?)\b", re.I)


def _clean(block: str) -> str:
    text = block.lstrip("#").strip()
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _spoken(text: str) -> str:
    for pattern, replacement in _PRONUNCIATION:
        text = pattern.sub(replacement, text)
    return text


def segment_article(title: str, content_md: str) -> list[tuple[str, bool]]:
    """Split article markdown into narration segments: ``[(text, is_heading)]``.

    The title is the first segment. The series-navigation preamble (everything
    before the first horizontal rule, when one exists) is not narrated, and a
    trailing horizontal rule ends the read — it introduces the
    "Continuar leyendo" call-to-action, which is navigation, not prose.

    Section headings are spoken as ``Parte N: <heading>.`` — a bare heading
    dropped after a paragraph confused listeners — except the intro and
    conclusion, which keep their own label. Narration-only respellings
    (``_PRONUNCIATION``) are applied to every segment; the article text is
    untouched.
    """
    body = content_md.split("\n---\n", 1)[1] if "\n---\n" in content_md else content_md
    segments: list[tuple[str, bool]] = []
    if title.strip():
        title_text = _spoken(_clean(title))
        segments.append(
            (title_text if title_text.endswith((".", "?", "!")) else title_text + ".", True)
        )
    part = 0
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        if set(block) <= {"-"}:  # trailing hr — the CTA block starts here
            break
        if _CTA_HEADER.match(block):
            # Older series members carry the "Continuar leyendo" block with no
            # rule in front of it (36 narrations read it aloud before this
            # guard existed), so the header itself also ends the read.
            break
        is_heading = block.startswith("#")
        text = _spoken(_clean(block))
        if not text:
            continue
        if is_heading:
            if not _UNNUMBERED_HEADING.match(text):
                part += 1
                text = f"Parte {part}: {text}"
            if not text.endswith((".", "?", "!")):
                text += "."
        segments.append((text, is_heading))
    return segments


def segment_hash(text: str) -> str:
    """Stable content hash for one narration segment (16 hex chars)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _segment_key(article_id: int, text_hash: str) -> str:
    return f"articles/audio/{article_id}/segments/{text_hash}.mp3"


def manifest_total_duration_ms(manifest: list[dict] | None) -> int | None:
    """Total narration length: segment durations plus the concat silences.

    Mirrors the assembly loop in :func:`synthesize_article` (1.0s between
    paragraphs, 1.4s before a heading, nothing before the first segment), so
    the number matches what the player will report. ``None`` for pre-manifest
    narrations.
    """
    if not manifest:
        return None
    total = 0
    for i, entry in enumerate(manifest):
        if i > 0:
            total += HEADING_SILENCE_MS if entry.get("is_heading") else PARAGRAPH_SILENCE_MS
        total += int(entry.get("duration_ms") or 0)
    return total


def segment_start_ms(manifest: list[dict], position: int) -> int:
    """Where segment ``position`` starts in the final read, in ms.

    Same arithmetic as :func:`manifest_total_duration_ms` (the segments before
    it plus their concat silences, plus the silence in front of it), so it
    matches the player's clock.
    """
    if position <= 0:
        return 0
    entry = manifest[position]
    silence = HEADING_SILENCE_MS if entry.get("is_heading") else PARAGRAPH_SILENCE_MS
    return (manifest_total_duration_ms(manifest[:position]) or 0) + silence


@dataclass
class NarrationResult:
    mp3: bytes
    duration_ms: int
    segments: int
    synthesized: int
    reused: int
    manifest: list[dict]
    # Voice-check result (#601), persisted on articles.audio_qa_es.
    qa: dict | None = None


def _measure_pitch(mp3: bytes) -> float | None:
    """Median F0 of a raw clip; ``None`` when unmeasurable. Never raises: a
    voice check that cannot decode a clip must not fail a paid narration."""
    try:
        return median_f0(mp3)
    except Exception as exc:
        logger.warning("Narration pitch measurement failed: %s", exc)
        return None


def _deviation(hz: float, baseline: float) -> float:
    return (hz - baseline) / baseline


def _synthesize_segment(plan: list[tuple[str, bool, str]], i: int) -> bytes:
    """One ElevenLabs call for ``plan[i]`` with its neighbour context."""
    text, is_heading, _ = plan[i]
    return synthesize(
        text,
        voice_settings=ARTICLE_VOICE_SETTINGS,
        previous_text=plan[i - 1][0] if i > 0 else None,
        # Headings get no next-context: it made the voice read them with an
        # unfinished, leaning-forward inflection.
        next_text=plan[i + 1][0] if not is_heading and i < len(plan) - 1 else None,
    )


@dataclass
class _Retake:
    position: int
    hz_before: float
    hz_after: float
    attempts: int
    resolved: bool
    mp3: bytes | None  # the accepted take, or None when the first take stays
    # Why an accepted take was NOT swapped in (#608): its post-processing or
    # upload failed, so the original clip stays in the read and at its key.
    reason: str | None = None


def _retake(
    plan: list[tuple[str, bool, str]], i: int, hz_before: float, baseline: float
) -> _Retake:
    """Re-request ``plan[i]`` until a take is within the threshold.

    Same text, neighbour context and voice settings — ElevenLabs is
    non-deterministic at stability 0.40, which is what makes a re-take work.
    Keeps the first take within the threshold; otherwise the lowest-pitch one
    (possibly the original), reported as unresolved.
    """
    best_hz, best_mp3 = hz_before, None
    attempts = 0
    for attempt in range(1, MAX_RETAKES + 1):
        try:
            mp3 = _synthesize_segment(plan, i)
        except Exception as exc:
            logger.warning("Narration re-take of segment %d failed: %s", i, exc)
            break
        attempts = attempt
        hz = _measure_pitch(mp3)
        logger.info(
            "Narration re-take segment %d attempt %d: %.1f Hz -> %s Hz (baseline %.1f)",
            i,
            attempt,
            hz_before,
            f"{hz:.1f}" if hz is not None else "unmeasurable",
            baseline,
        )
        if hz is None:
            continue
        if _deviation(hz, baseline) <= PITCH_SUSPECT_THRESHOLD:
            return _Retake(i, hz_before, hz, attempts, True, mp3)
        if hz < best_hz:
            best_hz, best_mp3 = hz, mp3
    return _Retake(i, hz_before, best_hz, attempts, False, best_mp3)


def _apply_retake(
    storage: ImageStorageService,
    article_id: int,
    plan: list[tuple[str, bool, str]],
    take: _Retake,
    processed: list,
    wavs: list[str],
    tmp: str,
) -> None:
    """Swap an accepted take into the read and its hash key, or keep the original.

    A re-take is an improvement, not a requirement (#608): the ElevenLabs
    synthesis of the whole article is already paid for, so a failed ffmpeg
    pass or Spaces upload on one take must not fail the run. The order keeps
    the manifest invariant (the raw clip stored at a hash key is the one whose
    processed duration the manifest records): the take is post-processed into
    its OWN WAV first, uploaded only if that succeeded, and adopted only if
    both did. On any failure the original raw object, WAV and ``processed[i]``
    are untouched and the take is reported unresolved with a reason.
    """
    i = take.position
    text, is_heading, text_hash = plan[i]
    out_wav = f"{tmp}/{i:04d}.retake.wav"
    step = "post-processing"
    try:
        seg = process_segment(take.mp3, text=text, is_heading=is_heading, out_wav=out_wav)
        step = "upload"
        storage.upload_bytes(take.mp3, _segment_key(article_id, text_hash), "audio/mpeg")
    except Exception as exc:
        logger.warning(
            "Narration re-take of segment %d not applied (%s failed: %s); keeping the original",
            i,
            step,
            exc,
        )
        sentry_sdk.capture_exception(exc)
        take.reason = f"re-take {step} failed: {exc}"[:200]
        # The read keeps the original clip, so report the original's pitch.
        take.hz_after = take.hz_before
        take.resolved = False
        take.mp3 = None
        return
    processed[i] = seg
    wavs[i] = out_wav


def _voice_check(
    plan: list[tuple[str, bool, str]], pitches: dict[int, float | None], fresh: set[int]
) -> dict:
    """First-pass verdict: baseline, suspects, and whether to re-take them.

    The baseline is the median of every measurable segment (reused ones in a
    ``"changed"`` run included — they calibrate the voice too). Only segments
    synthesized in this run are suspects: reused ones were already accepted.
    """
    measurable = {i: hz for i, hz in pitches.items() if hz is not None}
    baseline = statistics.median(measurable.values()) if measurable else None
    suspects = (
        sorted(
            i
            for i, hz in measurable.items()
            if i in fresh and _deviation(hz, baseline) > PITCH_SUSPECT_THRESHOLD
        )
        if baseline
        else []
    )
    return {
        "baseline": baseline,
        "measured": len(measurable),
        "skipped": len(plan) - len(measurable),
        "suspects": suspects,
        "retry": bool(suspects) and len(suspects) / len(measurable) < REVIEW_CUTOFF,
        "retakes": [],
    }


def _qa_result(
    plan: list[tuple[str, bool, str]],
    manifest: list[dict],
    pitches: dict[int, float | None],
    check: dict,
    retake_calls: int,
) -> dict:
    """The ``articles.audio_qa_es`` document, timestamps from the final manifest."""
    baseline = check["baseline"]
    retried, unresolved = [], []
    for take in check["retakes"]:
        # Only a take that could not be swapped in (#608) carries a reason.
        reason = {"reason": take.reason} if take.reason else {}
        retried.append(
            _qa_entry(plan, manifest, take.position)
            | {
                "hz_before": round(take.hz_before, 1),
                "hz_after": round(take.hz_after, 1),
                "pct_before": _pct(take.hz_before, baseline),
                "pct_after": _pct(take.hz_after, baseline),
                "attempts": take.attempts,
                "resolved": take.resolved,
            }
            | reason
        )
        if not take.resolved:
            unresolved.append(
                _qa_entry(plan, manifest, take.position)
                | {
                    "hz": round(take.hz_after, 1),
                    "pct": _pct(take.hz_after, baseline),
                    "attempts": take.attempts,
                }
                | reason
            )
    if check["suspects"] and not check["retry"]:
        # The alarm path: nothing re-taken, every suspect is one to listen to.
        for i in check["suspects"]:
            unresolved.append(
                _qa_entry(plan, manifest, i)
                | {"hz": round(pitches[i], 1), "pct": _pct(pitches[i], baseline), "attempts": 0}
            )
    if unresolved:
        status = QA_NEEDS_REVIEW
    elif retried:
        status = QA_FIXED
    else:
        status = QA_OK
    return {
        "status": status,
        "baseline_hz": round(baseline, 1) if baseline else None,
        "threshold_pct": round(PITCH_SUSPECT_THRESHOLD * 100, 1),
        "review_cutoff_pct": round(REVIEW_CUTOFF * 100, 1),
        "segments": len(plan),
        "measured": check["measured"],
        "skipped": check["skipped"],
        "suspects": len(check["suspects"]),
        "retake_calls": retake_calls,
        "retried": retried,
        "unresolved": unresolved,
    }


def _qa_entry(plan: list[tuple[str, bool, str]], manifest: list[dict], i: int) -> dict:
    """Where to listen: position (1-based, title included — the numbering of
    the drawer's ``audio-segments`` chooser), start timestamp in the final
    read, and a text preview."""
    return {
        "position": i + 1,
        "start_ms": segment_start_ms(manifest, i),
        "text": plan[i][0][:_QA_PREVIEW_CHARS],
    }


def _pct(hz: float, baseline: float) -> float:
    return round(_deviation(hz, baseline) * 100, 1)


def summarize_qa(qa: dict | None) -> dict | None:
    """The compact voice-check summary carried in the job ``stats``."""
    if not qa:
        return None
    return {
        "status": qa["status"],
        "baseline_hz": qa["baseline_hz"],
        "measured": qa["measured"],
        "skipped": qa["skipped"],
        "suspects": qa["suspects"],
        "retried": len(qa["retried"]),
        "unresolved": len(qa["unresolved"]),
        "retake_calls": qa["retake_calls"],
    }


def plan_narration(
    title: str, content_md: str, manifest: list[dict] | None = None, mode: str = "all"
) -> list[tuple[str, bool, str]]:
    """The ordered ``[(text, is_heading, hash)]`` a run would narrate.

    Pure (no I/O), so the endpoint can refuse a pointless run with a 400
    before it queues a background job. Raises ``ValueError`` when there is
    nothing to narrate, or when a ``"changed"`` run finds no edited segments
    (nothing to pay for — the stored audio already matches the text).
    """
    segments = segment_article(title, content_md)
    if not segments or len(segments) < 2:
        raise ValueError("Article has no narratable Spanish content")
    plan = [(text, is_heading, segment_hash(text)) for text, is_heading in segments]
    stored = {entry["hash"] for entry in (manifest or [])}
    if mode == "changed" and all(h in stored for _, _, h in plan):
        raise ValueError("No changed segments — the stored audio already matches the Spanish text")
    return plan


def synthesize_article(
    article_id: int,
    title: str,
    content_md: str,
    manifest: list[dict] | None = None,
    mode: str = "all",
    progress: Callable[[int, int, str], None] | None = None,
) -> NarrationResult:
    """Narrate an article, reusing stored per-segment MP3s when ``mode="changed"``.

    ``mode="all"`` synthesizes every segment; ``mode="changed"`` reuses the
    Spaces object for any segment whose text hash appears in ``manifest``
    (matching is by content, so splitting/merging paragraphs only re-pays for
    the actually-edited text) and synthesizes the rest. Either way every
    segment MP3 ends up stored under its hash key, the new manifest is
    returned for the caller to persist, and orphaned segment objects from the
    old manifest are best-effort deleted.

    Raises ``ValueError`` as :func:`plan_narration` does. ``progress(done,
    total, current)`` is called once per segment as its audio is obtained
    (the background job commits it to the job row).

    High-pitch takes are re-requested before the read is assembled (the voice
    check, see the module docstring); ``NarrationResult.qa`` records it.

    Touches no DB — see the module docstring for why the caller must close its
    session first.
    """
    plan = plan_narration(title, content_md, manifest, mode)
    by_hash = {entry["hash"]: entry for entry in (manifest or [])}

    synthesized = reused = 0
    storage = ImageStorageService()
    with tempfile.TemporaryDirectory(prefix="narration_") as tmp:
        # Post-processing runs on a small pool while the next segments are
        # still being synthesized, so its ffmpeg time mostly hides behind the
        # TTS I/O. Only compressed MP3 bytes wait in the queue; decoded audio
        # lives in one worker call at a time (see narration_postprocess).
        with ThreadPoolExecutor(max_workers=POSTPROCESS_WORKERS) as pool:
            futures: list[Future] = []
            pitch_futures: dict[int, Future] = {}
            fresh: set[int] = set()  # positions synthesized in this run
            try:
                for i, (text, is_heading, text_hash) in enumerate(plan):
                    mp3 = None
                    if mode == "changed" and text_hash in by_hash:
                        try:
                            mp3 = storage.download_bytes(by_hash[text_hash]["key"])
                            reused += 1
                            logger.info(
                                "Segment %d/%d reused (hash %s)", i + 1, len(plan), text_hash
                            )
                        except Exception as exc:
                            logger.warning(
                                "Stored segment %s unavailable (%s); re-synthesizing",
                                text_hash,
                                exc,
                            )
                    if mp3 is None:
                        mp3 = _synthesize_segment(plan, i)
                        # The RAW clip is what gets stored: a later "changed"
                        # run re-processes it from the original.
                        storage.upload_bytes(mp3, _segment_key(article_id, text_hash), "audio/mpeg")
                        synthesized += 1
                        fresh.add(i)
                        logger.info("Segment %d/%d synthesized", i + 1, len(plan))
                    if progress is not None:
                        progress(i + 1, len(plan), f"Segment {i + 1}/{len(plan)}")
                    # Voice check (#601): pitch of the RAW clip, measured on
                    # the pool like the post-processing (one decode at a
                    # time per worker). Headings are too short to measure.
                    if not is_heading:
                        pitch_futures[i] = pool.submit(_measure_pitch, mp3)
                    futures.append(
                        pool.submit(
                            process_segment,
                            mp3,
                            text=text,
                            is_heading=is_heading,
                            out_wav=f"{tmp}/{i:04d}.wav",
                        )
                    )
                    # Fail fast: a broken ffmpeg should not keep paying
                    # ElevenLabs for the rest of the article. The raw segments
                    # already stored make a "changed" retry cheap.
                    for f in futures:
                        if f.done() and f.exception() is not None:
                            f.result()
                processed = [f.result() for f in futures]
                # The WAV each position is assembled from; an applied re-take
                # repoints its entry at the re-take's own file.
                wavs = [f"{tmp}/{i:04d}.wav" for i in range(len(plan))]
                pitches = {i: f.result() for i, f in pitch_futures.items()}

                check = _voice_check(plan, pitches, fresh)
                retake_calls = 0
                for i in check["suspects"] if check["retry"] else []:
                    take = _retake(plan, i, pitches[i], check["baseline"])
                    retake_calls += take.attempts
                    check["retakes"].append(take)
                    if take.mp3 is not None:
                        _apply_retake(storage, article_id, plan, take, processed, wavs, tmp)

                # Segment objects for text that no longer exists are dead weight.
                live_keys = {_segment_key(article_id, h) for _, _, h in plan}
                for entry in manifest or []:
                    if entry["key"] not in live_keys:
                        try:
                            storage.delete_image(entry["key"])
                        except Exception as exc:
                            logger.warning(
                                "Could not delete orphaned segment %s: %s", entry["key"], exc
                            )
            finally:
                for f in [*futures, *pitch_futures.values()]:
                    f.cancel()
                storage.close()

        new_manifest = [
            {
                "hash": text_hash,
                "key": _segment_key(article_id, text_hash),
                "is_heading": is_heading,
                # The processed length, not the raw clip's: the public player
                # derives its displayed duration from these.
                "duration_ms": seg.duration_ms,
            }
            for (_, is_heading, text_hash), seg in zip(plan, processed, strict=True)
        ]
        out_mp3 = f"{tmp}/narration.mp3"
        assemble(
            [
                (wav, HEADING_SILENCE_MS if is_heading else PARAGRAPH_SILENCE_MS)
                for wav, (_, is_heading, _) in zip(wavs, plan, strict=True)
            ],
            out_mp3,
        )
        final_mp3 = Path(out_mp3).read_bytes()

    logger.info(
        "Article %s narration post-processed: %d blips removed, gain %+.1f..%+.1f dB, "
        "%d limited, %d re-paced",
        article_id,
        sum(p.blips_removed for p in processed),
        min(p.gain_db for p in processed),
        max(p.gain_db for p in processed),
        sum(p.limited for p in processed),
        sum(p.tempo != 1.0 for p in processed),
    )
    qa = _qa_result(plan, new_manifest, pitches, check, retake_calls)
    logger.info(
        "Article %s narration voice check: %s, baseline %s Hz, %d measured, %d skipped, "
        "%d suspects, %d re-taken (%d ElevenLabs calls), %d unresolved",
        article_id,
        qa["status"],
        qa["baseline_hz"],
        qa["measured"],
        qa["skipped"],
        qa["suspects"],
        len(qa["retried"]),
        retake_calls,
        len(qa["unresolved"]),
    )
    return NarrationResult(
        mp3=final_mp3,
        duration_ms=manifest_total_duration_ms(new_manifest) or 0,
        segments=len(plan),
        synthesized=synthesized,
        reused=reused,
        manifest=new_manifest,
        qa=qa,
    )


def store_article_audio(article_id: int, mp3: bytes, old_url: str | None) -> str:
    """Upload the narration MP3 and best-effort delete the superseded object.

    The key is timestamped so a regeneration gets a fresh URL — overwriting a
    fixed key would leave browsers and the Spaces CDN serving the stale audio.
    """
    storage = ImageStorageService()
    try:
        key = f"articles/audio/{article_id}/es_{int(time.time())}.mp3"
        url = storage.upload_bytes(mp3, key, "audio/mpeg")
        if old_url:
            match = re.search(rf"(articles/audio/{article_id}/[^/?]+\.mp3)$", old_url)
            if match:
                try:
                    storage.delete_image(match.group(1))
                except Exception as exc:
                    logger.warning("Could not delete superseded narration %s: %s", old_url, exc)
        return url
    finally:
        storage.close()
