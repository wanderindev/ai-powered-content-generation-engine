"""Narration voice check (#601): re-take high-pitch ElevenLabs clips, warn when too many.

ElevenLabs, the pitch estimator, post-processing and storage are all mocked:
each fake clip is the bytes ``b"<text>#<take>"`` and the fake estimator reads
the pitch for that take from a per-test table.
"""

from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models import ArticleSuggestion, Research
from app.models.article import Article
from app.services.article_audio import (
    QA_FIXED,
    QA_NEEDS_REVIEW,
    QA_OK,
    segment_article,
    segment_hash,
    segment_start_ms,
    synthesize_article,
)
from app.services.narration_postprocess import ProcessedSegment

TITLE = "Título de prueba"
N_PARAGRAPHS = 20
PARAGRAPHS = [f"Párrafo número {k} con texto suficiente para medir." for k in range(N_PARAGRAPHS)]
CONTENT = "## Primera sección\n\n" + "\n\n".join(PARAGRAPHS)
BASE_HZ = 144.0


def _plan_texts(content=CONTENT):
    return [text for text, _ in segment_article(TITLE, content)]


def _pos(paragraph: int) -> int:
    """0-based plan index of PARAGRAPHS[paragraph] (title, heading, then
    paragraphs). The voice check reports 1-based positions: ``_pos(p) + 1``."""
    return paragraph + 2


class Fakes:
    """Stand-ins for synthesize / median_f0 / process_segment / storage."""

    def __init__(self, pitches=None, short=()):
        # pitches: {text: [hz of take 1, take 2, ...]}; default BASE_HZ.
        self.pitches = pitches or {}
        self.short = set(short)
        self.takes = defaultdict(int)
        self.syn_calls = []
        self.measured = []
        self.processed = {}
        self.uploads = []
        self.assembled = []  # the WAV paths assemble() read, in order

    def in_read(self, i):
        """The raw clip whose processed WAV went into the final read at plan index i."""
        return self.processed[self.assembled[i]]

    def stored(self, text):
        """The raw clip last uploaded at ``text``'s segment key."""
        key = f"articles/audio/1/segments/{segment_hash(text)}.mp3"
        return [d for k, d in self.uploads if k == key][-1]

    def synthesize(self, text, **kwargs):
        self.takes[text] += 1
        self.syn_calls.append((text, kwargs))
        return f"{text}#{self.takes[text]}".encode()

    def median_f0(self, mp3):
        self.measured.append(mp3)
        if mp3.startswith(b"reused:"):
            text = mp3.decode().split(":", 1)[1]
            take = 0
        else:
            text, take = mp3.decode().rsplit("#", 1)
            take = int(take)
        if text in self.short:
            return None
        hz = self.pitches.get(text)
        if hz is None:
            return BASE_HZ
        return hz[take - 1] if take else hz[0]

    def process(self, mp3, *, text, is_heading, out_wav):
        self.processed[out_wav] = mp3
        return ProcessedSegment(
            duration_ms=640 if is_heading else 820,
            blips_removed=0,
            gain_db=0.0,
            limited=False,
            wpm=None,
            tempo=1.0,
        )


def _assemble(self, parts, out):
    self.assembled = [wav for wav, _ in parts]
    with open(out, "wb") as f:
        f.write(b"FINAL")


Fakes.assemble = _assemble


@contextmanager
def _patched(fakes, downloads=None):
    with (
        patch("app.services.article_audio.synthesize", side_effect=fakes.synthesize),
        patch("app.services.article_audio.median_f0", side_effect=fakes.median_f0),
        patch("app.services.article_audio.process_segment", side_effect=fakes.process),
        patch("app.services.article_audio.assemble", side_effect=fakes.assemble),
        patch("app.services.article_audio.ImageStorageService") as storage_cls,
    ):
        storage = storage_cls.return_value
        storage.upload_bytes.side_effect = lambda data, key, ct: fakes.uploads.append((key, data))
        if downloads is not None:
            storage.download_bytes.side_effect = downloads
        yield storage


def _run(fakes, **kwargs):
    with _patched(fakes):
        return synthesize_article(1, TITLE, CONTENT, **kwargs)


class TestVoiceCheck:
    def test_no_suspects(self):
        fakes = Fakes({PARAGRAPHS[3]: [150.0], PARAGRAPHS[4]: [138.0]})
        result = _run(fakes)
        qa = result.qa
        assert qa["status"] == QA_OK
        assert qa["baseline_hz"] == BASE_HZ
        assert qa["threshold_pct"] == 15.0
        assert qa["measured"] == N_PARAGRAPHS
        assert qa["skipped"] == 2  # title + heading
        assert qa["suspects"] == 0
        assert qa["retried"] == [] and qa["unresolved"] == []
        assert len(fakes.syn_calls) == len(result.manifest)

    def test_few_suspects_fixed_on_first_retry(self):
        bad = PARAGRAPHS[5]
        fakes = Fakes({bad: [197.5, 146.0, 140.0]})
        result = _run(fakes)
        qa = result.qa
        assert qa["status"] == QA_FIXED
        assert qa["suspects"] == 1
        assert qa["retake_calls"] == 1
        (entry,) = qa["retried"]
        assert entry["position"] == _pos(5) + 1
        assert entry["hz_before"] == 197.5
        assert entry["hz_after"] == 146.0
        assert entry["attempts"] == 1
        assert entry["resolved"] is True
        assert entry["text"] == bad[:120]
        assert qa["unresolved"] == []

        # The re-take used the same text, neighbour context and settings.
        first, retake = [kw for text, kw in fakes.syn_calls if text == bad]
        assert first == retake
        # The accepted take overwrote the raw segment at its hash key...
        key = f"articles/audio/1/segments/{segment_hash(bad)}.mp3"
        assert [d for k, d in fakes.uploads if k == key] == [
            f"{bad}#1".encode(),
            f"{bad}#2".encode(),
        ]
        # ...and is what went into the final read.
        assert fakes.in_read(_pos(5)) == f"{bad}#2".encode()
        # Timestamp matches the final manifest the player reads.
        assert entry["start_ms"] == segment_start_ms(result.manifest, _pos(5))
        # title 640 + 1400 + heading 640 + 1000 + 5 * (820 + 1000)
        assert entry["start_ms"] == 640 + 1400 + 640 + 1000 + 5 * (820 + 1000)

    def test_retry_still_high_keeps_the_lowest_take(self):
        bad = PARAGRAPHS[7]
        fakes = Fakes({bad: [200.0, 185.0, 192.0]})
        result = _run(fakes)
        qa = result.qa
        assert qa["status"] == QA_NEEDS_REVIEW
        assert qa["retake_calls"] == 2
        (entry,) = qa["retried"]
        assert entry["attempts"] == 2
        assert entry["resolved"] is False
        assert entry["hz_after"] == 185.0
        (unresolved,) = qa["unresolved"]
        assert unresolved["position"] == _pos(7) + 1
        assert unresolved["hz"] == 185.0
        assert unresolved["attempts"] == 2
        assert unresolved["pct"] == pytest.approx(round((185 - 144) / 144 * 100, 1))
        # Lowest-pitch take (#2) is stored and processed.
        key = f"articles/audio/1/segments/{segment_hash(bad)}.mp3"
        assert [d for k, d in fakes.uploads if k == key][-1] == f"{bad}#2".encode()
        assert fakes.in_read(_pos(7)) == f"{bad}#2".encode()

    def test_original_take_stays_when_it_is_the_lowest(self):
        bad = PARAGRAPHS[7]
        fakes = Fakes({bad: [180.0, 190.0, 195.0]})
        result = _run(fakes)
        assert result.qa["unresolved"][0]["hz"] == 180.0
        key = f"articles/audio/1/segments/{segment_hash(bad)}.mp3"
        assert [d for k, d in fakes.uploads if k == key] == [f"{bad}#1".encode()]

    def test_many_suspects_retry_nothing(self):
        # 2 of 20 measurable = 10 %: the alarm path.
        fakes = Fakes({PARAGRAPHS[1]: [190.0], PARAGRAPHS[9]: [200.0]})
        result = _run(fakes)
        qa = result.qa
        assert qa["status"] == QA_NEEDS_REVIEW
        assert qa["suspects"] == 2
        assert qa["retried"] == []
        assert qa["retake_calls"] == 0
        assert [u["position"] for u in qa["unresolved"]] == [_pos(1) + 1, _pos(9) + 1]
        assert all(u["attempts"] == 0 for u in qa["unresolved"])
        # No ElevenLabs call beyond the first pass.
        assert len(fakes.syn_calls) == len(result.manifest)

    def test_changed_mode_does_not_retry_reused_segments(self):
        texts = _plan_texts()
        manifest = [
            {
                "hash": segment_hash(t),
                "key": f"articles/audio/1/segments/{segment_hash(t)}.mp3",
                "is_heading": i < 2,
                "duration_ms": 500,
            }
            for i, t in enumerate(texts)
        ]
        by_key = {e["key"]: t for e, t in zip(manifest, texts, strict=True)}
        edited = PARAGRAPHS[10] + " Editado."
        content = CONTENT.replace(PARAGRAPHS[10], edited)
        fakes = Fakes(
            {
                PARAGRAPHS[2]: [210.0],  # reused, high: already accepted
                edited: [199.0, 150.0],  # new, high: re-taken
            }
        )
        with _patched(fakes, downloads=lambda key: f"reused:{by_key[key]}".encode()):
            result = synthesize_article(1, TITLE, content, manifest=manifest, mode="changed")
        qa = result.qa
        assert result.synthesized == 1 and result.reused == len(texts) - 1
        # Reused segments count toward the baseline and the measured total.
        assert qa["measured"] == N_PARAGRAPHS
        assert qa["suspects"] == 1
        assert [r["position"] for r in qa["retried"]] == [_pos(10) + 1]
        assert qa["status"] == QA_FIXED
        assert [t for t, _ in fakes.syn_calls] == [edited, edited]

    def test_headings_and_short_segments_are_skipped(self):
        short = PARAGRAPHS[0]
        fakes = Fakes({short: [260.0]}, short={short})
        result = _run(fakes)
        qa = result.qa
        assert qa["measured"] == N_PARAGRAPHS - 1
        assert qa["skipped"] == 3  # title, heading, short paragraph
        assert qa["status"] == QA_OK
        # Headings are never decoded for pitch.
        heading_takes = {f"{t}#1".encode() for t in _plan_texts()[:2]}
        assert not heading_takes & set(fakes.measured)

    def test_low_pitch_is_not_flagged(self):
        fakes = Fakes({PARAGRAPHS[6]: [110.0]})  # -24 %
        result = _run(fakes)
        assert result.qa["status"] == QA_OK
        assert result.qa["suspects"] == 0
        assert len(fakes.syn_calls) == len(result.manifest)

    def test_unmeasurable_article_is_ok(self):
        fakes = Fakes(short=set(PARAGRAPHS))
        result = _run(fakes)
        assert result.qa["status"] == QA_OK
        assert result.qa["baseline_hz"] is None
        assert result.qa["measured"] == 0

    def test_failed_retake_call_keeps_the_first_take(self):
        bad = PARAGRAPHS[4]
        fakes = Fakes({bad: [200.0]})
        original = fakes.synthesize

        def flaky(text, **kwargs):
            if text == bad and fakes.takes[text] >= 1:
                raise RuntimeError("ElevenLabs 500")
            return original(text, **kwargs)

        fakes.synthesize = flaky
        result = _run(fakes)
        (unresolved,) = result.qa["unresolved"]
        assert unresolved["hz"] == 200.0
        assert unresolved["attempts"] == 0
        assert result.qa["status"] == QA_NEEDS_REVIEW


class TestRetakeApplyFailure:
    """#608: a failed post-process or upload of an accepted take keeps the original."""

    BAD_IDX = 5
    BAD = PARAGRAPHS[BAD_IDX]

    def _fakes(self):
        # First take high, re-take #1 within the threshold (accepted).
        return Fakes({self.BAD: [197.5, 146.0]})

    def _assert_original_kept(self, fakes, result, reason_fragment):
        bad, bad_idx = self.BAD, self.BAD_IDX
        qa = result.qa
        # The paid run succeeded and assembled the ORIGINAL take...
        assert result.mp3 == b"FINAL"
        assert fakes.in_read(_pos(bad_idx)) == f"{bad}#1".encode()
        # ...and the object at its hash key is that same clip, so a later
        # "changed" run reuses what the manifest's duration describes.
        assert fakes.stored(bad) == f"{bad}#1".encode()
        assert result.manifest[_pos(bad_idx)]["duration_ms"] == 820
        # The paragraph is reported unresolved, with the original's pitch.
        assert qa["status"] == QA_NEEDS_REVIEW
        (retried,) = qa["retried"]
        assert retried["resolved"] is False
        assert retried["hz_after"] == 197.5
        assert reason_fragment in retried["reason"]
        (unresolved,) = qa["unresolved"]
        assert unresolved["position"] == _pos(bad_idx) + 1
        assert unresolved["hz"] == 197.5
        assert unresolved["attempts"] == 1
        assert reason_fragment in unresolved["reason"]

    def test_upload_failure_keeps_the_original(self):
        fakes = self._fakes()
        retake = f"{self.BAD}#2".encode()
        with (
            _patched(fakes) as storage,
            patch("app.services.article_audio.sentry_sdk.capture_exception") as capture,
        ):

            def upload(data, key, ct):
                if data == retake:
                    raise RuntimeError("Spaces 503")
                fakes.uploads.append((key, data))

            storage.upload_bytes.side_effect = upload
            result = synthesize_article(1, TITLE, CONTENT)
        self._assert_original_kept(fakes, result, "upload failed: Spaces 503")
        capture.assert_called_once()

    def test_process_failure_keeps_the_original_and_never_uploads_the_take(self):
        fakes = self._fakes()
        retake = f"{self.BAD}#2".encode()
        original = fakes.process

        def process(mp3, **kwargs):
            if mp3 == retake:
                raise RuntimeError("ffmpeg exited 1")
            return original(mp3, **kwargs)

        fakes.process = process
        with (
            _patched(fakes),
            patch("app.services.article_audio.sentry_sdk.capture_exception") as capture,
        ):
            result = synthesize_article(1, TITLE, CONTENT)
        self._assert_original_kept(fakes, result, "post-processing failed: ffmpeg exited 1")
        # Processed first, so the failed take was never written over the original.
        assert retake not in [d for _, d in fakes.uploads]
        capture.assert_called_once()

    def test_successful_retake_carries_no_reason(self):
        result = _run(self._fakes())
        (retried,) = result.qa["retried"]
        assert "reason" not in retried


_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture
def _admin():
    from app.api.dependencies import (
        validate_admin_or_allowlisted_driver_token,
        validate_admin_token,
    )
    from app.main import app

    app.dependency_overrides[validate_admin_token] = lambda: _FAKE_ADMIN
    app.dependency_overrides[validate_admin_or_allowlisted_driver_token] = lambda: _FAKE_ADMIN
    yield
    app.dependency_overrides.pop(validate_admin_token, None)
    app.dependency_overrides.pop(validate_admin_or_allowlisted_driver_token, None)


def test_job_persists_the_voice_check_and_admin_exposes_it(
    test_client, test_db, category_factory, narration_job_session, _admin
):
    category = category_factory()
    suggestion = ArticleSuggestion(
        title="T",
        category_id=category.id,
        status="APPROVED",
        main_topic="T",
        sub_topics=[],
        point_of_view="narrative",
    )
    test_db.add(suggestion)
    test_db.flush()
    research = Research(suggestion_id=suggestion.id, content="x", status="APPROVED")
    test_db.add(research)
    test_db.flush()
    article = Article(
        research_id=research.id,
        category_id=category.id,
        title="T",
        title_es=TITLE,
        content="c",
        content_es=CONTENT,
        status="APPROVED",
    )
    test_db.add(article)
    test_db.commit()

    fakes = Fakes({PARAGRAPHS[5]: [197.5, 146.0]})
    with _patched(fakes) as storage:
        storage.upload_bytes.side_effect = lambda data, key, ct: "https://cdn.example/x.mp3"
        resp = test_client.post(f"/api/v1/admin/dashboard/articles/{article.id}/generate-audio")
    assert resp.status_code == 202, resp.text
    job = test_client.get(f"/api/v1/media-library/jobs/{resp.json()['id']}").json()
    assert job["status"] == "SUCCEEDED", job
    assert job["stats"]["voice_check"] == {
        "status": QA_FIXED,
        "baseline_hz": BASE_HZ,
        "measured": N_PARAGRAPHS,
        "skipped": 2,
        "suspects": 1,
        "retried": 1,
        "unresolved": 0,
        "retake_calls": 1,
    }

    test_db.expire_all()
    row = test_db.get(Article, article.id)
    assert row.audio_qa_es["status"] == QA_FIXED
    assert row.audio_qa_es["retried"][0]["position"] == _pos(5) + 1

    detail = test_client.get(f"/api/v1/admin/dashboard/articles/{article.id}").json()
    assert detail["audio_qa_es"]["status"] == QA_FIXED
    listing = test_client.get("/api/v1/admin/dashboard/articles").json()
    item = next(a for a in listing if a["id"] == article.id)
    assert item["audio_qa_es"]["status"] == QA_FIXED
