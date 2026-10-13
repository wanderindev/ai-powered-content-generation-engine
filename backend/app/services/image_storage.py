"""Image storage service — download external images and upload to DigitalOcean Spaces."""

import io
import logging
from pathlib import PurePosixPath

import boto3
import sentry_sdk
from botocore.config import Config as BotoConfig
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.core.config import get_settings
from app.services.http_client import make_hardened_client

logger = logging.getLogger(__name__)

_USER_AGENT = "PanamaInContext/1.0 (diego@panamaincontext.com)"

# Maximum image size to download (20 MB)
MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024

# Authoritative image content-types — derived from the *decoded* bytes (PIL
# format), never the upstream Content-Type header, which is attacker-controlled
# (#72). image/svg+xml is intentionally absent: an SVG served public-read from
# the CDN can carry <script>.
FORMAT_TO_CONTENT_TYPE = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
}

# Pillow's default decompression-bomb guard (~89M px) is below the largest
# legitimate scans we ingest. 300M px ≈ the 20 MB MAX_DOWNLOAD_SIZE cap × a
# pessimistic compression ratio — it bounds the in-memory decode to what the
# byte cap already permits, no more. Set once at import as a named module
# constant rather than as a side effect inside function bodies (#81).
MAX_IMAGE_PIXELS = 300_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageStorageService:
    """Upload and manage images on DigitalOcean Spaces (S3-compatible)."""

    def __init__(self):
        settings = get_settings()
        self.bucket = settings.do_spaces_bucket
        self.cdn_url = settings.do_spaces_cdn_url

        self.client = boto3.client(
            "s3",
            region_name=settings.do_spaces_region,
            endpoint_url=settings.do_spaces_endpoint,
            aws_access_key_id=settings.do_spaces_key,
            aws_secret_access_key=settings.do_spaces_secret,
            config=BotoConfig(signature_version="s3v4"),
        )

        # One pooled, SSRF-validating HTTP client per service instance, reused
        # across calls (no per-call TLS handshake). Close it via close() or use
        # the service as a context manager (#72/#73).
        self._http = make_hardened_client(headers={"User-Agent": _USER_AGENT})

    def __enter__(self) -> "ImageStorageService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close the pooled HTTP client."""
        self._http.close()

    @staticmethod
    def _authoritative_content_type(data: bytes) -> str:
        """Derive the content-type from the decoded image bytes.

        The upstream Content-Type header is ignored — it is attacker-controlled.
        Raises ValueError if the bytes are not a decodable, allowed raster image
        (notably rejecting SVG / non-raster types).
        """
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
        except (UnidentifiedImageError, OSError) as e:
            raise ValueError(f"Downloaded bytes are not a decodable image: {e}") from e

        content_type = FORMAT_TO_CONTENT_TYPE.get(fmt or "")
        if content_type is None:
            raise ValueError(f"Disallowed image format: {fmt!r}")
        return content_type

    def download_image(self, url: str) -> tuple[bytes, str]:
        """
        Download an image from an external URL through the SSRF-validating client.

        The returned content-type is derived authoritatively from the decoded
        image bytes (PIL format), never the upstream Content-Type header (#72).

        Returns (image_bytes, content_type).
        Raises ValueError if the download fails, exceeds the size limit, or the
        bytes are not an allowed raster image.
        """
        with self._http.stream("GET", url) as response:
            response.raise_for_status()

            # Stream chunk-by-chunk and abort as soon as the cap is
            # exceeded, so an oversized body is never fully buffered.
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_DOWNLOAD_SIZE:
                    raise ValueError(f"Image too large: at least {len(chunks)} bytes")

        data = bytes(chunks)
        content_type = self._authoritative_content_type(data)
        return data, content_type

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str,
    ) -> str:
        """
        Upload arbitrary bytes to DO Spaces, public-read.

        The generic primitive behind ``upload_image`` — also used for non-image
        artifacts (e.g. slide-narration MP3s, #322).

        Args:
            data: Raw object bytes
            key: Object key (path in the bucket), e.g. "materials/slides/audio/1/01.mp3"
            content_type: MIME type, e.g. "audio/mpeg"

        Returns:
            Public CDN URL for the uploaded object.
        """
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=io.BytesIO(data),
            ContentType=content_type,
            ACL="public-read",
        )

        return f"{self.cdn_url}/{key}"

    def upload_image(
        self,
        image_bytes: bytes,
        key: str,
        content_type: str = "image/jpeg",
    ) -> str:
        """
        Upload image bytes to DO Spaces.

        Args:
            image_bytes: Raw image data
            key: Object key (path in the bucket), e.g. "media/loc/2007660946.jpg"
            content_type: MIME type

        Returns:
            Public CDN URL for the uploaded image.
        """
        return self.upload_bytes(image_bytes, key, content_type)

    def delete_image(self, key: str) -> None:
        """Delete an image from DO Spaces."""
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def download_bytes(self, key: str) -> bytes:
        """Fetch an object's raw bytes from our Spaces bucket by key.

        The complement of ``upload_image`` — used by the video stage (#323) to
        pull our own rendered frames / narration MP3s back for muxing. Unlike
        ``download_image`` (which fetches an *external* URL through the
        SSRF-guarded HTTP client and validates raster bytes), this reads a known
        in-bucket object directly via the S3 API, so it is format-agnostic.
        """
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def generate_thumbnail(
        self,
        image_bytes: bytes,
        key: str,
        max_width: int = 300,
    ) -> str | None:
        """
        Generate a thumbnail from image bytes and upload to Spaces.

        Args:
            image_bytes: Original image data
            key: Original object key — thumbnail is stored under thumbs/ prefix
            max_width: Maximum thumbnail width in pixels

        Returns:
            CDN URL for the thumbnail, or None if generation fails.
        """
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img = img.convert("RGB")

            # Resize maintaining aspect ratio
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            thumb_bytes = buf.getvalue()

            # Build thumbnail key: media/wikimedia/file.jpg -> media/wikimedia/thumbs/file.jpg
            parts = key.rsplit("/", 1)
            if len(parts) == 2:
                thumb_key = f"{parts[0]}/thumbs/{parts[1].rsplit('.', 1)[0]}.jpg"
            else:
                thumb_key = f"thumbs/{parts[0].rsplit('.', 1)[0]}.jpg"

            return self.upload_image(thumb_bytes, thumb_key, "image/jpeg")
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
            # Pillow content failures — swallow and return None so the caller
            # contract is preserved. Any other exception propagates — it signals
            # a real bug, not a content-related failure.
            logger.warning("Thumbnail generation failed for %s: %s", key, e)
            sentry_sdk.add_breadcrumb(
                category="image_storage",
                message="thumbnail generation failed",
                level="warning",
                data={"storage_key": key, "image_size_bytes": len(image_bytes)},
            )
            sentry_sdk.capture_exception(e)
            return None

    def download_and_upload(
        self,
        source_url: str,
        key: str,
    ) -> tuple[str, int, str, str | None]:
        """
        Download an external image and re-upload to DO Spaces.

        Args:
            source_url: External image URL to download
            key: Target key in Spaces

        Returns:
            (cdn_url, file_size, content_type, thumbnail_url)
        """
        # content_type is derived authoritatively from the decoded bytes and is
        # always one of FORMAT_TO_CONTENT_TYPE — no header-guessing fallback
        # needed (the upstream header is never trusted now, #72).
        image_bytes, content_type = self.download_image(source_url)

        cdn_url = self.upload_image(image_bytes, key, content_type)
        thumbnail_url = self.generate_thumbnail(image_bytes, key)

        return cdn_url, len(image_bytes), content_type, thumbnail_url


# NotebookLM stamps a "Made with NotebookLM" watermark flush against the
# bottom-right corner of every infographic it exports (export size is a fixed
# 2752x1536; the mark occupies a ~212x66 px box pinned to that corner). We erase
# it by sampling the surrounding border color and painting the box with it, then
# PIC's own tag goes on top (see watermark_infographic). The box is anchored to
# the corner rather than absolute pixels so it stays correct if a future NLM
# export differs in size — the mark is a fixed-size logo, always in the corner.
_NLM_WM_BOX_W = 212
_NLM_WM_BOX_H = 66


def _sample_border_color(img: Image.Image) -> tuple[int, int, int]:
    """Median RGB of five border/corner points — the NLM infographic border color."""
    w, h = img.size
    points = [(5, h - 5), (5, 5), (w // 2, 5), (w // 2, h - 5), (w // 4, h - 5)]
    samples = [img.getpixel(p)[:3] for p in points]
    return tuple(sorted(s[i] for s in samples)[len(samples) // 2] for i in range(3))


def strip_nlm_watermark(image_bytes: bytes) -> bytes:
    """Erase the NotebookLM watermark from a generated infographic (#312).

    Samples the infographic's border color and paints over the fixed
    bottom-right watermark box with it, returning a clean PNG. Running it on an
    already-clean image simply repaints the border color (harmless). Returns the
    image unchanged (re-encoded as PNG) when it is smaller than the watermark box
    — a defensive guard against an unexpected non-NLM upload.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    if w < _NLM_WM_BOX_W or h < _NLM_WM_BOX_H:
        logger.warning("Infographic %dx%d smaller than NLM watermark box; skipping strip", w, h)
    else:
        border_color = _sample_border_color(img)
        draw = ImageDraw.Draw(img)
        draw.rectangle([w - _NLM_WM_BOX_W, h - _NLM_WM_BOX_H, w, h], fill=border_color)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def watermark_infographic(image_bytes: bytes) -> bytes:
    """Add a 'panamaincontext.com' tag to the bottom-right of an infographic.

    Tag: 200x25 px, background #e8f6fc, border/text #1095d3.
    Positioned 210px from right edge, 35px from bottom edge (10px margin).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    tag_w, tag_h = 200, 25
    bg_color = (232, 246, 252, 255)  # #e8f6fc
    fg_color = (16, 149, 211, 255)  # #1095d3

    tag = Image.new("RGBA", (tag_w, tag_h), bg_color)
    draw = ImageDraw.Draw(tag)

    # 1px border
    draw.rectangle([0, 0, tag_w - 1, tag_h - 1], outline=fg_color)

    # Load DejaVu Sans — renders well at small sizes
    font_size = 14
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    text = "panamaincontext.com"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = (tag_w - text_w) // 2
    text_y = (tag_h - text_h) // 2 - bbox[1]  # offset by top bearing
    draw.text((text_x, text_y), text, fill=fg_color, font=font)

    # Paste tag onto image
    x = img.width - 210
    y = img.height - 35
    img.paste(tag, (x, y), tag)

    # Save as PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def watermark_slides(pdf_bytes: bytes) -> bytes:
    """Add a 'panamaincontext.com' tag to every page of a slides PDF.

    Uses PyMuPDF to draw a vector overlay directly on each page.
    Tag: 200x25 pts, background #e8f6fc, border/text #1095d3.
    Positioned 210pts from right edge, 35pts from bottom edge.
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    tag_w, tag_h = 200, 25
    bg_color = (232 / 255, 246 / 255, 252 / 255)  # #e8f6fc
    fg_color = (16 / 255, 149 / 255, 211 / 255)  # #1095d3
    font = fitz.Font("helv")
    text = "panamaincontext.com"
    font_size = 13

    text_width = font.text_length(text, fontsize=font_size)
    ascender = font.ascender * font_size
    descender = font.descender * font_size
    text_height = ascender - descender

    for page in doc:
        rect = page.rect
        x = rect.width - 210
        y = rect.height - 35
        tag_rect = fitz.Rect(x, y, x + tag_w, y + tag_h)

        # Draw filled rectangle with border
        shape = page.new_shape()
        shape.draw_rect(tag_rect)
        shape.finish(color=fg_color, fill=bg_color, width=0.75)
        shape.commit()

        # Center text in tag
        text_x = x + (tag_w - text_width) / 2
        text_y = y + (tag_h - text_height) / 2 + ascender

        tw = fitz.TextWriter(page.rect)
        tw.append((text_x, text_y), text, font=font, fontsize=font_size)
        tw.write_text(page, color=fg_color)

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_storage_key(source: str, external_id: str, content_type: str = "image/jpeg") -> str:
    """
    Build a clean Spaces object key from source and external ID.

    Examples:
        WIKIMEDIA, "File:Panama_Canal.jpg" -> "media/wikimedia/Panama_Canal.jpg"
        LOC, "/item/2007660946" -> "media/loc/2007660946.jpg"
    """
    source_dir = source.lower()

    # Clean the external ID into a filename
    if source == "WIKIMEDIA":
        filename = external_id.removeprefix("File:").replace(" ", "_")
    elif source == "LOC":
        # Extract the last path segment as filename
        parts = external_id.rstrip("/").split("/")
        base = parts[-1] if parts else external_id
        # Add extension if missing
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/tiff": ".tif",
            "image/gif": ".gif",
        }
        has_ext = any(
            base.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif")
        )
        if not has_ext:
            ext = ext_map.get(content_type, ".jpg")
            base = f"{base}{ext}"
        filename = base
    else:
        filename = PurePosixPath(external_id).name or external_id

    return f"media/{source_dir}/{filename}"
