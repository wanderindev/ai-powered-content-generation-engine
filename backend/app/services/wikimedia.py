"""Wikimedia Commons service — crawl Panama-related categories and index images."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from html import unescape
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.media_candidate import CandidateSource, CandidateStatus, MediaCandidate
from app.services.http_client import RateLimiter, make_hardened_client, run_with_retry

logger = logging.getLogger(__name__)

# Progress callback signature: (units_done, units_total, current_unit).
ProgressCallback = Callable[[int, int, str], None]

API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"

# Default seed categories for Panama-related content.
# These are top-level Wikimedia Commons categories with rich subcategory trees.
PANAMA_SEED_CATEGORIES = [
    "History of Panama",
    "Panama Canal",
    "Panama Canal Zone",
    "Old maps of Panama",
    "Maps of Panama",
    "Monuments and memorials in Panama",
    "Archaeological sites in Panama",
    "Buildings in Panama City",
    "Casco Viejo, Panama City",
    "Fortifications in Panama",
    "History of Panama City",
    "Presidents of Panama",
    "Panamá Viejo",
    "Flora of Panama",
    "Fauna of Panama",
    "National symbols of Panama",
    "Culture of Panama",
]


class WikimediaService:
    """Synchronous client for the Wikimedia Commons API."""

    def __init__(
        self,
        user_agent: str = "PanamaInContext/1.0 (diego@panamaincontext.com)",
        rate_limit: float | None = None,
    ):
        self.user_agent = user_agent
        interval = (
            rate_limit if rate_limit is not None else get_settings().wikimedia_rate_limit_seconds
        )
        self.rate_limit = interval
        self._rate_limiter = RateLimiter(interval)
        # One pooled, SSRF-validating client for the whole crawl — reused across
        # calls (no per-request TLS handshake) (#73).
        self._http = make_hardened_client(headers={"User-Agent": user_agent})

    def __enter__(self) -> "WikimediaService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close the pooled HTTP client."""
        self._http.close()

    def _api_request(self, params: dict[str, Any]) -> dict[str, Any]:
        """Make a single API request: rate-limited, pooled, retried on transient 5xx/429."""
        self._rate_limiter.wait()
        params["format"] = "json"

        def _do() -> dict[str, Any]:
            response = self._http.get(API_ENDPOINT, params=params)
            response.raise_for_status()
            return response.json()

        return run_with_retry(_do)

    # ── Category tree traversal ──────────────────────────────────────────

    def get_subcategories(
        self,
        category: str,
        max_depth: int = 2,
        _current_depth: int = 0,
    ) -> list[str]:
        """
        Recursively discover subcategories under the given category.

        Returns a flat list of category names (without the "Category:" prefix).
        """
        if _current_depth >= max_depth:
            return []

        category = category.removeprefix("Category:")
        results = [category]
        cmcontinue = None

        while True:
            params: dict[str, Any] = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmtype": "subcat",
                "cmlimit": 50,
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            try:
                data = self._api_request(params)
            except Exception as e:
                logger.error("Error fetching subcategories for '%s': %s", category, e)
                break

            members = data.get("query", {}).get("categorymembers", [])
            for member in members:
                sub_name = member["title"].removeprefix("Category:")
                if sub_name not in results:
                    results.append(sub_name)
                    # Recurse into subcategory
                    children = self.get_subcategories(sub_name, max_depth, _current_depth + 1)
                    for child in children:
                        if child not in results:
                            results.append(child)

            # Handle pagination
            if "continue" in data:
                cmcontinue = data["continue"].get("cmcontinue")
            else:
                break

        return results

    # ── File listing from categories ─────────────────────────────────────

    def get_category_files(self, category: str, limit: int = 100) -> list[str]:
        """
        Get file page titles from a Wikimedia Commons category.

        Returns titles like "File:Panama_Canal_1913.jpg".
        """
        category = category.removeprefix("Category:")
        titles: list[str] = []
        cmcontinue = None

        while len(titles) < limit:
            params: dict[str, Any] = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmtype": "file",
                "cmlimit": min(50, limit - len(titles)),
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            try:
                data = self._api_request(params)
            except Exception as e:
                logger.error("Error fetching files for '%s': %s", category, e)
                break

            members = data.get("query", {}).get("categorymembers", [])
            titles.extend(m["title"] for m in members)

            if "continue" in data:
                cmcontinue = data["continue"].get("cmcontinue")
            else:
                break

        return titles[:limit]

    # ── Metadata fetching ────────────────────────────────────────────────

    def get_files_metadata(self, titles: list[str], batch_size: int = 50) -> list[dict[str, Any]]:
        """
        Fetch metadata for a list of file titles in batches.

        Returns standardized metadata dicts ready for MediaCandidate creation.
        """
        results: list[dict[str, Any]] = []

        for i in range(0, len(titles), batch_size):
            batch = titles[i : i + batch_size]
            params = {
                "action": "query",
                "prop": "imageinfo|categories",
                "titles": "|".join(batch),
                "iiprop": "url|size|mime|extmetadata",
                "iiextmetadatafilter": (
                    "License|LicenseUrl|Attribution|Artist|ImageDescription|ObjectName|Categories"
                ),
                "cllimit": "max",
            }

            try:
                data = self._api_request(params)
            except Exception as e:
                logger.error("Error fetching metadata batch %d-%d: %s", i, i + batch_size, e)
                continue

            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                metadata = self._extract_metadata(page)
                if metadata:
                    results.append(metadata)

        return results

    def _extract_metadata(self, page: dict[str, Any]) -> dict[str, Any] | None:
        """Extract standardized metadata from a Wikimedia API page response."""
        if "imageinfo" not in page:
            return None

        try:
            info = page["imageinfo"][0]
            ext = info.get("extmetadata", {})

            # Clean HTML from metadata fields
            description = _clean_html(ext.get("ImageDescription", {}).get("value"))
            author = _clean_html(ext.get("Artist", {}).get("value"))

            # Prefer ObjectName over filename for title
            obj_name = ext.get("ObjectName", {}).get("value")
            raw_title = obj_name or page["title"].removeprefix("File:")
            title = _clean_html(raw_title) or page["title"]

            # Extract categories from the response
            categories = [c["title"].removeprefix("Category:") for c in page.get("categories", [])]

            # Build thumbnail URL (300px wide)
            thumb_url = _make_thumbnail_url(info.get("url", ""), 300)

            # Page URL on Wikimedia Commons
            commons_id = page["title"]
            page_url = f"https://commons.wikimedia.org/wiki/{commons_id.replace(' ', '_')}"

            return {
                "source": CandidateSource.WIKIMEDIA.value,
                "external_id": commons_id,
                "external_url": info["url"],
                "thumbnail_url": thumb_url,
                "page_url": page_url,
                "title": title[:500],
                "description": description,
                "author": author[:500] if author else None,
                "license": ext.get("License", {}).get("value"),
                "license_url": ext.get("LicenseUrl", {}).get("value"),
                "width": info.get("width"),
                "height": info.get("height"),
                "mime_type": info.get("mime"),
                "file_size": info.get("size"),
                "source_categories": categories[:20] if categories else None,
            }
        except (KeyError, IndexError) as e:
            logger.warning("Error extracting metadata from page %s: %s", page.get("title"), e)
            return None

    # ── Crawl orchestrator ───────────────────────────────────────────────

    def crawl_categories(
        self,
        db: Session,
        seed_categories: list[str] | None = None,
        max_depth: int = 2,
        max_per_category: int = 100,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """
        Crawl Panama-related Wikimedia categories and upsert results into DB.

        ``progress_callback(done, total, current)`` is invoked after each category
        is processed so a background-job runner can report progress (#277). It is
        optional — direct callers (scripts/tests) can omit it.

        Returns stats about the crawl.
        """
        seeds = seed_categories or PANAMA_SEED_CATEGORIES
        now = datetime.now(UTC)
        stats = {
            "categories_crawled": 0,
            "images_found": 0,
            "images_new": 0,
            "images_skipped": 0,
            "errors": [],
        }

        # Discover all categories (seeds + subcategories)
        all_categories: list[str] = []
        for seed in seeds:
            try:
                cats = self.get_subcategories(seed, max_depth=max_depth)
                for c in cats:
                    if c not in all_categories:
                        all_categories.append(c)
            except Exception as e:
                msg = f"Error discovering subcategories for '{seed}': {e}"
                logger.error(msg)
                stats["errors"].append(msg)

        logger.info("Discovered %d categories from %d seeds", len(all_categories), len(seeds))

        total = len(all_categories)

        # Crawl each category for files
        for index, category in enumerate(all_categories, start=1):
            try:
                titles = self.get_category_files(category, limit=max_per_category)
                if titles:
                    stats["categories_crawled"] += 1
                    metadata_list = self.get_files_metadata(titles)
                    stats["images_found"] += len(metadata_list)

                    # Upsert into database
                    new_count, skip_count = self._upsert_candidates(db, metadata_list, now)
                    stats["images_new"] += new_count
                    stats["images_skipped"] += skip_count

            except Exception as e:
                msg = f"Error crawling category '{category}': {e}"
                logger.error(msg)
                stats["errors"].append(msg)

            if progress_callback is not None:
                progress_callback(index, total, category)

        return stats

    def _upsert_candidates(
        self,
        db: Session,
        metadata_list: list[dict[str, Any]],
        crawled_at: datetime,
    ) -> tuple[int, int]:
        """
        Insert new candidates, skip existing ones. Merge source_categories for duplicates.

        Returns (new_count, skip_count).
        """
        new_count = 0
        skip_count = 0

        for meta in metadata_list:
            # Check if already exists
            existing = db.execute(
                select(MediaCandidate).where(
                    MediaCandidate.source == meta["source"],
                    MediaCandidate.external_id == meta["external_id"],
                )
            ).scalar_one_or_none()

            if existing:
                # Merge new categories into existing record
                if meta.get("source_categories"):
                    old_cats = set(existing.source_categories or [])
                    new_cats = old_cats | set(meta["source_categories"])
                    if new_cats != old_cats:
                        existing.source_categories = sorted(new_cats)
                skip_count += 1
            else:
                candidate = MediaCandidate(
                    **meta,
                    status=CandidateStatus.PENDING.value,
                    crawled_at=crawled_at,
                )
                db.add(candidate)
                new_count += 1

        db.commit()
        return new_count, skip_count


# ── Utility functions ────────────────────────────────────────────────────


def _clean_html(html_content: str | None) -> str | None:
    """Strip HTML tags, unescape entities, and normalize whitespace."""
    if not html_content:
        return None

    text = unescape(html_content)

    # Simple tag stripping without BeautifulSoup dependency
    import re

    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text.strip() or None


def _make_thumbnail_url(full_url: str, width: int = 300) -> str | None:
    """
    Build a Wikimedia thumbnail URL from a full-resolution image URL.

    Wikimedia URLs follow the pattern:
    https://upload.wikimedia.org/wikipedia/commons/a/ab/File.jpg
    Thumbnails are at:
    https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/File.jpg/{width}px-File.jpg
    """
    if not full_url or "upload.wikimedia.org" not in full_url:
        return None

    # Don't thumbnail SVGs — they scale natively
    if full_url.lower().endswith(".svg"):
        return full_url

    try:
        # Replace /commons/ or /en/ etc. path with /commons/thumb/
        url = full_url.replace("/wikipedia/commons/", "/wikipedia/commons/thumb/", 1)
        filename = full_url.rsplit("/", 1)[-1]
        return f"{url}/{width}px-{filename}"
    except Exception as e:
        # Benign fallback: an unexpected URL shape just means no derived thumbnail,
        # and the caller falls back to the full-resolution image. Log so the (very
        # rare) failure is observable rather than fully silent.
        logger.debug("Could not derive thumbnail URL for %s: %s", full_url, e)
        return None
