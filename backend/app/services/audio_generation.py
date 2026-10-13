"""Slide-narration audio synthesis (#322).

Each ``EduSlide.narration`` (authored natively in Spanish, #320) becomes one
Diego-voice MP3 via ElevenLabs (:func:`app.services.audio_tts.synthesize`, the
#317 wrapper — single voice, retry/backoff). Ported from feliu-dev's
``audio_generation.py`` and collapsed to a single narrator:

  * **per-slide segment cache + resume** — a slide's MP3 is cached on disk keyed
    by ``(material, position)``; a re-run skips slides already synthesized, so a
    failure partway through doesn't re-pay ElevenLabs for finished slides;
  * **24h TTL sweep** — cache dirs from abandoned runs are reclaimed on entry so
    ``/tmp`` can't grow without bound (the success path only cleans its own dir);
  * **per-slide durations retained** — ``EduSlide.duration_ms`` (milliseconds, via
    pydub) so the video stage (#323) can time each frame. ``audio_url`` holds the
    uploaded MP3. (feliu kept only a single total; we need per-slide.)
"""

import io
import logging
import os
import time
from datetime import UTC, datetime

from pydub import AudioSegment

from app.models.edu_material import EduMaterial
from app.services.audio_tts import synthesize
from app.services.image_storage import ImageStorageService

logger = logging.getLogger(__name__)

# Per-slide MP3s are cached here between retries (one dir per slides-material).
SEGMENT_CACHE_DIR = "/tmp/edu_slide_audio_segments"
# A per-material cache enables resume across retries, so it can't be deleted on
# failure. Instead, dirs untouched for longer than this TTL belong to runs that
# never finished and are swept on entry to bound /tmp growth.
SEGMENT_CACHE_TTL_SECONDS = 24 * 3600


def _material_cache_dir(material_id: int) -> str:
    return os.path.join(SEGMENT_CACHE_DIR, f"slides_{material_id}")


def _segment_cache_path(material_id: int, position: int) -> str:
    """Cache file path for one slide's MP3 (created lazily)."""
    cache_dir = _material_cache_dir(material_id)
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"segment_{position:04d}.mp3")


def _remove_cache_dir(cache_dir: str) -> None:
    """Delete a per-material segment cache directory if it exists."""
    if not os.path.isdir(cache_dir):
        return
    for name in os.listdir(cache_dir):
        os.unlink(os.path.join(cache_dir, name))
    os.rmdir(cache_dir)


def _sweep_stale_segment_caches(max_age_seconds: int = SEGMENT_CACHE_TTL_SECONDS) -> None:
    """Reclaim segment-cache dirs left behind by failed or abandoned runs.

    A dir whose mtime is older than the TTL hasn't had a segment written to it
    recently, so it belongs to a run that never finished — the success path only
    cleans up its own material, so without this sweep /tmp grows without bound.
    An in-progress run keeps writing segments, bumping its dir's mtime, so it is
    never swept.
    """
    if not os.path.isdir(SEGMENT_CACHE_DIR):
        return
    cutoff = time.time() - max_age_seconds
    for name in os.listdir(SEGMENT_CACHE_DIR):
        path = os.path.join(SEGMENT_CACHE_DIR, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                _remove_cache_dir(path)
                logger.info("Swept stale slide-audio cache: %s", path)
        except OSError as e:
            logger.warning("Failed to sweep stale slide-audio cache %s: %s", path, e)


def generate_slide_audio(material: EduMaterial, db, positions: set[int] | None = None) -> dict:
    """Synthesize Diego-voice narration for a deck's narrated slides.

    For each targeted ``EduSlide`` with non-empty ``narration`` (in ``position``
    order): synthesize the MP3, measure its duration with pydub, upload it to DO
    Spaces, and write ``audio_url`` + ``duration_ms`` on the row. Mutates the rows
    in place; the caller commits.

    ``positions`` (1-based) restricts synthesis to a subset — used to re-synth
    only slides whose narration was edited. A subset run **bypasses the segment
    cache** (reads and writes) so a re-roll always produces fresh audio for the
    edited text; slides outside the subset are left untouched. A whole-deck run
    (``positions is None``) keeps the resume cache and clears it on full success.

    Returns ``{narrated, total_duration_ms, skipped_no_narration}``.
    Raises ``ValueError`` if no targeted slide has narration.
    """
    slides = material.slides  # relationship is ordered by EduSlide.position
    subset = positions is not None
    targeted = [s for s in slides if positions is None or s.position in positions]
    narrated = [s for s in targeted if (s.narration or "").strip()]
    if not narrated:
        if subset:
            raise ValueError("None of the selected slides have narration to synthesize")
        raise ValueError("Material has no slide narration to synthesize")

    # Reclaim caches from earlier failed/abandoned runs before starting.
    _sweep_stale_segment_caches()

    total_ms = 0
    storage = ImageStorageService()
    try:
        for slide in narrated:
            cache_path = _segment_cache_path(material.id, slide.position)
            if not subset and os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                logger.info("Slide %d audio cached; skipping synthesis", slide.position)
                with open(cache_path, "rb") as f:
                    mp3 = f.read()
            else:
                mp3 = synthesize(slide.narration)
                if subset:
                    # Targeted re-roll: don't leave a stale cache file behind that
                    # a later whole-deck resume could read for the old narration.
                    if os.path.exists(cache_path):
                        os.unlink(cache_path)
                else:
                    with open(cache_path, "wb") as f:
                        f.write(mp3)

            duration_ms = len(AudioSegment.from_file(io.BytesIO(mp3), format="mp3"))
            key = f"materials/slides/audio/{material.id}/{slide.position:02d}.mp3"
            slide.audio_url = storage.upload_bytes(mp3, key, "audio/mpeg")
            slide.duration_ms = duration_ms
            total_ms += duration_ms
    finally:
        storage.close()

    if not subset:
        # Whole deck synthesized + uploaded — this material's cache is no longer
        # needed for resume (stale caches from other runs are handled by the sweep).
        _remove_cache_dir(_material_cache_dir(material.id))

    material.updated_at = datetime.now(UTC)
    logger.info(
        "Synthesized %d slide narrations for material id=%s (%d ms total)%s",
        len(narrated),
        material.id,
        total_ms,
        f" [subset {sorted(positions)}]" if subset else "",
    )
    return {
        "narrated": len(narrated),
        "total_duration_ms": total_ms,
        "skipped_no_narration": len(targeted) - len(narrated),
    }
