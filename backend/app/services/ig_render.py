"""Render an Instagram post into a branded 1080x1350 JPEG on DO Spaces.

Builds standalone HTML via :func:`app.services.ig_html.build_ig_html`,
screenshots the ``.post`` element with headless Chromium (Playwright), converts
the PNG to JPEG (the Graph API's ``image_url`` accepts JPEG only) and uploads
it public-read — the publisher hands Instagram that public URL.

Playwright's **sync** API is used. It must not run inside an asyncio event
loop, so the caller must be a *sync* (threadpool) FastAPI endpoint, never an
``async`` one (same rule as the slide renderer).
"""

import io
import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from PIL import Image

from app.models.instagram_post import InstagramPost
from app.services.ig_html import build_ig_html, igfit_script
from app.services.image_storage import ImageStorageService

logger = logging.getLogger(__name__)

# Fixed 4:5 portrait canvas at device-scale 1 → exactly 1080x1350 px.
VIEWPORT = {"width": 1080, "height": 1350}
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--hide-scrollbars"]
_SET_CONTENT_TIMEOUT_MS = 20_000
# The igfit flag is set from document.fonts.ready; generous budget for prod
# (decoding a large polaroid/background photo can peg the shared CPU — same
# lesson as the slide renderer's autofit wait).
_IGFIT_TIMEOUT_MS = 15_000
_JPEG_QUALITY = 90


@contextmanager
def _chromium_page():
    """Yield a single 1080x1350 chromium page; the browser is closed on exit."""
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
    """Set the page content and screenshot the ``.post`` element as PNG bytes."""
    page.set_content(html, wait_until="load", timeout=_SET_CONTENT_TIMEOUT_MS)
    # Chromium does not run the inline <script> from set_content — re-inject the
    # shrink-to-fit pass and wait for its done flag (it also awaits the bundled
    # Caveat font) so the capture never races font paint or size stepping.
    page.add_script_tag(content=igfit_script())
    page.wait_for_function("window.__igfit_done === true", timeout=_IGFIT_TIMEOUT_MS)
    element = page.query_selector(".post")
    if element is None:  # pragma: no cover - build_ig_html always emits .post
        return page.screenshot(type="png", clip={"x": 0, "y": 0, **VIEWPORT})
    return element.screenshot(type="png")


def _png_to_jpeg(png_bytes: bytes) -> bytes:
    """Convert the captured PNG to a JPEG (RGB, quality 90) for the Graph API."""
    with Image.open(io.BytesIO(png_bytes)) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=_JPEG_QUALITY)
        return buf.getvalue()


def render_instagram_post(post: InstagramPost) -> str:
    """Render *post* to a public JPEG on Spaces and write ``post.image_url``.

    The key is timestamped (``instagram/{id}/render-{ts}.jpg``) so a re-render
    never fights the CDN cache; the previous artifact is deleted best-effort.
    Mutates the row in place; the caller commits. Returns the new URL.
    """
    html = build_ig_html(post)
    with _chromium_page() as page:
        png = _screenshot(page, html)
    jpeg = _png_to_jpeg(png)

    storage = ImageStorageService()
    try:
        previous_url = post.image_url
        key = f"instagram/{post.id}/render-{int(datetime.now(UTC).timestamp())}.jpg"
        post.image_url = storage.upload_bytes(jpeg, key, "image/jpeg")
        if previous_url and previous_url.startswith(f"{storage.cdn_url}/"):
            try:
                storage.delete_image(previous_url.removeprefix(f"{storage.cdn_url}/"))
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.warning("Could not delete previous render %s", previous_url, exc_info=True)
    finally:
        storage.close()

    post.updated_at = datetime.now(UTC)
    logger.info("Rendered instagram post id=%s -> %s", post.id, post.image_url)
    return post.image_url
