"""Tests for the slide render stage (app.services.slide_render, #321 half B).

Two layers:
  * the render endpoint — wiring/validation with render patched out
  * real chromium renders — gated behind _chromium_available(), so they run
    under `scripts/run-tests.sh --build` (browser baked into the image) and
    skip cleanly under plain `pytest` / CI (pip install only, no browser).

Slides embed only the human-picked ``media_path`` (slides redesign) — the old
``image_query`` auto-matcher (resolve_slide_image) is gone.
"""

import functools
import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.services.slide_render import render_material_slides, render_to_png
from PIL import Image

# A 1x1 PNG as a data URI — embeds a "photo" into the slide with no network,
# keeping the real-render tests fully offline.
_DATA_URI_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@functools.lru_cache(maxsize=1)
def _chromium_available() -> bool:
    """True only if a launchable chromium binary is present (image, not CI)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            browser.close()
        return True
    except Exception:
        return False


requires_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="headless chromium not installed — run under scripts/run-tests.sh --build",
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


# --- Helpers ----------------------------------------------------------------


def _slides_material(
    db,
    n=2,
    image_query="ruinas de panama viejo",
    material_type="slides",
    media_path=None,
):
    """A slides-type EduMaterial with n bullet slides sharing one image_query."""
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
    for i in range(1, n + 1):
        m.slides.append(
            EduSlide(
                position=i,
                template="bullets",
                title=f"Slide {i}",
                body="Uno\nDos\nTres",
                image_query=image_query,
                media_path=media_path,
                narration="Hola.",
            )
        )
    db.commit()
    db.refresh(m)
    return m


# --- POST /materials/{id}/render-slides (render patched out) ----------------


class TestRenderEndpoint:
    URL = "/api/v1/admin/dashboard/edu/materials/{id}/render-slides"

    def test_missing_material_404(self, test_client, test_db):
        resp = test_client.post(self.URL.format(id=999999))
        assert resp.status_code == 404

    def test_non_slides_material_400(self, test_client, test_db):
        m = _slides_material(test_db, material_type="guide")
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400

    def test_empty_deck_400(self, test_client, test_db):
        m = _slides_material(test_db, n=0)
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 400

    @patch("app.api.edu.render_material_slides")
    def test_success_sets_urls_and_counts(self, mock_render, test_client, test_db):
        m = _slides_material(test_db, n=3)

        def fake(material, db, positions=None):
            for slide in material.slides:
                slide.image_url = f"https://cdn.example/frames/{material.id}/{slide.position}.png"
            return {"rendered": 3, "images_resolved": 2, "images_missing": 1}

        mock_render.side_effect = fake

        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["slides_rendered"] == 3
        assert body["images_resolved"] == 2
        assert body["images_missing"] == 1
        assert len(body["slide_image_urls"]) == 3

    @patch("app.api.edu.render_material_slides", side_effect=RuntimeError("no chromium"))
    def test_render_engine_unavailable_500(self, _mock_render, test_client, test_db):
        m = _slides_material(test_db, n=1)
        resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 500

    @patch("app.api.edu.render_material_slides")
    def test_positions_threaded_to_service(self, mock_render, test_client, test_db):
        m = _slides_material(test_db, n=4)
        mock_render.return_value = {"rendered": 2, "images_resolved": 0, "images_missing": 2}
        resp = test_client.post(self.URL.format(id=m.id), json={"positions": [3, 1, 3]})
        assert resp.status_code == 200
        assert mock_render.call_args.kwargs["positions"] == {1, 3}

    def test_out_of_range_positions_422(self, test_client, test_db):
        m = _slides_material(test_db, n=2)
        with patch("app.api.edu.render_material_slides") as svc:
            resp = test_client.post(self.URL.format(id=m.id), json={"positions": [9]})
        assert resp.status_code == 422
        svc.assert_not_called()

    def test_whole_deck_when_positions_omitted(self, test_client, test_db):
        m = _slides_material(test_db, n=2)
        with patch("app.api.edu.render_material_slides") as svc:
            svc.return_value = {"rendered": 2, "images_resolved": 0, "images_missing": 2}
            resp = test_client.post(self.URL.format(id=m.id))
        assert resp.status_code == 200
        assert svc.call_args.kwargs["positions"] is None


# --- Real chromium renders (gated) ------------------------------------------


class _FakeStorage:
    """Captures uploads instead of hitting DO Spaces."""

    def __init__(self):
        self.uploads = []

    def upload_image(self, data, key, content_type="image/jpeg"):
        self.uploads.append((key, content_type, data))
        return f"https://cdn.example/{key}"

    def close(self):
        pass


@requires_chromium
def test_render_to_png_real_is_1280x720_png():
    from app.services.slide_html import build_slide_html

    html = build_slide_html({"template": "bullets", "title": "Hola", "body": "Uno\nDos"})
    png = render_to_png(html)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(png)) as img:
        assert img.size == (1280, 720)


@requires_chromium
def test_render_material_slides_real_end_to_end(test_db):
    # Slides carry a human-picked media_path (a data URI so the embedded photo
    # loads offline) — the renderer embeds it directly, no auto-matching.
    material = _slides_material(test_db, n=2, media_path=_DATA_URI_PNG)

    fake = _FakeStorage()
    with patch("app.services.slide_render.ImageStorageService", return_value=fake):
        summary = render_material_slides(material, test_db)

    assert summary == {"rendered": 2, "images_resolved": 2, "images_missing": 0}
    assert len(fake.uploads) == 2
    for slide in material.slides:
        assert slide.image_url and slide.image_url.startswith("https://cdn.example/")
    # Every uploaded frame is a real 1280x720 PNG.
    for key, content_type, data in fake.uploads:
        assert content_type == "image/png"
        assert key.startswith(f"materials/slides/frames/{material.id}/")
        with Image.open(io.BytesIO(data)) as img:
            assert img.size == (1280, 720)


@requires_chromium
def test_subset_render_touches_only_targeted_slides(test_db):
    # A subset render must set image_url on ONLY the targeted slides and leave
    # every other slide's existing frame untouched.
    material = _slides_material(test_db, n=3, media_path=_DATA_URI_PNG)
    for slide in material.slides:
        slide.image_url = f"https://cdn.example/old/{slide.position}.png"
    test_db.commit()

    fake = _FakeStorage()
    with patch("app.services.slide_render.ImageStorageService", return_value=fake):
        summary = render_material_slides(material, test_db, positions={2})

    assert summary == {"rendered": 1, "images_resolved": 1, "images_missing": 0}
    assert len(fake.uploads) == 1
    assert material.slides[0].image_url == "https://cdn.example/old/1.png"  # untouched
    assert material.slides[1].image_url.startswith("https://cdn.example/materials/")  # re-rendered
    assert material.slides[2].image_url == "https://cdn.example/old/3.png"  # untouched


@requires_chromium
def test_unpicked_slide_renders_no_photo_variant(test_db):
    # No media_path on the slides → still renders (brand no-photo variant), and
    # the summary surfaces them as missing so the admin can pick photos.
    material = _slides_material(test_db, n=2, media_path=None)

    fake = _FakeStorage()
    with patch("app.services.slide_render.ImageStorageService", return_value=fake):
        summary = render_material_slides(material, test_db)

    assert summary == {"rendered": 2, "images_resolved": 0, "images_missing": 2}
    for slide in material.slides:
        assert slide.image_url


@requires_chromium
def test_autofit_shrinks_overflowing_bullets_and_leaves_short_decks_alone():
    from app.services.slide_html import autofit_script, build_slide_html, heroicons_script
    from app.services.slide_render import _AUTOFIT_TIMEOUT_MS, _chromium_page

    long_bullets = "\n".join(
        f"Punto {i}: una línea de texto bastante larga para forzar el desbordamiento vertical"
        for i in range(1, 9)
    )
    overflowing = build_slide_html(
        {
            "template": "bullets",
            "title": "Un título largo que ocupa varias líneas en el lienzo",
            "body": long_bullets,
        }
    )
    short = build_slide_html({"template": "bullets", "title": "Corto", "body": "Uno\nDos"})

    def fit_value(html):
        with _chromium_page() as page:
            page.set_content(html, wait_until="load")
            page.add_script_tag(content=heroicons_script())
            page.add_script_tag(content=autofit_script())
            page.wait_for_function("window.__autofit_done === true", timeout=_AUTOFIT_TIMEOUT_MS)
            return float(
                page.evaluate(
                    "getComputedStyle(document.querySelector('.slide')).getPropertyValue('--fit') || '1'"
                )
            )

    assert fit_value(overflowing) < 1.0
    assert fit_value(short) == 1.0
