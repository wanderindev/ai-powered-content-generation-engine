"""Tests for Instagram post HTML assembly and JPEG rendering."""

import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.models.instagram_post import InstagramPost
from app.services.ig_html import build_ig_html, sanitize_fact
from app.services.ig_render import render_instagram_post
from PIL import Image


def _post(**kwargs):
    defaults = {
        "id": 7,
        "post_type": "SABIAS_QUE",
        "template_variant": "AZUL",
        "title": "Arco Chato",
        "fact": sanitize_fact("el <b>Arco Chato</b> resistió tres siglos?"),
        "polaroid_caption": "El Arco Chato, Casco Antiguo",
        "media_path": "https://cdn.example.com/media/arco.jpg",
        "image_url": None,
    }
    defaults.update(kwargs)
    post = InstagramPost()
    for key, value in defaults.items():
        setattr(post, key, value)
    return post


class TestBuildIgHtml:
    def test_azul_variant_full_card(self):
        html = build_ig_html(_post())
        assert "¿Sabías que" in html
        assert "<b>Arco Chato</b> resistió" in html
        assert "https://cdn.example.com/media/arco.jpg" in html
        assert "El Arco Chato, Casco Antiguo" in html
        assert "rotate(-5deg)" in html  # azul polaroid tilt
        assert "data:font/woff2;base64," in html  # bundled Caveat, no Google fetch
        assert "fonts.googleapis.com" not in html
        assert "--accent-500" in html  # zip token file with the orange family

    def test_claro_variant_selected(self):
        html = build_ig_html(_post(template_variant="CLARO"))
        assert "sepia(0.5)" in html
        assert "rotate(4deg)" in html

    def test_no_photo_drops_polaroid(self):
        html = build_ig_html(_post(media_path=None))
        assert 'class="polaroid"' not in html  # .polaroid CSS rules still bundled

    def test_fact_markup_is_sanitized_defensively(self):
        # Even if a raw string reaches the model column, only <b> survives.
        html = build_ig_html(_post(fact="<script>x</script> el <b>bueno</b>"))
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x" in html
        assert "<b>bueno</b>" in html

    def test_polaroid_caption_is_escaped(self):
        html = build_ig_html(_post(polaroid_caption='<img src=x onerror=1>'))
        assert "<img src=x" not in html

    def test_promo_template(self):
        html = build_ig_html(
            _post(
                post_type="ARTICLE_PROMO",
                template_variant=None,
                title="Los Tres Panamás",
                kicker="Serie · 3 artículos",
                meta_text="por Diego Raúl Feliu · 3 artículos",
                media_path="https://cdn.example.com/media/feature.jpg",
            )
        )
        assert "Nuevo en el blog" in html
        assert "Los Tres Panamás" in html
        assert "Serie · 3 artículos" in html
        assert "Léelo ahora — enlace en la bio" in html
        assert "data:image/avif;base64," in html  # Diego avatar bundled

    def test_promo_without_background_uses_brand_gradient(self):
        html = build_ig_html(_post(post_type="ARTICLE_PROMO", media_path=None))
        assert "radial-gradient" in html


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 5), "blue").save(buf, "PNG")
    return buf.getvalue()


@contextmanager
def _fake_page():
    yield MagicMock()


class TestRenderInstagramPost:
    @patch("app.services.ig_render.ImageStorageService")
    @patch("app.services.ig_render._screenshot", return_value=_tiny_png())
    @patch("app.services.ig_render._chromium_page", _fake_page)
    def test_uploads_jpeg_and_sets_image_url(self, _shot, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.cdn_url = "https://cdn.example.com"
        storage.upload_bytes.side_effect = lambda data, key, ct: f"https://cdn.example.com/{key}"

        post = _post()
        url = render_instagram_post(post)

        data, key, content_type = storage.upload_bytes.call_args.args
        assert content_type == "image/jpeg"
        assert data[:3] == b"\xff\xd8\xff"  # JPEG magic — PNG was converted
        assert key.startswith("instagram/7/render-")
        assert key.endswith(".jpg")
        assert post.image_url == url
        storage.close.assert_called_once()

    @patch("app.services.ig_render.ImageStorageService")
    @patch("app.services.ig_render._screenshot", return_value=_tiny_png())
    @patch("app.services.ig_render._chromium_page", _fake_page)
    def test_rerender_deletes_previous_artifact(self, _shot, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.cdn_url = "https://cdn.example.com"
        storage.upload_bytes.side_effect = lambda data, key, ct: f"https://cdn.example.com/{key}"

        post = _post(image_url="https://cdn.example.com/instagram/7/render-100.jpg")
        render_instagram_post(post)

        storage.delete_image.assert_called_once_with("instagram/7/render-100.jpg")

    @patch("app.services.ig_render.ImageStorageService")
    @patch("app.services.ig_render._screenshot", return_value=_tiny_png())
    @patch("app.services.ig_render._chromium_page", _fake_page)
    def test_foreign_previous_url_is_left_alone(self, _shot, mock_storage_cls):
        storage = mock_storage_cls.return_value
        storage.cdn_url = "https://cdn.example.com"
        storage.upload_bytes.side_effect = lambda data, key, ct: f"https://cdn.example.com/{key}"

        post = _post(image_url="https://elsewhere.example.com/old.jpg")
        render_instagram_post(post)

        storage.delete_image.assert_not_called()
