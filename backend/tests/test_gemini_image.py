"""Tests for the Gemini feature-image service (#419).

Every test mocks ``httpx.Client`` — no request leaves the container — and pins
``gemini_api_key`` on the ``lru_cache``d settings singleton so the ambient value
baked into the dev ``.env`` can never decide the outcome.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.core.config import get_settings
from app.services.gemini_image import API_URL, GeminiImageError, generate_feature_image
from PIL import Image


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-gemini-key")
    return "test-gemini-key"


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (16, 9), (10, 20, 30)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _interaction(blocks: list[dict]) -> dict:
    return {"steps": [{"type": "model_output", "content": blocks}]}


def _mock_client(response=None, side_effect=None):
    """Patch ``httpx.Client`` so ``post`` returns *response* (or raises)."""
    client = MagicMock()
    if side_effect is not None:
        client.post.side_effect = side_effect
    else:
        client.post.return_value = response
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return patch("httpx.Client", return_value=ctx), client


def _response(status_code: int, json_body=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_body is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = json_body
    return resp


def test_success_returns_jpeg_and_pins_the_request(gemini_key):
    payload = _interaction([{"type": "image", "mime_type": "image/png", "data": _png_b64()}])
    patcher, client = _mock_client(_response(200, payload))
    with patcher:
        result = generate_feature_image("a watercolor of the canal")

    with Image.open(io.BytesIO(result)) as img:
        assert img.format == "JPEG"

    url = client.post.call_args.args[0]
    kwargs = client.post.call_args.kwargs
    assert url == API_URL
    assert kwargs["headers"]["x-goog-api-key"] == gemini_key
    body = kwargs["json"]
    assert body["model"] == "gemini-3.1-flash-image"
    assert body["store"] is False
    assert body["response_format"]["aspect_ratio"] == "16:9"
    assert body["input"] == [{"type": "text", "text": "a watercolor of the canal"}]


def test_no_image_block_raises_502_with_the_model_text(gemini_key):
    payload = _interaction([{"type": "text", "text": "I cannot draw that."}])
    patcher, _ = _mock_client(_response(200, payload))
    with patcher, pytest.raises(GeminiImageError) as exc:
        generate_feature_image("prompt")
    assert exc.value.status_code == 502
    assert "cannot draw" in str(exc.value)


def test_quota_error_maps_to_429(gemini_key):
    patcher, _ = _mock_client(_response(429, {"error": "quota"}, text="RESOURCE_EXHAUSTED"))
    with patcher, pytest.raises(GeminiImageError) as exc:
        generate_feature_image("prompt")
    assert exc.value.status_code == 429


def test_other_http_error_maps_to_502(gemini_key):
    patcher, _ = _mock_client(_response(400, {"error": "bad"}, text="bad request"))
    with patcher, pytest.raises(GeminiImageError) as exc:
        generate_feature_image("prompt")
    assert exc.value.status_code == 502
    assert "400" in str(exc.value)


def test_timeout_maps_to_502(gemini_key):
    patcher, _ = _mock_client(side_effect=httpx.TimeoutException("too slow"))
    with patcher, pytest.raises(GeminiImageError) as exc:
        generate_feature_image("prompt")
    assert exc.value.status_code == 502
    assert "timed out" in str(exc.value)


def test_transport_error_maps_to_502(gemini_key):
    patcher, _ = _mock_client(side_effect=httpx.ConnectError("no route"))
    with patcher, pytest.raises(GeminiImageError) as exc:
        generate_feature_image("prompt")
    assert exc.value.status_code == 502


def test_missing_key_raises_before_any_request(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", "")
    patcher, client = _mock_client(_response(200, _interaction([])))
    with patcher, pytest.raises(GeminiImageError) as exc:
        generate_feature_image("prompt")
    assert exc.value.status_code == 503
    client.post.assert_not_called()
