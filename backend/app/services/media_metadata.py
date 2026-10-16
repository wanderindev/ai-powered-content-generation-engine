"""Bilingual search terms + alt text for media rows (#589).

The single home of the search-terms prompt. Three write paths use it:

- ``POST /media-library/media/import-url`` — an editor-imported image gets its
  terms at import time, merged after any operator keywords;
- ``PATCH /media-library/media/{id}`` — regenerated when the title, caption or
  credit changes (unless the body sets ``search_terms`` itself);
- ``POST /media-library/candidates/{id}/approve`` — a crawled image gets its
  terms the moment it becomes a ``Media`` row;

plus ``scripts.backfill_search_terms`` for rows that have none. Every call goes
through ``create_message`` on Haiku.

Media keywords deliberately never become ``Tag`` rows: ``Tag`` is the
*article* tag vocabulary (``article_generation`` feeds every row into the
article-tagging prompt), so they are merged into ``Media.search_terms`` instead.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import sentry_sdk

from app.core.config import get_settings
from app.services.llm import HAIKU_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

SEARCH_TERMS_PROMPT = """You are generating bilingual search terms and alt text for images on a website
about Panama's history, geography, and culture.

For each image below, return:
1. **search_terms**: A comma-separated list of 10-20 search keywords in BOTH English and Spanish.
   Include terms for: the subject matter, historical period, location, people depicted,
   type of image (photo, engraving, map, painting, etc.), and relevant themes.
   Example: "Panama Canal, Canal de Panamá, construction, construcción, Culebra Cut, excavation,
   steam shovel, 1910s, engineering, ingeniería"

2. **alt_text**: A concise, descriptive alt text (max 200 chars) in English that describes
   what the image shows. Good alt text helps visually impaired users and improves SEO.
   Example: "Black and white photograph of workers operating steam shovels during the Culebra Cut excavation, Panama Canal, circa 1910"

Respond with a JSON array. Each element must have:
- "id" (integer): the media ID
- "search_terms" (string): comma-separated bilingual keywords
- "alt_text" (string): descriptive alt text, max 200 characters

Images to process:
"""

# Haiku 5.5 thinks at medium effort and thinking counts toward the cap (#610),
# so this covers the thinking plus the JSON array (was 4096). At 8000 the call
# streams, on the 600s timeout.
_MAX_TOKENS = 8000


@dataclass
class MediaTextInput:
    """A plain snapshot of what the model sees for one image.

    Built before the pre-LLM commit so the call never touches ORM attributes
    (which would re-open a transaction and pin a pool slot).
    """

    id: int
    title: str | None
    source: str
    description: str | None = None
    credit: str | None = None
    keywords: str | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None
    relevance_notes: str | None = None


def media_text_input(media, candidate=None, *, keywords: str | None = None) -> MediaTextInput:
    """Snapshot a ``Media`` row (and its crawl candidate, if any)."""
    return MediaTextInput(
        id=media.id,
        title=media.title,
        source=str(media.source),
        description=media.caption or (candidate.description if candidate else None),
        credit=media.attribution,
        keywords=keywords,
        categories=list(candidate.source_categories or []) if candidate else None,
        tags=list(candidate.tags or []) if candidate else None,
        relevance_notes=candidate.relevance_notes if candidate else None,
    )


def build_item_text(item: MediaTextInput) -> str:
    """One `` | ``-joined line describing an image for the prompt."""
    parts = [f"[ID={item.id}] Title: {item.title or 'N/A'}"]
    if item.description:
        parts.append(f"Description: {item.description[:300]}")
    if item.credit:
        parts.append(f"Credit: {item.credit[:200]}")
    if item.keywords:
        parts.append(f"Keywords: {item.keywords[:300]}")
    if item.categories:
        parts.append(f"Categories: {', '.join(item.categories[:8])}")
    if item.tags:
        parts.append(f"Tags: {', '.join(item.tags[:10])}")
    if item.relevance_notes:
        parts.append(f"Relevance: {item.relevance_notes[:200]}")
    parts.append(f"Source: {item.source}")
    return " | ".join(parts)


def _extract_json_text(response_text: str) -> str:
    """Strip markdown code fences from an LLM response."""
    text = response_text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def parse_search_terms_response(text: str) -> list[dict]:
    """Extract the JSON array from the reply, with a regex repair fallback."""
    body = _extract_json_text(text)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        # Fallback: salvage the complete individual objects (e.g. a reply cut
        # off mid-array).
        objects = []
        for match in re.finditer(r'\{[^{}]*"id"\s*:\s*(\d+)[^{}]*\}', body):
            try:
                objects.append(json.loads(match.group(0)))
            except json.JSONDecodeError:
                continue
        if objects:
            logger.warning("JSON repair: extracted %d objects via regex fallback", len(objects))
            return objects
        raise
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError("search-terms reply is not a JSON array")
    return [obj for obj in parsed if isinstance(obj, dict)]


def merge_search_terms(*parts: str | None) -> str | None:
    """Comma-merge term lists, de-duplicated case-insensitively, first wins."""
    seen: set[str] = set()
    terms: list[str] = []
    for part in parts:
        if not part:
            continue
        for raw in part.split(","):
            term = raw.strip()
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            terms.append(term)
    return ", ".join(terms) if terms else None


def generate_search_metadata(items: list[MediaTextInput], *, feature: str) -> dict[int, dict]:
    """One Haiku call for ``items``; raises on any failure.

    Returns ``{id: {"search_terms": str, "alt_text": str | None}}`` for the
    ids the model answered with non-blank terms; an omitted id is simply
    absent (callers treat it as a failure for that item).
    """
    # reject_on_truncation=False is deliberate: the regex repair in
    # parse_search_terms_response salvages every complete object from a
    # truncated reply, and any id missing from it is treated as a failure. A
    # reply cut off while still thinking has no text block at all; first_text
    # then raises ValueError, which is this function's "raises on any failure".
    message = create_message(
        feature=feature,
        model=HAIKU_MODEL,
        max_tokens=_MAX_TOKENS,
        output_config={"effort": "medium"},
        reject_on_truncation=False,
        messages=[
            {
                "role": "user",
                "content": SEARCH_TERMS_PROMPT + "\n".join(build_item_text(i) for i in items),
            }
        ],
    )
    results = parse_search_terms_response(first_text(message))
    wanted = {i.id for i in items}
    out: dict[int, dict] = {}
    for obj in results:
        mid = obj.get("id")
        terms = obj.get("search_terms")
        if mid not in wanted or not isinstance(terms, str) or not terms.strip():
            continue
        alt = obj.get("alt_text")
        alt = alt.strip()[:255] if isinstance(alt, str) and alt.strip() else None
        out[mid] = {"search_terms": terms.strip(), "alt_text": alt}
    return out


def try_generate_search_metadata(
    item: MediaTextInput, *, feature: str = "media_search_terms"
) -> dict | None:
    """Best-effort single-item generation; never raises.

    ``None`` when no Anthropic key is configured, the call fails, or the model
    did not answer for this item.
    """
    if not get_settings().anthropic_api_key:
        return None
    try:
        result = generate_search_metadata([item], feature=feature)
    except Exception as exc:  # noqa: BLE001 — the media write must not fail on generation
        logger.exception("media search terms: generation failed for media %d", item.id)
        sentry_sdk.capture_exception(exc)
        return None
    return result.get(item.id)


def alt_is_default(alt_text: str | None, *titles: str | None) -> bool:
    """True when ``alt_text`` is blank or just a copy of a title.

    Both write paths fall back to ``alt_text = title``, so only such a value
    counts as auto-generated and safe to replace.
    """
    if not alt_text or not alt_text.strip():
        return True
    return any(t is not None and alt_text == t for t in titles)
