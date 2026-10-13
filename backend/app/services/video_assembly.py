"""Narrated slide-video assembly (#323).

Muxes each slide's rendered 1280x720 PNG frame (#321, ``EduSlide.image_url``)
with its Diego-voice narration MP3 (#322, ``EduSlide.audio_url``) into one MP4 —
the slide held on-screen for the length of its narration plus a brief pause —
then concatenates the per-slide clips into a single 1280x720 H.264 + AAC video
and stores it as a ``video`` ``EduMaterial``. ffmpeg does the muxing (feliu-dev
stopped at the MP3 stage; this step is new).

Pacing (user decision): **hard cuts** between slides with a **~0.5s hold**. The
hold is implemented by padding each clip's audio with trailing silence
(``apad``) and capping the clip to ``narration + PAUSE`` via ``-t`` — deterministic,
using the per-slide ``duration_ms`` already measured in #322. Identical encode
params per clip let the final concat run with ``-c copy`` (lossless, fast).
"""

import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.edu_material import EduMaterial
from app.models.enums import ContentStatus, MaterialType
from app.services.image_storage import ImageStorageService

logger = logging.getLogger(__name__)

VIDEO_FPS = 30
PAUSE_MS = 500  # silent hold appended after each slide's narration (hard cut after)
_AUDIO_BITRATE = "192k"


def _key_from_url(cdn_url: str, url: str) -> str:
    """Recover the in-bucket object key from a stored CDN URL."""
    prefix = cdn_url.rstrip("/") + "/"
    if not url.startswith(prefix):
        raise ValueError(f"URL is not under the configured CDN ({cdn_url}): {url}")
    return url[len(prefix) :]


def _clip_cmd(png_path: str, mp3_path: str, out_path: str, total_ms: int) -> list[str]:
    """ffmpeg args for one slide clip: still frame + narration + trailing silence."""
    return [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        png_path,
        "-i",
        mp3_path,
        # Pad the audio with silence; -t then caps the clip at narration + PAUSE so
        # the frame holds through the pause and hard-cuts to the next slide.
        "-af",
        "apad",
        "-t",
        f"{total_ms / 1000:.3f}",
        "-r",
        str(VIDEO_FPS),
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        out_path,
    ]


def _concat_cmd(list_path: str, out_path: str) -> list[str]:
    """ffmpeg args to losslessly concat the per-slide clips into the final MP4."""
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        out_path,
    ]


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run an ffmpeg command, raising RuntimeError with stderr tail on failure."""
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"ffmpeg failed ({cmd[-1]}): {tail}")


def assemble_slide_video(slides_material: EduMaterial, db: Session) -> EduMaterial:
    """Assemble a slides-material's frames + narration into one MP4 ``EduMaterial``.

    Every slide must already have both ``image_url`` (#321) and ``audio_url``
    (#322); raises ``ValueError`` otherwise (the operator runs render-slides and
    generate-audio first). Downloads each frame + MP3, muxes per-slide clips,
    concats them, uploads the MP4 to Spaces, and upserts a ``video`` EduMaterial
    on ``(research_id, "video", grade_band)`` with ``file_url`` + ``duration_ms``.
    The caller commits.
    """
    slides = slides_material.slides  # ordered by EduSlide.position
    if not slides:
        raise ValueError("Material has no slides")
    incomplete = [s.position for s in slides if not s.image_url or not s.audio_url]
    if incomplete:
        raise ValueError(
            f"Slides missing a rendered frame or narration audio at positions "
            f"{incomplete}; run render-slides and generate-audio first"
        )

    storage = ImageStorageService()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            clip_paths = []
            for slide in slides:
                png_path = os.path.join(tmp, f"frame_{slide.position:04d}.png")
                mp3_path = os.path.join(tmp, f"audio_{slide.position:04d}.mp3")
                with open(png_path, "wb") as f:
                    f.write(storage.download_bytes(_key_from_url(storage.cdn_url, slide.image_url)))
                with open(mp3_path, "wb") as f:
                    f.write(storage.download_bytes(_key_from_url(storage.cdn_url, slide.audio_url)))

                clip_path = os.path.join(tmp, f"clip_{slide.position:04d}.mp4")
                total_ms = (slide.duration_ms or 0) + PAUSE_MS
                _run_ffmpeg(_clip_cmd(png_path, mp3_path, clip_path, total_ms))
                clip_paths.append(clip_path)

            list_path = os.path.join(tmp, "concat.txt")
            with open(list_path, "w") as f:
                for clip_path in clip_paths:
                    f.write(f"file '{clip_path}'\n")

            out_path = os.path.join(tmp, "video.mp4")
            _run_ffmpeg(_concat_cmd(list_path, out_path))
            with open(out_path, "rb") as f:
                mp4_bytes = f.read()

        band_slug = (slides_material.grade_band or "none").replace("-", "_")
        key = f"materials/video/{slides_material.research_id}/{band_slug}.mp4"
        file_url = storage.upload_image(mp4_bytes, key, "video/mp4")
    finally:
        storage.close()

    total_duration_ms = sum((s.duration_ms or 0) + PAUSE_MS for s in slides)
    title = (slides_material.title or "").replace("— Slides", "").strip() + " — Video"
    now = datetime.now(UTC)

    # Upsert the video material on its natural key (research_id, "video", band).
    video = (
        db.query(EduMaterial)
        .filter(
            EduMaterial.research_id == slides_material.research_id,
            EduMaterial.material_type == MaterialType.VIDEO.value,
            EduMaterial.grade_band == slides_material.grade_band,
        )
        .first()
    )
    if video is None:
        video = EduMaterial(
            research_id=slides_material.research_id,
            material_type=MaterialType.VIDEO.value,
            grade_band=slides_material.grade_band,
            title=title,
            file_url=file_url,
            duration_ms=total_duration_ms,
            status=ContentStatus.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        db.add(video)
    else:
        video.title = title
        video.file_url = file_url
        video.duration_ms = total_duration_ms
        video.status = ContentStatus.PENDING.value
        video.updated_at = now
    db.flush()

    logger.info(
        "Assembled %d-slide video material id=%s (%d ms) for band=%s",
        len(slides),
        video.id,
        total_duration_ms,
        slides_material.grade_band,
    )
    return video
