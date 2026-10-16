"""Feature-image generation via Google's Gemini image API (#419).

This replaces the manual gemini.google.com step: the tuned watercolor prompt
(``app/services/image_prompt.py``) goes straight to the API and comes back as
JPEG bytes the caller stores under a fresh timestamped filename.

Why plain ``httpx`` and not the hardened client in ``services/http_client.py``:
that transport exists to pin DNS and block SSRF when crawling *semi-trusted*
media hosts. A single fixed Google endpoint needs none of it. And why no SDK:
``google-genai`` would add a dependency (and a container rebuild) for one POST.

No retry layer here on purpose — the weekly driver's ``agent_task_max_attempts``
ceiling is the retry, and an image call is expensive enough that a silent
in-process retry is the wrong default.
"""

import base64
import io
import logging

import httpx
from PIL import Image

from app.core.config import get_settings

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = "gemini-3.1-flash-image"
ASPECT_RATIO = "16:9"
IMAGE_SIZE = "1K"
# Image generation routinely takes 10-40s; 120s is headroom, not an expectation.
TIMEOUT_SECONDS = 120.0
JPEG_QUALITY = 90
_ERROR_EXCERPT_CHARS = 200


class GeminiImageError(RuntimeError):
    """An image-generation attempt failed; the message is operator-facing.

    ``status_code`` is the HTTP status the API layer should surface (502 for a
    generic upstream failure, 429 when Google reports a quota problem).
    """

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _excerpt(text: str) -> str:
    text = (text or "").strip()
    return text[:_ERROR_EXCERPT_CHARS]


def _extract_image(payload: dict) -> tuple[bytes, str] | None:
    """Pull the first image block out of an Interaction response."""
    for step in payload.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
                return base64.b64decode(block["data"]), block.get("mime_type", "")
    return None


def _extract_text(payload: dict) -> str:
    """Any text the model produced — the useful part of a no-image response."""
    parts: list[str] = []
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
    return " ".join(parts)


def _to_jpeg(raw: bytes) -> bytes:
    """Normalise whatever Gemini returned to JPEG (RGB, quality 90)."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
            return buf.getvalue()
    except Exception as exc:  # pragma: no cover - defensive
        raise GeminiImageError(f"Gemini returned an unreadable image: {exc}") from exc


def generate_feature_image(prompt: str) -> bytes:
    """Generate a 16:9 feature image for *prompt* and return JPEG bytes.

    Raises ``GeminiImageError`` on any failure (not configured, upstream error,
    quota, timeout, or a response carrying no image).
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiImageError("Gemini API key is not configured", status_code=503)

    body = {
        "model": MODEL,
        "input": [{"type": "text", "text": prompt}],
        # Opt out of Google-side retention (the API default is to store).
        "store": False,
        "response_format": {
            "type": "image",
            "aspect_ratio": ASPECT_RATIO,
            "image_size": IMAGE_SIZE,
        },
    }

    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.post(
                API_URL,
                headers={
                    "x-goog-api-key": settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise GeminiImageError(f"Gemini image request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise GeminiImageError(f"Gemini image request failed: {exc}") from exc

    if response.status_code == 429:
        raise GeminiImageError(
            f"Gemini image quota exceeded: {_excerpt(response.text)}", status_code=429
        )
    if response.status_code >= 300:
        raise GeminiImageError(
            f"Gemini image API returned {response.status_code}: {_excerpt(response.text)}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise GeminiImageError(f"Gemini returned a non-JSON response: {exc}") from exc

    found = _extract_image(payload if isinstance(payload, dict) else {})
    if found is None:
        text = _excerpt(_extract_text(payload if isinstance(payload, dict) else {}))
        detail = f" Model said: {text}" if text else ""
        raise GeminiImageError(f"Gemini returned no image.{detail}")

    raw, mime_type = found
    jpeg = _to_jpeg(raw)
    logger.info(
        "Gemini feature image generated (mime=%s, raw=%d bytes, jpeg=%d bytes)",
        mime_type or "unknown",
        len(raw),
        len(jpeg),
    )
    return jpeg
