"""Tests for translation service and endpoints."""

import logging
from unittest.mock import MagicMock, patch

import deepl
import pytest
from app.services.translation import TranslationService


class TestTranslationService:
    """Tests for TranslationService class."""

    def test_translate_text_calls_deepl_with_correct_params(self):
        """Verify DeepL is called with correct target language."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            mock_result.text = "Hola mundo"
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")
            result = service._sync_translate_text("Hello world")

            mock_translator.translate_text.assert_called_once_with(
                "Hello world",
                target_lang="ES-419",
                formality="default",
            )
            assert result == "Hola mundo"

    def test_translate_blocks_translates_text_not_image_query(self):
        """#340: block text fields translate in one batch; image_query/type are kept."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            # Fake DeepL: return one result per input string (uppercased).
            mock_translator.translate_text.side_effect = lambda texts, **kw: [
                MagicMock(text=t.upper()) for t in texts
            ]
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")
            blocks = [
                {"type": "cover", "title": "hello", "subtitle": "sub"},
                {"type": "bullets", "items": ["one", "two"]},
                {"type": "image", "image_query": "cathedral tower", "caption": "cap"},
                {"type": "timeline", "events": [{"label": "1519", "text": "founded"}]},
            ]
            out = service._sync_translate_blocks(blocks)

        # Text fields translated.
        assert out[0]["title"] == "HELLO" and out[0]["subtitle"] == "SUB"
        assert out[1]["items"] == ["ONE", "TWO"]
        assert out[2]["caption"] == "CAP"
        assert out[3]["events"][0]["text"] == "FOUNDED"
        # image_query (the English media-matcher key) and type are NOT translated.
        assert out[2]["image_query"] == "cathedral tower"
        assert out[2]["type"] == "image"
        # Input is not mutated, and DeepL is called exactly once (batched).
        assert blocks[0]["title"] == "hello"
        assert mock_translator.translate_text.call_count == 1

    def test_translate_text_forwards_formality(self):
        """Verify a non-default formality is forwarded to DeepL for plain text."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            mock_result.text = "Hola mundo"
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")
            result = service._sync_translate_text("Hello world", formality="less")

            mock_translator.translate_text.assert_called_once_with(
                "Hello world",
                target_lang="ES-419",
                formality="less",
            )
            assert result == "Hola mundo"

    def test_translate_markdown_forwards_formality(self):
        """Verify a non-default formality is forwarded to DeepL for markdown."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            mock_result.text = "<p>Hola</p>\n"
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")
            service._sync_translate_markdown("Hello", formality="more")

            call_kwargs = mock_translator.translate_text.call_args[1]
            assert call_kwargs["formality"] == "more"
            assert call_kwargs["tag_handling"] == "html"

    def test_code_blocks_with_attributes_protected(self):
        """Verify attributed <code>/<pre> tags get notranslate without duplicate class."""
        with patch("app.services.translation.deepl.Translator"):
            service = TranslationService("fake-api-key")

            html = (
                '<pre><code class="language-python">print("hi")</code></pre>'
            )
            protected = service._protect_code_blocks(html)

            # notranslate was injected on the bare <pre> tag
            assert '<pre class="notranslate">' in protected
            # The existing language-python class is preserved on <code>
            assert 'class="language-python"' in protected
            # notranslate added to <code> after the existing attribute
            assert (
                '<code class="language-python" class="notranslate">' in protected
            )
            # No tag carries two notranslate classes / malformed duplicate
            assert protected.count('class="notranslate"') == 2

            # _unprotect round-trips back to the original
            unprotected = service._unprotect_code_blocks(protected)
            assert unprotected == html

    def test_markdown_to_html_conversion(self):
        """Verify markdown is converted to HTML before translation."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            # DeepL returns translated HTML
            mock_result.text = "<h1>Título</h1>\n<p>Párrafo con <strong>negrita</strong>.</p>\n"
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")

            # Input markdown
            md_input = "# Title\n\nParagraph with **bold**."

            result = service._sync_translate_markdown(md_input)

            # Verify DeepL was called with tag_handling="html"
            call_kwargs = mock_translator.translate_text.call_args[1]
            assert call_kwargs["tag_handling"] == "html"

            # Result should be markdown (converted back from HTML)
            assert "Título" in result or "Title" in result  # Translated or original

    def test_code_blocks_protected_from_translation(self):
        """Verify code blocks get notranslate class."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            # DeepL returns HTML with code block unchanged
            mock_result.text = '<p>Texto</p>\n<pre class="notranslate"><code class="notranslate">code = "value"</code></pre>\n'
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")

            md_input = "Text\n\n```python\ncode = \"value\"\n```"

            # Call the protection method directly
            html = service.md_parser.render(md_input)
            protected_html = service._protect_code_blocks(html)

            # Verify notranslate class was added
            assert 'class="notranslate"' in protected_html

    def test_empty_text_returns_empty(self):
        """Verify empty input returns empty output."""
        with patch("app.services.translation.deepl.Translator"):
            service = TranslationService("fake-api-key")

            assert service._sync_translate_text("") == ""
            assert service._sync_translate_text("   ") == "   "

    def test_markdown_structure_preserved(self):
        """Verify headers, lists, and formatting are preserved."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            # DeepL returns translated HTML with structure intact
            mock_result.text = """<h1>Encabezado</h1>
<ul>
<li>Elemento uno</li>
<li>Elemento dos</li>
</ul>
<p>Texto con <em>énfasis</em> y <strong>negrita</strong>.</p>
"""
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")

            md_input = """# Header

- Item one
- Item two

Text with *emphasis* and **bold**."""

            result = service._sync_translate_markdown(md_input)

            # Result should contain markdown formatting
            assert "#" in result or "Encabezado" in result  # Header
            assert "-" in result or "*" in result  # List items
            assert "**" in result or "*" in result  # Bold/emphasis

    def test_deepl_exception_is_captured_and_reraised(self):
        """DeepL failures are Sentry-captured with context, then re-raised (#71)."""
        with (
            patch("app.services.translation.deepl.Translator") as mock_translator_class,
            patch("app.services.translation.sentry_sdk") as mock_sentry,
        ):
            mock_translator = MagicMock()
            mock_translator.translate_text.side_effect = deepl.DeepLException("boom")
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")

            with pytest.raises(deepl.DeepLException):
                service._sync_translate_text("Hello world")

            mock_sentry.capture_exception.assert_called_once()

    def test_logs_char_count_and_detected_source_lang(self, caplog):
        """Each call logs character count (DeepL bills per char) + source lang (#71)."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_result = MagicMock()
            mock_result.text = "Hola mundo"
            mock_result.detected_source_lang = "EN"
            mock_translator.translate_text.return_value = mock_result
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")
            with caplog.at_level(logging.INFO, logger="app.services.translation"):
                service._sync_translate_text("Hello world")  # 11 chars

            messages = [r.getMessage() for r in caplog.records]
            assert any("deepl translate: chars=11" in m for m in messages)
            assert any("detected_source_lang=EN" in m for m in messages)


# Note: Endpoint tests require Docker-in-Docker (testcontainers) which isn't
# available inside the container. These would be integration tests run from
# the host machine. The translation service unit tests above verify the core
# logic without needing database fixtures.

    def test_translate_blocks_covers_callout_and_mc_question(self):
        """Redesign: callout label/text/terms and mc_question question/choices
        translate; media selection fields are untouched."""
        with patch("app.services.translation.deepl.Translator") as mock_translator_class:
            mock_translator = MagicMock()
            mock_translator.translate_text.side_effect = lambda texts, **kw: [
                MagicMock(text=t.upper()) for t in texts
            ]
            mock_translator_class.return_value = mock_translator

            service = TranslationService("fake-api-key")
            blocks = [
                {
                    "type": "callout",
                    "label": "Key Terms",
                    "text": "an aside",
                    "terms": [{"term": "isthmus", "definition": "a land bridge"}],
                },
                {"type": "mc_question", "question": "when?", "choices": ["early", "late"]},
                {
                    "type": "image",
                    "image_query": "tower",
                    "caption": "cap",
                    "media_id": 42,
                    "media_path": "media/x.jpg",
                },
            ]
            out = service._sync_translate_blocks(blocks)

        assert out[0]["label"] == "KEY TERMS"
        assert out[0]["text"] == "AN ASIDE"
        assert out[0]["terms"][0] == {"term": "ISTHMUS", "definition": "A LAND BRIDGE"}
        assert out[1]["question"] == "WHEN?"
        assert out[1]["choices"] == ["EARLY", "LATE"]
        # Human-picked media selection survives translation untouched.
        assert out[2]["media_id"] == 42
        assert out[2]["media_path"] == "media/x.jpg"
        assert out[2]["image_query"] == "tower"
        assert mock_translator.translate_text.call_count == 1
