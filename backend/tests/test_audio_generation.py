"""Tests for slide-narration synthesis (app.services.audio_generation, #322).

ElevenLabs is never called for real — ``synthesize`` is mocked. pydub's duration
measurement needs ffmpeg, so the orchestration tests mock ``AudioSegment`` too
(running everywhere, incl. CI), and a single real-ffmpeg duration test is gated
behind ``_ffmpeg_available()`` (runs under scripts/run-tests.sh, skips in CI).
"""

import functools
import io
import os
import shutil
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.services import audio_generation
from app.services.audio_generation import generate_slide_audio


@functools.lru_cache(maxsize=1)
def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="ffmpeg not installed — run under scripts/run-tests.sh",
)

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    """Override the router-level admin gate for every test in this module (#54)."""
    from app.api.dependencies import validate_admin_token
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point the segment cache at a per-test tmp dir (no /tmp pollution)."""
    monkeypatch.setattr(audio_generation, "SEGMENT_CACHE_DIR", str(tmp_path / "cache"))


class _FakeSeg:
    """Stand-in for a pydub AudioSegment with a fixed length in ms."""

    def __init__(self, ms):
        self.ms = ms

    def __len__(self):
        return self.ms


class _FakeStorage:
    """Captures uploads instead of hitting DO Spaces."""

    def __init__(self):
        self.uploads = []

    def upload_bytes(self, data, key, content_type):
        self.uploads.append((key, content_type, data))
        return f"https://cdn.example/{key}"

    def close(self):
        pass


# --- Helpers ----------------------------------------------------------------


def _slides_material(db, narrations, material_type="slides"):
    """A slides-material whose slide i has narration narrations[i] (None = empty)."""
    s = EduSuggestion(title="t")
    db.add(s)
    db.flush()
    r = EduResearch(suggestion_id=s.id, content="x")
    db.add(r)
    db.flush()
    m = EduMaterial(
        research_id=r.id,
        material_type=material_type,
        grade_band="3rd-6th",
        title="Deck",
        content="preview",
        status="PENDING",
    )
    db.add(m)
    db.flush()
    for i, narration in enumerate(narrations, start=1):
        m.slides.append(
            EduSlide(position=i, template="bullets", title=f"Slide {i}", narration=narration)
        )
    db.commit()
    db.refresh(m)
    return m


# --- generate_slide_audio (synthesize + pydub mocked) -----------------------


class TestGenerateSlideAudio:
    def test_synthesizes_each_narrated_slide(self, test_db):
        material = _slides_material(test_db, ["Hola.", "Adiós.", None])
        storage = _FakeStorage()
        with (
            patch("app.services.audio_generation.synthesize", return_value=b"mp3") as synth,
            patch("app.services.audio_generation.AudioSegment") as seg,
            patch("app.services.audio_generation.ImageStorageService", return_value=storage),
        ):
            seg.from_file.return_value = _FakeSeg(1500)
            summary = generate_slide_audio(material, test_db)

        assert synth.call_count == 2  # the empty-narration slide is skipped
        assert summary == {"narrated": 2, "total_duration_ms": 3000, "skipped_no_narration": 1}
        narrated = [s for s in material.slides if s.narration]
        for slide in narrated:
            assert slide.duration_ms == 1500
            assert slide.audio_url == (
                f"https://cdn.example/materials/slides/audio/{material.id}/{slide.position:02d}.mp3"
            )
        # The no-narration slide is untouched.
        assert material.slides[2].audio_url is None
        assert len(storage.uploads) == 2
        assert all(ct == "audio/mpeg" for _k, ct, _d in storage.uploads)

    def test_resume_skips_already_cached_slides(self, test_db):
        material = _slides_material(test_db, ["Uno.", "Dos."])
        # Pre-seed slide 1's cache as if a prior run synthesized it.
        cached_path = audio_generation._segment_cache_path(material.id, 1)
        with open(cached_path, "wb") as f:
            f.write(b"already-here")

        storage = _FakeStorage()
        with (
            patch("app.services.audio_generation.synthesize", return_value=b"fresh") as synth,
            patch("app.services.audio_generation.AudioSegment") as seg,
            patch("app.services.audio_generation.ImageStorageService", return_value=storage),
        ):
            seg.from_file.return_value = _FakeSeg(800)
            generate_slide_audio(material, test_db)

        # Only the uncached slide 2 hits ElevenLabs; both still upload + persist.
        assert synth.call_count == 1
        assert all(s.audio_url for s in material.slides)
        assert {data for _k, _ct, data in storage.uploads} == {b"already-here", b"fresh"}

    def test_no_narration_raises(self, test_db):
        material = _slides_material(test_db, [None, "   "])
        with patch("app.services.audio_generation.synthesize") as synth:
            with pytest.raises(ValueError, match="no slide narration"):
                generate_slide_audio(material, test_db)
        synth.assert_not_called()

    def test_cache_dir_removed_on_success(self, test_db):
        material = _slides_material(test_db, ["Hola."])
        storage = _FakeStorage()
        with (
            patch("app.services.audio_generation.synthesize", return_value=b"mp3"),
            patch("app.services.audio_generation.AudioSegment") as seg,
            patch("app.services.audio_generation.ImageStorageService", return_value=storage),
        ):
            seg.from_file.return_value = _FakeSeg(1000)
            generate_slide_audio(material, test_db)
        assert not os.path.isdir(audio_generation._material_cache_dir(material.id))

    def test_subset_resynths_only_targeted_leaves_others(self, test_db):
        # Slides 1 & 3 already have audio; a subset run for {1} must re-synth ONLY
        # slide 1 and leave slides 2 (unnarrated) and 3 (its audio) untouched.
        material = _slides_material(test_db, ["Uno.", None, "Tres."])
        material.slides[0].audio_url = "https://cdn.example/old/1.mp3"
        material.slides[0].duration_ms = 111
        material.slides[2].audio_url = "https://cdn.example/keep/3.mp3"
        material.slides[2].duration_ms = 333
        test_db.commit()

        storage = _FakeStorage()
        with (
            patch("app.services.audio_generation.synthesize", return_value=b"fresh") as synth,
            patch("app.services.audio_generation.AudioSegment") as seg,
            patch("app.services.audio_generation.ImageStorageService", return_value=storage),
        ):
            seg.from_file.return_value = _FakeSeg(999)
            summary = generate_slide_audio(material, test_db, positions={1})

        assert synth.call_count == 1  # only the targeted narrated slide
        assert summary == {"narrated": 1, "total_duration_ms": 999, "skipped_no_narration": 0}
        assert material.slides[0].duration_ms == 999  # re-synthed
        assert material.slides[2].audio_url == "https://cdn.example/keep/3.mp3"  # untouched
        assert material.slides[2].duration_ms == 333
        assert len(storage.uploads) == 1

    def test_subset_bypasses_and_clears_stale_cache(self, test_db):
        # A cached segment for the targeted slide must NOT short-circuit synthesis
        # (edited narration) and must be removed so a later whole-deck resume can't
        # read the stale file.
        material = _slides_material(test_db, ["Editada."])
        cached_path = audio_generation._segment_cache_path(material.id, 1)
        with open(cached_path, "wb") as f:
            f.write(b"stale")

        storage = _FakeStorage()
        with (
            patch("app.services.audio_generation.synthesize", return_value=b"fresh") as synth,
            patch("app.services.audio_generation.AudioSegment") as seg,
            patch("app.services.audio_generation.ImageStorageService", return_value=storage),
        ):
            seg.from_file.return_value = _FakeSeg(500)
            generate_slide_audio(material, test_db, positions={1})

        assert synth.call_count == 1  # cache ignored — re-synthed
        assert not os.path.exists(cached_path)  # stale cache removed
        assert {data for _k, _ct, data in storage.uploads} == {b"fresh"}

    def test_subset_with_no_narrated_target_raises(self, test_db):
        material = _slides_material(test_db, ["Uno.", None])
        with patch("app.services.audio_generation.synthesize") as synth:
            with pytest.raises(ValueError, match="selected slides have narration"):
                generate_slide_audio(material, test_db, positions={2})
        synth.assert_not_called()


# --- stale-cache sweep ------------------------------------------------------


class TestSweepStaleCaches:
    def test_sweeps_old_dirs_keeps_fresh(self, tmp_path, monkeypatch):
        base = tmp_path / "cache"
        monkeypatch.setattr(audio_generation, "SEGMENT_CACHE_DIR", str(base))
        stale = audio_generation._material_cache_dir(1)
        fresh = audio_generation._material_cache_dir(2)
        os.makedirs(stale)
        os.makedirs(fresh)
        # Age the stale dir well past the TTL.
        old = time.time() - audio_generation.SEGMENT_CACHE_TTL_SECONDS - 100
        os.utime(stale, (old, old))

        audio_generation._sweep_stale_segment_caches()

        assert not os.path.isdir(stale)
        assert os.path.isdir(fresh)


# --- POST /materials/{id}/generate-audio ------------------------------------


class TestGenerateAudioEndpoint:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/generate-audio"

    def test_missing_material_404(self, test_client, test_db):
        resp = test_client.post(self.URL.format(id=999999))
        assert resp.status_code == 404

    def test_non_slides_material_400(self, test_client, test_db):
        m = _slides_material(test_db, ["Hola."], material_type="guide")
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400

    def test_empty_deck_400(self, test_client, test_db):
        m = _slides_material(test_db, [])
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400

    def test_no_narration_400_without_calling_service(self, test_client, test_db):
        m = _slides_material(test_db, [None, "  "])
        with patch("app.api.edu.generate_slide_audio") as svc:
            resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400
        svc.assert_not_called()

    @patch("app.api.edu.generate_slide_audio")
    def test_success_returns_counts(self, mock_svc, test_client, test_db):
        m = _slides_material(test_db, ["Hola.", "Adiós.", None])

        def fake(material, db, positions=None):
            for slide in material.slides:
                if slide.narration and slide.narration.strip():
                    slide.audio_url = f"https://cdn.example/a/{slide.position}.mp3"
                    slide.duration_ms = 2000
            return {"narrated": 2, "total_duration_ms": 4000, "skipped_no_narration": 1}

        mock_svc.side_effect = fake

        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["slides_narrated"] == 2
        assert body["skipped_no_narration"] == 1
        assert body["total_duration_ms"] == 4000

    @patch("app.api.edu.generate_slide_audio")
    def test_positions_threaded_to_service(self, mock_svc, test_client, test_db):
        m = _slides_material(test_db, ["Uno.", "Dos.", "Tres."])
        mock_svc.return_value = {
            "narrated": 1,
            "total_duration_ms": 500,
            "skipped_no_narration": 0,
        }
        resp = test_client.post(self.URL.format(id=m.id), json={"positions": [2, 2, 1]})
        assert resp.status_code == 200
        # Schema dedups/sorts; endpoint validates ⊆ 1..N and passes a set.
        assert mock_svc.call_args.kwargs["positions"] == {1, 2}

    def test_out_of_range_positions_422(self, test_client, test_db):
        m = _slides_material(test_db, ["Uno.", "Dos."])
        with patch("app.api.edu.generate_slide_audio") as svc:
            resp = test_client.post(self.URL.format(id=m.id), json={"positions": [5]})
        assert resp.status_code == 422
        svc.assert_not_called()

    def test_subset_with_no_narrated_target_400(self, test_client, test_db):
        m = _slides_material(test_db, ["Uno.", None])
        with patch("app.api.edu.generate_slide_audio") as svc:
            resp = test_client.post(self.URL.format(id=m.id), json={"positions": [2]})
        assert resp.status_code == 400
        svc.assert_not_called()


# --- real pydub duration (ffmpeg-gated) -------------------------------------


@requires_ffmpeg
def test_real_pydub_duration_end_to_end(test_db):
    from pydub import AudioSegment

    buf = io.BytesIO()
    AudioSegment.silent(duration=1000).export(buf, format="mp3")
    silent_mp3 = buf.getvalue()

    material = _slides_material(test_db, ["Narración de prueba."])
    storage = _FakeStorage()
    with (
        patch("app.services.audio_generation.synthesize", return_value=silent_mp3),
        patch("app.services.audio_generation.ImageStorageService", return_value=storage),
    ):
        summary = generate_slide_audio(material, test_db)

    slide = material.slides[0]
    assert slide.audio_url and slide.audio_url.endswith(f"/{material.id}/01.mp3")
    # ~1000ms, allowing for MP3 encoder frame padding.
    assert 900 <= slide.duration_ms <= 1200
    assert summary["narrated"] == 1
    assert 900 <= summary["total_duration_ms"] <= 1200
