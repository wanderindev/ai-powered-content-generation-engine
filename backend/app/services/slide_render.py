"""Render structured slides into branded 1280x720 PNG frames (#321 half B).

The deck produced by ``generate_edu_slides`` lands as ``EduSlide`` rows
(template / title / body / image_query / media picks). This stage closes the
loop: for each slide it embeds the human-picked photo (``media_path``, chosen
in the admin editor — ``image_query`` is only the picker's search hint, never
auto-matched), builds standalone HTML via
:func:`app.services.slide_html.build_slide_html`, screenshots it with headless
Chromium (Playwright), and uploads the PNG to DO Spaces. The rendered frame URL
is written to ``EduSlide.image_url`` — the per-slide artifact the video stage
(#323) muxes with the narration audio (#322).

Playwright's **sync** API is used. It must not run inside an asyncio event loop,
so the caller must be a *sync* (threadpool) FastAPI endpoint, never an ``async``
one. The chromium binary is provisioned in the backend image (see the
Dockerfiles); a missing browser surfaces as a clear runtime error.
"""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.edu_material import EduMaterial
from app.models.enums import SlideTemplate
from app.services.image_storage import ImageStorageService
from app.services.slide_html import autofit_script, build_slide_html, heroicons_script

logger = logging.getLogger(__name__)

# Fixed 16:9 frame at device-scale 1 → the screenshot is exactly 1280x720 px.
VIEWPORT = {"width": 1280, "height": 720}
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--hide-scrollbars"]
_SET_CONTENT_TIMEOUT_MS = 20_000
# The autofit flag is set synchronously during add_script_tag, so on a healthy
# page the wait returns immediately. The generous budget is for prod: decoding/
# painting a large media_path photo (LOC scans) can peg the renderer's main
# thread on the droplet's shared CPU, delaying the wait's evaluation well past
# 3s even though the flag is already set (Sentry PIC-PYTHON-FASTAPI-1K).
_AUTOFIT_TIMEOUT_MS = 15_000


@contextmanager
def _chromium_page():
    """Yield a single 1280x720 chromium page; the browser is closed on exit.

    Reuses one browser/page across a whole deck — launching chromium per slide
    would dominate the wall-clock. Raises a clear RuntimeError if the browser
    binary isn't installed in the image.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - dep is in requirements.txt
        raise RuntimeError("playwright is not installed") from e

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=_LAUNCH_ARGS)
        except Exception as e:
            raise RuntimeError(
                "Failed to launch headless chromium — is the browser binary "
                "installed (`playwright install chromium`)?"
            ) from e
        try:
            yield browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        finally:
            browser.close()


def _screenshot(page, html: str) -> bytes:
    """Set the page content and screenshot the ``.slide`` element as PNG bytes."""
    # "load" fires once images (the embedded photo + data-URI logos) have settled.
    page.set_content(html, wait_until="load", timeout=_SET_CONTENT_TIMEOUT_MS)
    # chromium does not run the inline <script> from set_content, so the heroicon
    # markers (data-hicon) stay empty — re-inject the hydration JS, which runs and
    # fills them synchronously before we capture. Same for the shrink-to-fit pass
    # (after the icons, which affect layout); wait for its done flag so the
    # screenshot never races the font-size stepping.
    page.add_script_tag(content=heroicons_script())
    page.add_script_tag(content=autofit_script())
    page.wait_for_function("window.__autofit_done === true", timeout=_AUTOFIT_TIMEOUT_MS)
    element = page.query_selector(".slide")
    if element is None:  # pragma: no cover - build_slide_html always emits .slide
        return page.screenshot(type="png", clip={"x": 0, "y": 0, **VIEWPORT})
    return element.screenshot(type="png")


def render_to_png(html: str) -> bytes:
    """Render one standalone slide-HTML page to a 1280x720 PNG (one-shot browser).

    Convenience wrapper used by the verification test; the deck renderer drives
    :func:`_chromium_page` directly to reuse one browser across all slides.
    """
    with _chromium_page() as page:
        return _screenshot(page, html)


def render_material_slides(
    material: EduMaterial, db: Session, positions: set[int] | None = None
) -> dict:
    """Render a slides-material's slides to branded PNG frames.

    For each ``EduSlide`` (in ``position`` order): embed its human-picked photo
    (``media_path``; slides without a pick render the no-photo brand variant),
    build the HTML, screenshot it, upload the PNG to DO Spaces, and write the
    frame URL to ``EduSlide.image_url``. Mutates the rows in place; the caller
    commits.

    ``positions`` (1-based) restricts rendering to a subset — used to re-render
    only slides whose content or photo changed. The deck-wide section numeral is
    still computed by walking every slide, so a subset render keeps consistent
    numbering; slides outside the subset are not touched.

    Returns a summary dict ``{rendered, images_resolved, images_missing}`` —
    resolved/missing now mean picked/unpicked (over the rendered subset), so the
    admin can spot slides still waiting on a photo before assembling the video.
    Raises ``ValueError`` if the material has no slides (or the subset is empty).
    """
    slides = material.slides  # relationship is ordered by EduSlide.position
    if not slides:
        raise ValueError("Material has no slides to render")
    if positions is not None and not any(s.position in positions for s in slides):
        raise ValueError("None of the requested positions match this deck's slides")

    rendered = 0
    images_resolved = 0
    # The kit's kicker line, shared by every slide of the deck (and the
    # full-image chip label): "Excursión académica · <band>".
    eyebrow = "Excursión académica"
    if material.grade_band:
        eyebrow = f"{eyebrow} · {material.grade_band}"
    storage = ImageStorageService()
    section_no = 0  # 1-based count of section dividers, drives the big numeral
    try:
        with _chromium_page() as page:
            for slide in slides:
                # Advance the deck-wide section numeral for every slide so a
                # subset render still numbers section dividers consistently.
                if slide.template == SlideTemplate.SECTION.value:
                    section_no += 1
                if positions is not None and slide.position not in positions:
                    continue
                photo_url = slide.media_path or None
                if photo_url:
                    images_resolved += 1
                html = build_slide_html(
                    {
                        "template": slide.template,
                        "title": slide.title,
                        "body": slide.body,
                        "image_url": photo_url,
                    },
                    eyebrow=eyebrow,
                    section_no=section_no
                    if slide.template == SlideTemplate.SECTION.value
                    else None,
                )
                png = _screenshot(page, html)
                key = f"materials/slides/frames/{material.id}/{slide.position:02d}.png"
                slide.image_url = storage.upload_image(png, key, "image/png")
                rendered += 1
    finally:
        storage.close()

    material.updated_at = datetime.now(UTC)
    logger.info(
        "Rendered %d slide frames for material id=%s (%d with a resolved photo)",
        rendered,
        material.id,
        images_resolved,
    )
    return {
        "rendered": rendered,
        "images_resolved": images_resolved,
        "images_missing": rendered - images_resolved,
    }
