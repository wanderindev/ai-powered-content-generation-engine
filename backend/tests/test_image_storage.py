"""Tests for app.services.image_storage pure helpers — key building and watermarking.

DO NOT test ImageStorageService.download_image, upload_image, etc. — they're
DO Spaces / httpx integrations and out of scope for the pure-logic issue.
"""

import io
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from app.services.image_storage import (
    MAX_DOWNLOAD_SIZE,
    ImageStorageService,
    make_storage_key,
    strip_nlm_watermark,
    watermark_infographic,
    watermark_slides,
)
from PIL import Image, UnidentifiedImageError


class TestMakeStorageKey:
    """Covers make_storage_key(source, external_id, content_type)."""

    def test_wikimedia_strips_file_prefix(self):
        assert (
            make_storage_key("WIKIMEDIA", "File:Panama_Canal.jpg")
            == "media/wikimedia/Panama_Canal.jpg"
        )

    def test_wikimedia_replaces_spaces_with_underscores(self):
        assert (
            make_storage_key("WIKIMEDIA", "File:Has spaces.jpg") == "media/wikimedia/Has_spaces.jpg"
        )

    def test_loc_default_jpeg_extension_added(self):
        assert make_storage_key("LOC", "/item/2007660946/") == "media/loc/2007660946.jpg"

    def test_loc_png_content_type_overrides_extension(self):
        assert (
            make_storage_key("LOC", "/item/2007660946/", content_type="image/png")
            == "media/loc/2007660946.png"
        )

    def test_loc_existing_extension_preserved(self):
        # ".tif" already in the basename → no extension is added
        assert make_storage_key("LOC", "/item/abc.tif/") == "media/loc/abc.tif"

    def test_unknown_source_uses_pureposixpath_basename(self):
        assert make_storage_key("SMITHSONIAN", "some-id.png") == "media/smithsonian/some-id.png"


class _FakeStreamResponse:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    """Stand-in for the pooled httpx client whose .stream() yields a response."""

    def __init__(self, response):
        self._response = response

    @contextmanager
    def stream(self, *args, **kwargs):
        yield self._response


def _make_png(w: int = 8, h: int = 8) -> bytes:
    img = Image.new("RGB", (w, h), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestDownloadImage:
    """download_image: size guard + authoritative (decoded-bytes) content-type (#72)."""

    def _service(self, fake_client):
        # Bypass __init__ (boto3/settings); inject the fake pooled client.
        svc = ImageStorageService.__new__(ImageStorageService)
        svc._http = fake_client
        return svc

    def test_oversized_stream_raises_before_fully_buffering(self):
        # Two chunks that together exceed the cap; the guard must fire mid-stream
        # (before any decode is attempted).
        chunk = b"x" * (MAX_DOWNLOAD_SIZE // 2 + 1)
        svc = self._service(_FakeClient(_FakeStreamResponse([chunk, chunk])))

        with pytest.raises(ValueError, match="Image too large"):
            svc.download_image("https://example.test/huge.tif")

    def test_content_type_derived_from_decoded_bytes(self):
        png = _make_png()
        svc = self._service(_FakeClient(_FakeStreamResponse([png[:4], png[4:]])))

        data, content_type = svc.download_image("https://example.test/x")

        assert data == png
        # Type comes from the PNG bytes themselves, not any upstream header.
        assert content_type == "image/png"

    def test_rejects_non_raster_bytes(self):
        # An SVG (or any non-decodable / disallowed type) must be rejected so it
        # can never be served public-read from the CDN.
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
        svc = self._service(_FakeClient(_FakeStreamResponse([svg])))

        with pytest.raises(ValueError, match="not a decodable image"):
            svc.download_image("https://example.test/x.svg")


class TestWatermarkInfographic:
    """Covers watermark_infographic(image_bytes)."""

    def _make_white_png(self, w: int = 500, h: int = 500) -> bytes:
        from PIL import Image

        img = Image.new("RGB", (w, h), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_returns_png_of_same_dimensions(self):
        from PIL import Image

        original_bytes = self._make_white_png(500, 500)
        out_bytes = watermark_infographic(original_bytes)

        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.format == "PNG"
        assert out_img.size == (500, 500)

    def test_bytes_differ_from_input(self):
        original_bytes = self._make_white_png(500, 500)
        out_bytes = watermark_infographic(original_bytes)
        assert out_bytes != original_bytes


class TestStripNlmWatermark:
    """Covers strip_nlm_watermark(image_bytes) — #312."""

    # Mirrors the real NLM infographic export size and the corner-anchored box.
    NLM_W, NLM_H = 2752, 1536
    BOX_W, BOX_H = 212, 66
    BORDER = (250, 250, 250)  # NLM's near-white border

    def _make_nlm_png(self, w: int = NLM_W, h: int = NLM_H) -> bytes:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (w, h), self.BORDER)
        # Paint a dark "watermark" blob into the bottom-right box.
        draw = ImageDraw.Draw(img)
        draw.rectangle([w - self.BOX_W, h - self.BOX_H, w, h], fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_paints_over_watermark_with_border_color(self):
        from PIL import Image

        out = strip_nlm_watermark(self._make_nlm_png())
        img = Image.open(io.BytesIO(out)).convert("RGB")
        assert img.format == "PNG" or img.size == (self.NLM_W, self.NLM_H)

        # The corner that held the dark blob should now match the border color
        # (median sampling tolerates PNG round-trip drift).
        px = img.getpixel((self.NLM_W - 5, self.NLM_H - 5))
        assert all(abs(c - b) <= 3 for c, b in zip(px, self.BORDER))

    def test_preserves_dimensions(self):
        from PIL import Image

        out = strip_nlm_watermark(self._make_nlm_png())
        assert Image.open(io.BytesIO(out)).size == (self.NLM_W, self.NLM_H)

    def test_image_smaller_than_box_is_returned_unpainted(self):
        from PIL import Image

        # 100x100 < 212x66 box → guard skips painting, re-encodes as PNG.
        small = Image.new("RGB", (100, 100), (10, 20, 30))
        buf = io.BytesIO()
        small.save(buf, format="PNG")
        out = strip_nlm_watermark(buf.getvalue())

        img = Image.open(io.BytesIO(out)).convert("RGB")
        assert img.size == (100, 100)
        # Untouched: every pixel is still the original fill.
        assert img.getpixel((50, 50)) == (10, 20, 30)


class TestGenerateThumbnail:
    """Covers generate_thumbnail — narrowed catch and Sentry capture."""

    def _service(self):
        # Bypass __init__ (boto3/settings) — generate_thumbnail needs no instance state
        # beyond what Pillow and sentry_sdk provide.
        return ImageStorageService.__new__(ImageStorageService)

    def _make_valid_jpeg(self, w: int = 100, h: int = 100) -> bytes:
        from PIL import Image

        img = Image.new("RGB", (w, h), (128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_non_image_bytes_returns_none_and_captures_exception(self):
        """Non-image bytes raise UnidentifiedImageError — now caught, returns None."""
        svc = self._service()

        with (
            patch("app.services.image_storage.sentry_sdk.capture_exception") as mock_capture,
            patch("app.services.image_storage.sentry_sdk.add_breadcrumb"),
        ):
            result = svc.generate_thumbnail(b"not an image", "media/test/file.jpg")

        assert result is None
        assert mock_capture.call_count == 1
        # The exception passed to capture_exception must be an UnidentifiedImageError
        captured_exc = mock_capture.call_args[0][0]
        assert isinstance(captured_exc, UnidentifiedImageError)

    def test_unexpected_exception_propagates(self):
        """Exceptions outside the caught tuple must propagate — they signal a real bug."""
        svc = self._service()

        with patch("app.services.image_storage.Image.open", side_effect=KeyError("unexpected")):
            with pytest.raises(KeyError):
                svc.generate_thumbnail(b"some bytes", "media/test/file.jpg")

    def test_valid_image_returns_cdn_url(self):
        """A well-formed image produces a thumbnail URL via upload_image."""
        svc = self._service()
        image_bytes = self._make_valid_jpeg()
        fake_url = "https://cdn.example.com/media/test/thumbs/file.jpg"

        with patch.object(svc, "upload_image", return_value=fake_url) as mock_upload:
            result = svc.generate_thumbnail(image_bytes, "media/test/file.jpg")

        assert result == fake_url
        mock_upload.assert_called_once()


class TestWatermarkSlides:
    """Covers watermark_slides(pdf_bytes)."""

    def _make_single_page_pdf(self) -> bytes:
        # fitz is imported lazily inside watermark_slides; do the same here.
        import fitz

        doc = fitz.open()
        doc.new_page(width=612, height=792)  # standard letter, in points
        buf = io.BytesIO()
        doc.save(buf)
        doc.close()
        return buf.getvalue()

    def test_page_count_unchanged(self):
        import fitz

        original_bytes = self._make_single_page_pdf()
        out_bytes = watermark_slides(original_bytes)

        out_doc = fitz.open(stream=out_bytes, filetype="pdf")
        try:
            assert out_doc.page_count == 1
        finally:
            out_doc.close()

    def test_bytes_differ_from_input(self):
        original_bytes = self._make_single_page_pdf()
        out_bytes = watermark_slides(original_bytes)
        assert out_bytes != original_bytes
