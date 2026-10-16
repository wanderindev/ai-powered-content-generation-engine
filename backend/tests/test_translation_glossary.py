"""#633: the EN→ES DeepL glossary is opt-in; off leaves every SDK call unchanged."""

import asyncio
import logging
from unittest.mock import MagicMock, patch

from app.services.translation import TranslationService


def _drive(service_kwargs: dict):
    """Run all three entry points against a fake SDK; return (calls, results)."""
    with patch("app.services.translation.deepl.Translator") as mock_translator_class:
        sdk = mock_translator_class.return_value

        def fake(text, **kw):
            if isinstance(text, list):
                return [MagicMock(text=f"{t} 1.500") for t in text]
            return MagicMock(text="<p>Hola 1.500</p>" if kw.get("tag_handling") else "Hola")

        sdk.translate_text.side_effect = fake
        service = TranslationService("fake-api-key", **service_kwargs)

        async def run():
            return (
                await service.translate_text("Hello"),
                await service.translate_markdown("Hello"),
                await service.translate_blocks(
                    [{"type": "paragraph", "text": "a"}, {"type": "bullets", "items": ["b"]}]
                ),
            )

        results = asyncio.run(run())
    return sdk.translate_text.call_args_list, results


PRE_GLOSSARY_CALLS = [
    (("Hello",), {"target_lang": "ES-419", "formality": "default"}),
    (
        ("<p>Hello</p>\n",),
        {"target_lang": "ES-419", "formality": "default", "tag_handling": "html"},
    ),
    ((["a", "b"],), {"target_lang": "ES-419", "formality": "default"}),
]


def test_off_path_sdk_calls_are_exactly_the_pre_glossary_ones():
    for kwargs in ({}, {"glossary_enabled": False, "glossary_id": "gid-1"}):
        calls, _ = _drive(kwargs)
        assert [(c.args, c.kwargs) for c in calls] == PRE_GLOSSARY_CALLS


def test_on_path_passes_glossary_and_source_lang_on_every_entry_point():
    calls, results = _drive({"glossary_enabled": True, "glossary_id": "gid-1"})
    assert len(calls) == 3  # translate_blocks is still one batched call
    for c in calls:
        assert c.kwargs["glossary"] == "gid-1"
        assert c.kwargs["source_lang"] == "EN"
        assert c.kwargs["target_lang"] == "ES-419"  # dialect unchanged
    assert calls[2].args == (["a", "b"],)
    assert calls[1].kwargs["tag_handling"] == "html"
    # _normalize (ES thousands, #531) still runs on every path.
    text, markdown, blocks = results
    assert text == "Hola"
    assert markdown == "Hola 1,500"
    assert blocks[0]["text"] == "a 1,500" and blocks[1]["items"] == ["b 1,500"]


def test_enabled_without_id_degrades_to_off_with_one_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.translation"):
        calls, _ = _drive({"glossary_enabled": True, "glossary_id": "  "})
    assert [(c.args, c.kwargs) for c in calls] == PRE_GLOSSARY_CALLS
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "DEEPL_GLOSSARY_ID" in warnings[0].getMessage()


def test_log_line_names_the_glossary(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.translation"):
        _drive({"glossary_enabled": True, "glossary_id": "gid-1"})
    messages = [r.getMessage() for r in caplog.records]
    assert any("chars=5" in m and "glossary=gid-1" in m for m in messages)
    assert any("translate batch" in m and "glossary=gid-1" in m for m in messages)


def test_get_translation_service_reads_settings(monkeypatch):
    from app.services import translation

    settings = MagicMock(deepl_api="key", deepl_glossary_enabled=True, deepl_glossary_id="gid-9")
    monkeypatch.setattr(translation, "get_settings", lambda: settings)
    translation.get_translation_service.cache_clear()
    try:
        with patch("app.services.translation.deepl.Translator"):
            service = translation.get_translation_service()
        assert service.glossary_id == "gid-9"
    finally:
        translation.get_translation_service.cache_clear()


def test_settings_default_off(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("DEEPL_GLOSSARY_ENABLED", raising=False)
    monkeypatch.delenv("DEEPL_GLOSSARY_ID", raising=False)
    s = Settings(_env_file=None)
    assert s.deepl_glossary_enabled is False and s.deepl_glossary_id == ""
