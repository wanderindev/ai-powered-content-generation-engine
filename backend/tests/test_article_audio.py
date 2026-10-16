"""Tests for the Spanish article narration (app.services.article_audio).

ElevenLabs is never called for real — ``synthesize`` is mocked. pydub needs
ffmpeg, so the endpoint tests mock the post-processing seams
(``process_segment`` / ``assemble``) and assert on orchestration:
segmentation, stitching context, storage, and the DB write. The DSP itself is
covered by tests/test_narration_postprocess.py (ffmpeg-gated).
"""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models import ArticleSuggestion, Research
from app.models.article import Article
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.services.article_audio import segment_article, segment_hash
from app.services.narration_postprocess import ProcessedSegment

_FAKE_ADMIN = SimpleNamespace(user_id=None, admin_email="test@localhost")


@pytest.fixture(autouse=True)
def _bypass_admin_auth():
    # dependency_overrides keys on the exact callable a router declares: the
    # dashboard router uses the allowlisted-driver variant.
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


@pytest.fixture(autouse=True)
def _narration_runner_on_test_db(narration_job_session):
    yield


def _finished(test_client, resp, status="SUCCEEDED"):
    """generate-audio is a background job (#547): assert the 202, then read the
    job row — TestClient runs the background task before returning."""
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["job_type"] == "ARTICLE_NARRATION"
    job = test_client.get(f"/api/v1/media-library/jobs/{job['id']}").json()
    assert job["status"] == status, job
    return job["stats"] if status == "SUCCEEDED" else job


MD = """**Acerca de este artículo**

Este artículo es la parte 1 de una serie.

- [Otra parte](/es/articulos/otra-parte)

---
## Cuarenta días al año

Primer párrafo con [un enlace](https://x.com) y *énfasis*.

Segundo párrafo.

## El fracaso

Tercer párrafo con **negritas**.

---

Continuar leyendo

¿Listo para la siguiente parte?"""


class TestSegmentation:
    def test_title_first_preamble_and_cta_stripped_markdown_cleaned(self):
        segments = segment_article("De Nombre de Dios a Portobelo", MD)
        texts = [t for t, _ in segments]
        flags = [h for _, h in segments]
        assert texts == [
            "De Nombre de Dios a Portobelo.",
            "Parte 1: Cuarenta días al año.",
            "Primer párrafo con un enlace y énfasis.",
            "Segundo párrafo.",
            "Parte 2: El fracaso.",
            "Tercer párrafo con negritas.",
        ]
        assert flags == [True, True, False, False, True, False]

    def test_intro_and_conclusion_headings_are_not_numbered(self):
        md = (
            "## Introducción: El último viaje\n\nUno.\n\n## La travesía\n\nDos.\n\n"
            "## El regreso\n\nTres.\n\n## Conclusión: el eje del imperio\n\nCuatro."
        )
        headings = [t for t, h in segment_article("", md) if h]
        assert headings == [
            "Introducción: El último viaje.",
            "Parte 1: La travesía.",
            "Parte 2: El regreso.",
            "Conclusión: el eje del imperio.",
        ]

    def test_narration_respells_ngabe_without_touching_the_article(self):
        md = "Los ngäbe y los Ngäbe-Buglé.\n\n## Los ngäbes hoy"
        assert segment_article("Ngäbe", md) == [
            ("Nobe.", True),
            ("Los nobe y los Nobe-Buglé.", False),
            ("Parte 1: Los nobes hoy.", True),
        ]

    def test_cta_header_without_a_rule_still_ends_the_read(self):
        # Older series members carry the CTA block with no ``---`` in front of
        # it; 36 production narrations read it aloud before this guard.
        md = (
            "Primer párrafo.\n\n**Continuar leyendo**\n\n"
            "¿Listo para la siguiente parte? Continúa a la Parte 2: [X](/es/articulos/x)"
        )
        assert segment_article("Título", md) == [("Título.", True), ("Primer párrafo.", False)]

    def test_content_without_preamble_rule_is_read_from_the_top(self):
        segments = segment_article("Título", "Solo un párrafo.")
        assert segments == [("Título.", True), ("Solo un párrafo.", False)]


def _seed_article(test_db, category_factory, **overrides):
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
    fields = {
        "research_id": research.id,
        "category_id": category.id,
        "title": "English Title",
        "title_es": "Título en español",
        "content": "English content",
        "content_es": "Primer párrafo.\n\nSegundo párrafo.",
        "status": "APPROVED",
    }
    fields.update(overrides)
    article = Article(**fields)
    test_db.add(article)
    test_db.commit()
    return article


def _fake_process(mp3, *, text, is_heading, out_wav):
    """Stand-in for narration_postprocess.process_segment (needs ffmpeg)."""
    return ProcessedSegment(
        duration_ms=640 if is_heading else 820,
        blips_removed=0,
        gain_db=0.0,
        limited=False,
        wpm=None,
        tempo=1.0,
    )


def _fake_assemble(parts, out_mp3):
    with open(out_mp3, "wb") as fh:
        fh.write(b"FINALMP3")


class TestGenerateAudioEndpoint:
    URL = "/api/v1/admin/dashboard/articles/{id}/generate-audio"

    def _patches(self):
        return (
            patch("app.services.article_audio.synthesize", return_value=b"MP3SEG"),
            patch("app.services.article_audio.process_segment", side_effect=_fake_process),
            patch("app.services.article_audio.assemble", side_effect=_fake_assemble),
            patch("app.services.article_audio.ImageStorageService"),
        )

    def test_narrates_uploads_and_persists_url(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn as syn, p_proc, p_asm, p_storage as storage_cls:
            storage = storage_cls.return_value
            storage.upload_bytes.return_value = "https://cdn.example/articles/audio/1/es_1.mp3"

            resp = test_client.post(self.URL.format(id=article.id))

        data = _finished(test_client, resp)
        assert data["segments"] == 3  # title + 2 paragraphs
        assert data["synthesized"] == 3
        assert data["reused"] == 0
        assert data["audio_url_es"] == "https://cdn.example/articles/audio/1/es_1.mp3"
        assert syn.call_count == 3
        # Per-paragraph stitching: the middle segment carries both neighbours.
        middle = syn.call_args_list[1].kwargs
        assert middle["previous_text"] == "Título en español."
        assert middle["next_text"] == "Segundo párrafo."
        # Headings (the title here) get no next-context — with it the voice
        # reads them with an unfinished, mid-sentence inflection.
        assert syn.call_args_list[0].kwargs["next_text"] is None
        # The upload used the audio content type.
        assert storage.upload_bytes.call_args.args[2] == "audio/mpeg"
        # Every segment was also stored under its hash key for later reuse.
        segment_keys = [
            c.args[1] for c in storage.upload_bytes.call_args_list if "/segments/" in c.args[1]
        ]
        assert len(segment_keys) == 3

        test_db.expire_all()
        row = test_db.query(Article).filter(Article.id == article.id).first()
        assert row.audio_url_es == "https://cdn.example/articles/audio/1/es_1.mp3"
        assert [e["hash"] for e in row.audio_manifest_es] == [
            segment_hash("Título en español."),
            segment_hash("Primer párrafo."),
            segment_hash("Segundo párrafo."),
        ]
        assert all(
            e["key"].startswith(f"articles/audio/{article.id}/segments/")
            for e in row.audio_manifest_es
        )
        # The manifest carries the PROCESSED durations (the public player's
        # length is derived from them), and the response total matches.
        assert [e["duration_ms"] for e in row.audio_manifest_es] == [640, 820, 820]
        assert data["duration_ms"] == 640 + 1000 + 820 + 1000 + 820
        # The final upload is the assembled read; the segments went up raw.
        assert storage.upload_bytes.call_args.args[0] == b"FINALMP3"
        assert {c.args[0] for c in storage.upload_bytes.call_args_list[:-1]} == {b"MP3SEG"}

    def test_regenerate_deletes_superseded_object(self, test_client, test_db, category_factory):
        article = _seed_article(
            test_db,
            category_factory,
            audio_url_es="https://cdn.example/articles/audio/1/es_100.mp3",
        )
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn, p_proc, p_asm, p_storage as storage_cls:
            storage = storage_cls.return_value
            storage.upload_bytes.return_value = "https://cdn.example/articles/audio/1/es_200.mp3"

            resp = test_client.post(self.URL.format(id=article.id))

        _finished(test_client, resp)
        storage.delete_image.assert_called_once_with(f"articles/audio/{article.id}/es_100.mp3")
        test_db.expire_all()
        row = test_db.query(Article).filter(Article.id == article.id).first()
        assert row.audio_url_es == "https://cdn.example/articles/audio/1/es_200.mp3"

    def test_postprocess_failure_stops_paying_for_tts(self, test_client, test_db, category_factory):
        content = "\n\n".join(f"Párrafo número {n}." for n in range(12))
        article = _seed_article(test_db, category_factory, content_es=content)

        def _slow_tts(*args, **kwargs):
            time.sleep(0.05)  # give the failing worker time to finish first
            return b"MP3SEG"

        p_syn, p_proc, p_asm, p_storage = self._patches()
        with (
            p_syn as syn,
            patch(
                "app.services.article_audio.process_segment",
                side_effect=RuntimeError("ffmpeg failed: boom"),
            ),
            p_asm as asm,
            p_storage,
        ):
            syn.side_effect = _slow_tts
            resp = test_client.post(self.URL.format(id=article.id))

        job = _finished(test_client, resp, status="FAILED")
        assert "ffmpeg failed" in job["error"]
        assert syn.call_count < 13  # title + 12 paragraphs would be 13
        asm.assert_not_called()
        test_db.expire_all()
        assert test_db.get(Article, article.id).audio_url_es is None

    def test_requires_spanish_content(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory, content_es=None)
        resp = test_client.post(self.URL.format(id=article.id))
        assert resp.status_code == 400
        assert "Spanish" in resp.json()["detail"]

    def test_unknown_article_404s(self, test_client, test_db):
        assert test_client.post(self.URL.format(id=99999)).status_code == 404


def _manifest_for(article_id, texts_with_flags):
    return [
        {
            "hash": segment_hash(text),
            "key": f"articles/audio/{article_id}/segments/{segment_hash(text)}.mp3",
            "is_heading": is_heading,
            "duration_ms": 500,
        }
        for text, is_heading in texts_with_flags
    ]


class TestSelectiveRegeneration:
    URL = "/api/v1/admin/dashboard/articles/{id}/generate-audio"

    _patches = TestGenerateAudioEndpoint._patches

    def _seed_with_manifest(self, test_db, category_factory, **overrides):
        article = _seed_article(test_db, category_factory, **overrides)
        article.audio_url_es = f"https://cdn.example/articles/audio/{article.id}/es_100.mp3"
        article.audio_manifest_es = _manifest_for(
            article.id,
            [("Título en español.", True), ("Primer párrafo.", False), ("Segundo párrafo.", False)],
        )
        test_db.commit()
        return article

    def test_changed_mode_pays_only_for_edited_segments(
        self, test_client, test_db, category_factory
    ):
        article = self._seed_with_manifest(test_db, category_factory)
        old_p2_key = article.audio_manifest_es[2]["key"]
        article.content_es = "Primer párrafo.\n\nSegundo párrafo corregido."
        test_db.commit()

        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn as syn, p_proc, p_asm, p_storage as storage_cls:
            storage = storage_cls.return_value
            storage.download_bytes.return_value = b"REUSEDMP3"
            storage.upload_bytes.return_value = (
                f"https://cdn.example/articles/audio/{article.id}/es_200.mp3"
            )

            resp = test_client.post(self.URL.format(id=article.id), json={"mode": "changed"})

        data = _finished(test_client, resp)
        assert data["synthesized"] == 1
        assert data["reused"] == 2
        # Only the edited paragraph hit ElevenLabs, with fresh neighbour context.
        assert syn.call_count == 1
        call = syn.call_args
        assert call.args[0] == "Segundo párrafo corregido."
        assert call.kwargs["previous_text"] == "Primer párrafo."
        # Unchanged segments were fetched from storage, not re-synthesized.
        reused_keys = [c.args[0] for c in storage.download_bytes.call_args_list]
        assert len(reused_keys) == 2
        # The superseded paragraph's segment object was cleaned up.
        deleted = [c.args[0] for c in storage.delete_image.call_args_list]
        assert old_p2_key in deleted

        test_db.expire_all()
        row = test_db.query(Article).filter(Article.id == article.id).first()
        assert [e["hash"] for e in row.audio_manifest_es] == [
            segment_hash("Título en español."),
            segment_hash("Primer párrafo."),
            segment_hash("Segundo párrafo corregido."),
        ]

    def test_changed_mode_with_no_edits_400s_without_synthesis(
        self, test_client, test_db, category_factory
    ):
        article = self._seed_with_manifest(test_db, category_factory)
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn as syn, p_proc, p_asm, p_storage:
            resp = test_client.post(self.URL.format(id=article.id), json={"mode": "changed"})
        assert resp.status_code == 400
        assert "No changed segments" in resp.json()["detail"]
        syn.assert_not_called()

    def test_changed_mode_without_manifest_synthesizes_everything(
        self, test_client, test_db, category_factory
    ):
        # Audio generated before manifests existed: nothing reusable, full run.
        article = _seed_article(test_db, category_factory)
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn as syn, p_proc, p_asm, p_storage as storage_cls:
            storage_cls.return_value.upload_bytes.return_value = "https://cdn.example/a.mp3"
            resp = test_client.post(self.URL.format(id=article.id), json={"mode": "changed"})
        data = _finished(test_client, resp)
        assert data["synthesized"] == 3
        assert data["reused"] == 0
        assert syn.call_count == 3

    def test_missing_stored_segment_falls_back_to_synthesis(
        self, test_client, test_db, category_factory
    ):
        article = self._seed_with_manifest(test_db, category_factory)
        article.content_es = "Primer párrafo.\n\nSegundo párrafo corregido."
        test_db.commit()

        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn as syn, p_proc, p_asm, p_storage as storage_cls:
            storage = storage_cls.return_value
            storage.download_bytes.side_effect = RuntimeError("404 from Spaces")
            storage.upload_bytes.return_value = "https://cdn.example/a.mp3"
            resp = test_client.post(self.URL.format(id=article.id), json={"mode": "changed"})

        data = _finished(test_client, resp)
        assert data["synthesized"] == 3  # reuse failed → everything synthesized
        assert data["reused"] == 0
        assert syn.call_count == 3


class TestNarrationJob:
    """The background-job contract of generate-audio (#547)."""

    URL = "/api/v1/admin/dashboard/articles/{id}/generate-audio"
    _patches = TestGenerateAudioEndpoint._patches

    def _in_flight(self, test_db, article_id, mode="all", age=timedelta(0)):
        job = CrawlJob(
            job_type=CrawlJobType.ARTICLE_NARRATION.value,
            status=CrawlJobStatus.RUNNING.value,
            params={"article_id": article_id, "mode": mode},
            created_at=datetime.now(UTC) - age,
        )
        test_db.add(job)
        test_db.commit()
        return job

    def test_progress_is_recorded_per_segment(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn, p_proc, p_asm, p_storage as storage_cls:
            storage_cls.return_value.upload_bytes.return_value = "https://cdn.example/a.mp3"
            resp = test_client.post(self.URL.format(id=article.id))
        job = _finished(test_client, resp, status="SUCCEEDED")
        row = test_db.get(CrawlJob, resp.json()["id"])
        test_db.refresh(row)
        assert (row.units_done, row.units_total) == (3, 3)
        assert row.current_unit == "Segment 3/3"
        assert row.params == {"article_id": article.id, "mode": "all"}
        assert job["audio_url_es"] == "https://cdn.example/a.mp3"

    def test_identical_request_joins_the_running_job(
        self, test_client, test_db, category_factory
    ):
        article = _seed_article(test_db, category_factory)
        running = self._in_flight(test_db, article.id)
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn as syn, p_proc, p_asm, p_storage:
            resp = test_client.post(self.URL.format(id=article.id))
        assert resp.status_code == 202
        assert resp.json()["id"] == running.id
        syn.assert_not_called()  # no second runner was scheduled

    def test_different_mode_while_running_409s(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        article.audio_manifest_es = [{"hash": "stale", "key": "k", "is_heading": True}]
        test_db.commit()
        running = self._in_flight(test_db, article.id, mode="all")
        resp = test_client.post(self.URL.format(id=article.id), json={"mode": "changed"})
        assert resp.status_code == 409
        assert f"Job {running.id}" in resp.json()["detail"]

    def test_another_articles_job_does_not_block(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        self._in_flight(test_db, article.id + 1000)
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn, p_proc, p_asm, p_storage as storage_cls:
            storage_cls.return_value.upload_bytes.return_value = "https://cdn.example/a.mp3"
            resp = test_client.post(self.URL.format(id=article.id))
        _finished(test_client, resp)

    def test_stale_job_does_not_block(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        stale = self._in_flight(test_db, article.id, age=timedelta(hours=2))
        p_syn, p_proc, p_asm, p_storage = self._patches()
        with p_syn, p_proc, p_asm, p_storage as storage_cls:
            storage_cls.return_value.upload_bytes.return_value = "https://cdn.example/a.mp3"
            resp = test_client.post(self.URL.format(id=article.id))
        assert resp.json()["id"] != stale.id
        _finished(test_client, resp)

    def test_article_deleted_mid_run_fails_the_job(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        article_id = article.id

        def _delete_then_store(*args, **kwargs):
            test_db.query(Article).filter(Article.id == article_id).delete()
            test_db.commit()
            return "https://cdn.example/orphan.mp3"

        p_syn, p_proc, p_asm, p_storage = self._patches()
        with (
            p_syn,
            p_proc,
            p_asm,
            p_storage,
            patch(
                "app.services.article_narration_job.store_article_audio",
                side_effect=_delete_then_store,
            ),
        ):
            resp = test_client.post(self.URL.format(id=article_id))
        job = _finished(test_client, resp, status="FAILED")
        assert "deleted while narrating" in job["error"]

    def test_migration_0040_added_article_narration(self):
        """0040 historically added exactly ARTICLE_NARRATION to the job types."""
        import importlib.util
        from pathlib import Path

        def _load(name):
            path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / name
            spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        module = _load("0040_article_narration_job.py")
        previous = _load("0034_edu_job_types.py")
        assert set(module._OLD_VALUES) == set(previous._NEW_VALUES)
        assert set(module._NEW_VALUES) - set(module._OLD_VALUES) == {"ARTICLE_NARRATION"}


class TestAudioSegmentsEndpoint:
    URL = "/api/v1/admin/dashboard/articles/{id}/audio-segments"

    def test_without_manifest_everything_is_changed(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        data = test_client.get(self.URL.format(id=article.id)).json()
        assert data["has_audio"] is False
        assert data["has_manifest"] is False
        assert data["changed_positions"] == [1, 2, 3]
        assert data["segments"][0]["is_heading"] is True

    def test_diff_flags_only_edited_segments(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory)
        article.audio_url_es = "https://cdn.example/a.mp3"
        article.audio_manifest_es = _manifest_for(
            article.id,
            [("Título en español.", True), ("Primer párrafo.", False), ("Segundo párrafo.", False)],
        )
        article.content_es = "Primer párrafo.\n\nSegundo párrafo corregido.\n\nTercero nuevo."
        test_db.commit()

        data = test_client.get(self.URL.format(id=article.id)).json()
        assert data["has_audio"] is True
        assert data["has_manifest"] is True
        # Title and first paragraph unchanged; edited + brand-new flagged.
        assert data["changed_positions"] == [3, 4]
        assert data["segments"][2]["preview"] == "Segundo párrafo corregido."

    def test_requires_spanish_content(self, test_client, test_db, category_factory):
        article = _seed_article(test_db, category_factory, content_es=None)
        assert test_client.get(self.URL.format(id=article.id)).status_code == 400

    def test_unknown_article_404s(self, test_client, test_db):
        assert test_client.get(self.URL.format(id=99999)).status_code == 404


class TestPublicSurface:
    def test_audio_url_only_on_spanish_detail(self, test_client, test_db, category_factory):
        article = _seed_article(
            test_db,
            category_factory,
            audio_url_es="https://cdn.example/articles/audio/1/es_1.mp3",
        )
        es = test_client.get(f"/api/v1/articles/by-slug/{article.slug_es}?lang=es").json()
        en = test_client.get(f"/api/v1/articles/by-slug/{article.slug}?lang=en").json()
        assert es["audio_url"] == "https://cdn.example/articles/audio/1/es_1.mp3"
        assert en["audio_url"] is None
        # No manifest (pre-manifest narration) -> no computed duration.
        assert es["audio_duration_ms"] is None

    def test_audio_duration_computed_from_manifest(self, test_client, test_db, category_factory):
        """Duration = segment durations plus concat silences (1400ms before a
        heading, 1000ms otherwise, none before the first segment)."""
        article = _seed_article(
            test_db,
            category_factory,
            audio_url_es="https://cdn.example/articles/audio/1/es_1.mp3",
            audio_manifest_es=[
                {"hash": "a", "key": "k1", "is_heading": True, "duration_ms": 2000},
                {"hash": "b", "key": "k2", "is_heading": False, "duration_ms": 30000},
                {"hash": "c", "key": "k3", "is_heading": True, "duration_ms": 1500},
            ],
        )
        es = test_client.get(f"/api/v1/articles/by-slug/{article.slug_es}?lang=es").json()
        # 2000 + 1000 + 30000 + 1400 + 1500
        assert es["audio_duration_ms"] == 35900

    def test_admin_detail_exposes_audio_url_es(self, test_client, test_db, category_factory):
        article = _seed_article(
            test_db,
            category_factory,
            audio_url_es="https://cdn.example/articles/audio/1/es_1.mp3",
        )
        data = test_client.get(f"/api/v1/admin/dashboard/articles/{article.id}").json()
        assert data["audio_url_es"] == "https://cdn.example/articles/audio/1/es_1.mp3"
