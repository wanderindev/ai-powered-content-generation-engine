"""
Translation service using DeepL API.

Provides translation for:
- Plain text (titles, excerpts)
- Markdown content using Round-Trip Middleware (MD → HTML → DeepL → HTML → MD)

Observability (issue #71): every DeepL call is logged with its character count
(DeepL bills per character) and detected source language, and failures are
Sentry-captured with context before re-raising. No retry/timeout layer is added
here — the deepl SDK already retries transient failures (429/5xx) with
exponential backoff and manages connection timeouts internally, so a wrapper
retry would only double-retry. ``formality`` is a per-call override.

Glossary (#633): with ``deepl_glossary_enabled`` on and ``deepl_glossary_id``
set, every DeepL call (all three entry points) also passes ``glossary=<id>``
and an explicit ``source_lang="EN"`` — the SDK refuses a glossary without a
source language. DeepL glossaries are not regional: one created EN→ES (the
SDK strips ``ES-419`` to ``ES`` at creation) applies to an ``ES-419`` target,
so the target dialect is unchanged. Off — or on with no id, which logs one
warning — the SDK receives exactly the pre-#633 kwargs.
"""

import asyncio
import copy
import logging
import re
from functools import lru_cache
from typing import Literal

import deepl
import sentry_sdk
from markdown_it import MarkdownIt
from markdownify import markdownify as md

from app.core.config import get_settings

logger = logging.getLogger(__name__)

Formality = Literal["default", "more", "less", "prefer_more", "prefer_less"]

# The glossary is keyed EN→ES; DeepL requires an explicit source with a glossary.
GLOSSARY_SOURCE_LANG = "EN"

# --- Spanish thousands separator (#531) ----------------------------------------
# DeepL (ES-419) writes thousands inconsistently: "1.500" in some articles,
# "1,500" in others, sometimes both in one. Panama uses the comma form, matching
# the English source, and DeepL has no separator setting for ES-419, so the
# output is normalised here. A dot counts as a thousands separator only between
# a 1-3 digit group that does not start with 0 and exactly three digits, not
# followed by another digit or a ",d"/".d" continuation — so decimals (3.5,
# 2.75, 0.125), four-digit years, dates (12.05.1903) and European decimals
# (1.500,75) are left alone. Chains convert whole: 1.234.567 -> 1,234,567.
_ES_THOUSANDS_RE = re.compile(r"(?<![\d.,])[1-9]\d{0,2}(?:\.\d{3})+(?!\d|[.,]\d)")
# Never rewrite inside code or a URL.
_ES_THOUSANDS_SHIELD_RE = re.compile(
    r"```.*?```|~~~.*?~~~|(`+)[^`]*?\1|\]\([^)]*\)|<[^>\s]+://[^>\s]*>|https?://[^\s)\]>]+"
    r"|<(code|pre)\b.*?</\2>",
    re.DOTALL,
)


def normalize_es_thousands(text: str) -> str:
    """Rewrite dot-form thousands (``1.500``) to the comma form (``1,500``).

    Idempotent; comma-form numbers, decimals and anything inside code spans,
    fenced code, ``<code>``/``<pre>`` or URLs are untouched. Shared by
    ``TranslationService`` and ``scripts.normalize_es_thousands``.
    """
    if not text:
        return text
    out: list[str] = []
    last = 0
    for shield in _ES_THOUSANDS_SHIELD_RE.finditer(text):
        out.append(
            _ES_THOUSANDS_RE.sub(
                lambda m: m.group(0).replace(".", ","), text[last : shield.start()]
            )
        )
        out.append(shield.group(0))
        last = shield.end()
    out.append(_ES_THOUSANDS_RE.sub(lambda m: m.group(0).replace(".", ","), text[last:]))
    return "".join(out)


class TranslationService:
    """Service for translating content using DeepL API."""

    def __init__(self, api_key: str, *, glossary_enabled: bool = False, glossary_id: str = ""):
        """Initialize with DeepL API key and the optional EN→ES glossary (#633)."""
        self.translator = deepl.Translator(api_key)
        self.target_lang = "ES-419"  # Latin American Spanish
        self.md_parser = MarkdownIt("commonmark", {"html": True})
        self.glossary_id: str | None = None
        if glossary_enabled:
            if glossary_id and glossary_id.strip():
                self.glossary_id = glossary_id.strip()
            else:
                # Empty credentials disable a feature, they don't crash it.
                logger.warning(
                    "deepl glossary enabled but DEEPL_GLOSSARY_ID is empty; "
                    "translating without a glossary"
                )

    def _glossary_kwargs(self) -> dict:
        """Extra SDK kwargs for the glossary; empty (no change at all) when off."""
        if not self.glossary_id:
            return {}
        return {"source_lang": GLOSSARY_SOURCE_LANG, "glossary": self.glossary_id}

    async def translate_text(self, text: str, formality: Formality = "default") -> str:
        """
        Translate plain text (titles, excerpts).

        Uses run_in_executor for non-blocking async operation.

        Args:
            text: Plain text to translate
            formality: DeepL formality level (e.g. "more" for formal usted,
                "less" for informal tú). Defaults to "default".
        """
        if not text or not text.strip():
            return text

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._sync_translate_text, text, formality)
        return self._normalize(result)

    async def translate_markdown(
        self, markdown_content: str, formality: Formality = "default"
    ) -> str:
        """
        Translate markdown using Round-Trip Middleware.

        Flow:
        1. Convert Markdown → HTML
        2. Protect code blocks with notranslate class
        3. Send to DeepL with tag_handling="html"
        4. Convert HTML → Markdown

        Args:
            markdown_content: Markdown text to translate
            formality: DeepL formality level (e.g. "more" for formal usted,
                "less" for informal tú). Defaults to "default".

        Returns:
            Translated markdown text
        """
        if not markdown_content or not markdown_content.strip():
            return markdown_content

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._sync_translate_markdown, markdown_content, formality
        )
        return self._normalize(result)

    async def translate_blocks(
        self, blocks: list[dict], formality: Formality = "default"
    ) -> list[dict]:
        """Translate the human-text fields of structured guide blocks (#340).

        Translates titles, paragraphs, list items, quotes, captions, etc. in a
        single batched DeepL call; never translates ``image_query`` (the English
        media-matcher key) or ``type``. Returns a new block list — the input is
        not mutated.
        """
        if not blocks:
            return blocks

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_translate_blocks, blocks, formality)

    def _translate(self, text: str, **kwargs) -> deepl.TextResult:
        """Call DeepL ``translate_text`` with usage logging + error observability.

        Logs the character count (DeepL bills per character) and detected source
        language on success; logs + Sentry-captures DeepL failures with context
        before re-raising (so admin routes still surface the error). No retry or
        timeout is layered here — the deepl SDK handles both internally.
        """
        try:
            result = self.translator.translate_text(
                text, target_lang=self.target_lang, **kwargs, **self._glossary_kwargs()
            )
        except deepl.DeepLException as e:
            logger.error("deepl translate failed: chars=%d error=%s", len(text), e)
            sentry_sdk.capture_exception(e)
            raise
        logger.info(
            "deepl translate: chars=%d detected_source_lang=%s target_lang=%s formality=%s"
            " glossary=%s",
            len(text),
            getattr(result, "detected_source_lang", "?"),
            self.target_lang,
            kwargs.get("formality", "default"),
            self.glossary_id or "off",
        )
        return result

    def _sync_translate_text(self, text: str, formality: Formality = "default") -> str:
        """Synchronous DeepL text translation."""
        if not text or not text.strip():
            return text

        return self._translate(text, formality=formality).text

    def _sync_translate_markdown(
        self, markdown_content: str, formality: Formality = "default"
    ) -> str:
        """
        Synchronous markdown translation using Round-Trip Middleware.

        This handles the MD → HTML → DeepL → HTML → MD pipeline.
        """
        # Step 1: Convert Markdown to HTML
        html = self.md_parser.render(markdown_content)

        # Step 2: Protect code blocks from translation
        html = self._protect_code_blocks(html)

        # Step 3: Translate HTML with DeepL
        translated_html = self._translate(
            html,
            formality=formality,
            tag_handling="html",
        ).text

        # Step 4: Remove notranslate class (cleanup)
        translated_html = self._unprotect_code_blocks(translated_html)

        # Step 5: Convert HTML back to Markdown
        translated_md = md(
            translated_html,
            heading_style="atx",
            bullets="-",
            strong_em_symbol="*",
        )

        # Clean up extra whitespace
        translated_md = self._clean_markdown(translated_md)

        return translated_md

    def _translate_many(self, texts: list[str], formality: Formality = "default") -> list[str]:
        """Batch-translate a list of plain strings in one DeepL call (#340)."""
        total_chars = sum(len(t) for t in texts)
        try:
            results = self.translator.translate_text(
                texts,
                target_lang=self.target_lang,
                formality=formality,
                **self._glossary_kwargs(),
            )
        except deepl.DeepLException as e:
            logger.error(
                "deepl translate (batch) failed: items=%d chars=%d error=%s",
                len(texts),
                total_chars,
                e,
            )
            sentry_sdk.capture_exception(e)
            raise
        logger.info(
            "deepl translate batch: items=%d chars=%d target_lang=%s formality=%s glossary=%s",
            len(texts),
            total_chars,
            self.target_lang,
            formality,
            self.glossary_id or "off",
        )
        # translate_text returns a single result for a str, a list for a list.
        if not isinstance(results, list):
            results = [results]
        return [r.text for r in results]

    def _sync_translate_blocks(
        self, blocks: list[dict], formality: Formality = "default"
    ) -> list[dict]:
        """Collect every translatable string across the blocks, translate them in one
        batch, and write them back into a deep copy (input untouched)."""
        out = copy.deepcopy(blocks)
        texts: list[str] = []
        setters: list = []

        def add(get, set_):
            val = get()
            if isinstance(val, str) and val.strip():
                texts.append(val)
                setters.append(set_)

        for b in out:
            t = b.get("type")
            if t == "cover":
                add(lambda b=b: b.get("title"), lambda v, b=b: b.__setitem__("title", v))
                add(lambda b=b: b.get("subtitle"), lambda v, b=b: b.__setitem__("subtitle", v))
            elif t == "section":
                add(lambda b=b: b.get("title"), lambda v, b=b: b.__setitem__("title", v))
            elif t == "paragraph":
                add(lambda b=b: b.get("text"), lambda v, b=b: b.__setitem__("text", v))
            elif t in ("bullets", "ordered_list"):
                items = b.get("items") or []
                for i in range(len(items)):
                    add(
                        lambda items=items, i=i: items[i],
                        lambda v, items=items, i=i: items.__setitem__(i, v),
                    )
            elif t == "quote":
                add(lambda b=b: b.get("text"), lambda v, b=b: b.__setitem__("text", v))
                add(
                    lambda b=b: b.get("attribution"),
                    lambda v, b=b: b.__setitem__("attribution", v),
                )
            elif t == "timeline":
                for e in b.get("events") or []:
                    add(lambda e=e: e.get("label"), lambda v, e=e: e.__setitem__("label", v))
                    add(lambda e=e: e.get("text"), lambda v, e=e: e.__setitem__("text", v))
            elif t == "map":
                add(lambda b=b: b.get("caption"), lambda v, b=b: b.__setitem__("caption", v))
                locs = b.get("locations") or []
                for i in range(len(locs)):
                    add(
                        lambda locs=locs, i=i: locs[i],
                        lambda v, locs=locs, i=i: locs.__setitem__(i, v),
                    )
            elif t == "image":
                add(lambda b=b: b.get("caption"), lambda v, b=b: b.__setitem__("caption", v))
            elif t == "callout":
                add(lambda b=b: b.get("label"), lambda v, b=b: b.__setitem__("label", v))
                add(lambda b=b: b.get("text"), lambda v, b=b: b.__setitem__("text", v))
                for term in b.get("terms") or []:
                    add(
                        lambda term=term: term.get("term"),
                        lambda v, term=term: term.__setitem__("term", v),
                    )
                    add(
                        lambda term=term: term.get("definition"),
                        lambda v, term=term: term.__setitem__("definition", v),
                    )
            elif t == "mc_question":
                add(lambda b=b: b.get("question"), lambda v, b=b: b.__setitem__("question", v))
                choices = b.get("choices") or []
                for i in range(len(choices)):
                    add(
                        lambda choices=choices, i=i: choices[i],
                        lambda v, choices=choices, i=i: choices.__setitem__(i, v),
                    )

        if not texts:
            return out

        for set_, translated in zip(setters, self._translate_many(texts, formality), strict=True):
            set_(self._normalize(translated))
        return out

    def _normalize(self, text: str) -> str:
        """Post-process DeepL output for the target locale (#531: ES thousands)."""
        if self.target_lang.upper().startswith("ES"):
            return normalize_es_thousands(text)
        return text

    def _protect_code_blocks(self, html: str) -> str:
        """
        Add notranslate class to code elements to prevent translation.

        DeepL respects the notranslate class and skips those elements.
        """
        # Protect <code> tags (with or without existing attributes).
        # Inject class="notranslate" AFTER any existing attributes to avoid
        # producing a duplicate class attribute (e.g. class="language-python").
        html = re.sub(
            r"<code(\s[^>]*)?>",
            lambda m: f'<code{m.group(1) or ""} class="notranslate">',
            html,
        )
        # Protect <pre> tags (for fenced code blocks), same attribute handling.
        html = re.sub(
            r"<pre(\s[^>]*)?>",
            lambda m: f'<pre{m.group(1) or ""} class="notranslate">',
            html,
        )
        return html

    def _unprotect_code_blocks(self, html: str) -> str:
        """Remove notranslate class after translation."""
        html = html.replace(' class="notranslate"', "")
        return html

    def _clean_markdown(self, md_content: str) -> str:
        """Clean up markdown after HTML conversion."""
        # Remove excessive blank lines (more than 2 consecutive)
        md_content = re.sub(r"\n{3,}", "\n\n", md_content)
        # Trim leading/trailing whitespace
        md_content = md_content.strip()
        return md_content


@lru_cache
def get_translation_service() -> TranslationService | None:
    """
    Get cached TranslationService instance.

    Returns None if DeepL API key is not configured.
    """
    settings = get_settings()
    if not settings.deepl_api:
        return None
    return TranslationService(
        settings.deepl_api,
        glossary_enabled=settings.deepl_glossary_enabled,
        glossary_id=settings.deepl_glossary_id,
    )
