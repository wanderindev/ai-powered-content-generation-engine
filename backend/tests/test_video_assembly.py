"""Tests for narrated slide-video assembly (app.services.video_assembly, #323).

ffmpeg is mocked at the subprocess boundary for the orchestration tests (so they
run in CI), and a single real-ffmpeg test is gated behind ffmpeg availability
(runs under scripts/run-tests.sh, skips in CI). Storage is always faked.
"""

import functools
import io
import os
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.services import video_assembly
from app.services.video_assembly import _key_from_url, assemble_slide_video

_CDN = "https://cdn.example"


@functools.lru_cache(maxsize=1)
def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="ffmpeg/ffprobe not installed — run under scripts/run-tests.sh",
)

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


class _FakeStorage:
    """Captures uploads; serves per-key bytes for downloads."""

    cdn_url = _CDN

    def __init__(self, png=b"PNGDATA", mp3=b"MP3DATA"):
        self.png = png
        self.mp3 = mp3
        self.uploads = []

    def download_bytes(self, key):
        return self.png if key.endswith(".png") else self.mp3

    def upload_image(self, data, key, content_type="image/jpeg"):
        self.uploads.append((key, content_type, data))
        return f"{self.cdn_url}/{key}"

    def close(self):
        pass


def _fake_ffmpeg(cmd, capture_output=False):
    """Pretend to run ffmpeg: write marker bytes to the output path (cmd[-1])."""
    with open(cmd[-1], "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42")
    return SimpleNamespace(returncode=0, stderr=b"")


# --- Helpers ----------------------------------------------------------------


def _slides_material(db, n=2, with_frames=True, with_audio=True, durations=None, mtype="slides"):
    s = EduSuggestion(title="t")
    db.add(s)
    db.flush()
    r = EduResearch(suggestion_id=s.id, content="x")
    db.add(r)
    db.flush()
    m = EduMaterial(
        research_id=r.id,
        material_type=mtype,
        grade_band="3rd-6th",
        title="Panamá Viejo — Slides",
        content="preview",
        status="PENDING",
    )
    db.add(m)
    db.flush()
    for i in range(1, n + 1):
        dur = (durations[i - 1] if durations else 1000) if with_audio else None
        m.slides.append(
            EduSlide(
                position=i,
                template="bullets",
                title=f"Slide {i}",
                image_url=f"{_CDN}/materials/slides/frames/{m.id}/{i:02d}.png" if with_frames else None,
                audio_url=f"{_CDN}/materials/slides/audio/{m.id}/{i:02d}.mp3" if with_audio else None,
                duration_ms=dur,
            )
        )
    db.commit()
    db.refresh(m)
    return m


# --- _key_from_url (pure) ---------------------------------------------------


class TestKeyFromUrl:
    def test_strips_cdn_prefix(self):
        assert _key_from_url(_CDN, f"{_CDN}/materials/video/3/x.mp4") == "materials/video/3/x.mp4"

    def test_rejects_foreign_url(self):
        with pytest.raises(ValueError, match="not under the configured CDN"):
            _key_from_url(_CDN, "https://evil.example/x.png")


# --- assemble_slide_video (ffmpeg + storage mocked) -------------------------


class TestAssembleSlideVideo:
    def test_validation_missing_frame_or_audio(self, test_db):
        no_audio = _slides_material(test_db, n=2, with_audio=False)
        with pytest.raises(ValueError, match="missing a rendered frame or narration"):
            assemble_slide_video(no_audio, test_db)

    def test_builds_and_uploads_video_material(self, test_db):
        material = _slides_material(test_db, n=3, durations=[1000, 2000, 1500])
        storage = _FakeStorage()
        with (
            patch("app.services.video_assembly.ImageStorageService", return_value=storage),
            patch("app.services.video_assembly.subprocess.run", side_effect=_fake_ffmpeg) as run,
        ):
            video = assemble_slide_video(material, test_db)

        # 3 per-slide clips + 1 concat
        assert run.call_count == 4
        # one MP4 uploaded, video content-type, under the video/ prefix
        assert len(storage.uploads) == 1
        key, content_type, _data = storage.uploads[0]
        assert content_type == "video/mp4"
        assert key == f"materials/video/{material.research_id}/3rd_6th.mp4"
        # video EduMaterial upserted with file_url + summed duration (narration + pause)
        assert video.material_type == "video"
        assert video.file_url == f"{_CDN}/{key}"
        assert video.duration_ms == (1000 + 2000 + 1500) + 3 * video_assembly.PAUSE_MS
        assert video.title == "Panamá Viejo — Video"

    def test_reassemble_updates_existing_video(self, test_db):
        material = _slides_material(test_db, n=2)
        with (
            patch("app.services.video_assembly.ImageStorageService", return_value=_FakeStorage()),
            patch("app.services.video_assembly.subprocess.run", side_effect=_fake_ffmpeg),
        ):
            first = assemble_slide_video(material, test_db)
            test_db.commit()
            second = assemble_slide_video(material, test_db)

        assert first.id == second.id  # upsert, not a duplicate row
        assert (
            test_db.query(EduMaterial)
            .filter(
                EduMaterial.research_id == material.research_id,
                EduMaterial.material_type == "video",
            )
            .count()
            == 1
        )

    def test_ffmpeg_failure_raises_runtimeerror(self, test_db):
        material = _slides_material(test_db, n=1)

        def boom(cmd, capture_output=False):
            return SimpleNamespace(returncode=1, stderr=b"bad codec")

        with (
            patch("app.services.video_assembly.ImageStorageService", return_value=_FakeStorage()),
            patch("app.services.video_assembly.subprocess.run", side_effect=boom),
        ):
            with pytest.raises(RuntimeError, match="ffmpeg failed"):
                assemble_slide_video(material, test_db)


# --- POST /materials/{id}/assemble-video ------------------------------------


@pytest.mark.usefixtures("edu_job_session")
class TestAssembleVideoEndpoint:
    """assemble-video validates synchronously, then runs as a background job (#346)."""

    URL = "/api/v1/admin/dashboard/edu/materials/{id}/assemble-video"

    def test_missing_material_404(self, test_client, test_db):
        assert test_client.post(self.URL.format(id=999999)).status_code == 404

    def test_non_slides_material_400(self, test_client, test_db):
        m = _slides_material(test_db, n=1, mtype="guide")
        assert test_client.post(self.URL.format(id=m.id)).status_code == 400

    def test_incomplete_deck_400_without_calling_service(self, test_client, test_db):
        m = _slides_material(test_db, n=2, with_audio=False)
        with patch("app.services.edu_jobs.assemble_slide_video") as svc:
            resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400
        assert "positions" in resp.json()["detail"]
        svc.assert_not_called()

    @patch("app.services.edu_jobs.assemble_slide_video")
    def test_success_job_carries_video_fields(self, mock_svc, test_client, test_db):
        m = _slides_material(test_db, n=2, durations=[1000, 1000])
        mock_svc.return_value = SimpleNamespace(
            id=4242, file_url=f"{_CDN}/materials/video/1/3rd_6th.mp4", duration_ms=3000
        )

        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 202
        job = test_client.get(f"/api/v1/media-library/jobs/{resp.json()['id']}").json()
        assert job["job_type"] == "EDU_ASSEMBLE_VIDEO"
        assert job["status"] == "SUCCEEDED"
        body = job["stats"]
        assert body["slides_material_id"] == m.id
        assert body["video_material_id"] == 4242
        assert body["duration_ms"] == 3000
        assert body["slide_count"] == 2
        assert body["file_url"].endswith(".mp4")

    @patch("app.services.edu_jobs.assemble_slide_video", side_effect=RuntimeError("ffmpeg failed"))
    def test_ffmpeg_failure_fails_the_job(self, _mock_svc, test_client, test_db):
        m = _slides_material(test_db, n=1, durations=[1000])
        resp = test_client.post(self.URL.format(id=m.id))
        job = test_client.get(f"/api/v1/media-library/jobs/{resp.json()['id']}").json()
        assert job["status"] == "FAILED"
        assert "ffmpeg failed" in job["error"]


# --- real ffmpeg (gated) ----------------------------------------------------


def _ffprobe_seconds(mp4_bytes: bytes) -> float:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(mp4_bytes)
        path = tmp.name
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True,
            text=True,
        )
        return float(out.stdout.strip())
    finally:
        os.unlink(path)


@requires_ffmpeg
def test_real_ffmpeg_produces_playable_mp4(test_db):
    from PIL import Image
    from pydub import AudioSegment

    png_buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (16, 116, 150)).save(png_buf, format="PNG")
    mp3_buf = io.BytesIO()
    AudioSegment.silent(duration=1000).export(mp3_buf, format="mp3")

    material = _slides_material(test_db, n=2, durations=[1000, 1000])
    storage = _FakeStorage(png=png_buf.getvalue(), mp3=mp3_buf.getvalue())
    with patch("app.services.video_assembly.ImageStorageService", return_value=storage):
        video = assemble_slide_video(material, test_db)

    key, content_type, data = storage.uploads[0]
    assert content_type == "video/mp4"
    assert data[4:8] == b"ftyp"  # real MP4 container
    # 2 slides × (1000ms narration + 500ms pause) = 3.0s, allowing encoder slack.
    assert video.duration_ms == 3000
    assert 2.6 <= _ffprobe_seconds(data) <= 3.4
