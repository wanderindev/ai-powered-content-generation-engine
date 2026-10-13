"""Library of Congress service — search and index Panama-related historical images."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.media_candidate import CandidateSource, CandidateStatus, MediaCandidate
from app.services.http_client import RateLimiter, make_hardened_client, run_with_retry

logger = logging.getLogger(__name__)

# Progress callback signature: (units_done, units_total, current_unit).
ProgressCallback = Callable[[int, int, str], None]

API_BASE = "https://www.loc.gov"

_USER_AGENT = "PanamaInContext/1.0 (diego@panamaincontext.com)"

# Targeted search queries for Panama-related content at LOC.
# Strategy: keyword searches against specific collections/formats (not tree walking).
LOC_SEARCH_QUERIES = [
    {"collection": "panoramic-photographs", "query": "panama"},
    {"collection": "panoramic-photographs", "query": "canal zone"},
    {"query": "panama canal", "format": "photos"},
    {"query": "panama canal construction", "format": "photos"},
    {"query": "panama canal zone", "format": "photos"},
    {"query": "panama canal", "format": "maps"},
    {"query": "panama isthmus", "format": "maps"},
    {"query": "gatun locks", "format": "photos"},
    {"query": "culebra cut", "format": "photos"},
    {"query": "panama canal workers", "format": "photos"},
    {"query": "balboa panama", "format": "photos"},
    {"query": "colon panama", "format": "photos"},
    {"query": "panama railroad", "format": "photos"},
    {"query": "panama city", "format": "maps", "dates": "1800-1950"},
]


class LOCService:
    """Synchronous client for the Library of Congress API."""

    def __init__(self, rate_limit: float | None = None):
        # LOC allows 20 req/min — 3-second intervals to stay safe (config default).
        interval = rate_limit if rate_limit is not None else get_settings().loc_rate_limit_seconds
        self.rate_limit = interval
        self._rate_limiter = RateLimiter(interval)
        # One pooled, SSRF-validating client for the whole crawl — reused across
        # calls (no per-request TLS handshake) (#73).
        self._http = make_hardened_client(headers={"User-Agent": _USER_AGENT})

    def __enter__(self) -> "LOCService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Close the pooled HTTP client."""
        self._http.close()

    def _api_request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a single API request: rate-limited, pooled, retried on transient 5xx/429."""
        self._rate_limiter.wait()
        params = params or {}
        params["fo"] = "json"

        def _do() -> dict[str, Any]:
            response = self._http.get(url, params=params)
            response.raise_for_status()
            return response.json()

        return run_with_retry(_do)

    # ── Search ─────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        collection: str | None = None,
        fmt: str | None = None,
        dates: str | None = None,
        limit: int = 150,
    ) -> list[dict[str, Any]]:
        """
        Search the LOC API and return raw result items.

        Args:
            query: Search terms
            collection: LOC collection slug (e.g. "panoramic-photographs")
            fmt: Format filter (e.g. "photos", "maps")
            dates: Date range string (e.g. "1800-1950")
            limit: Maximum results to fetch (across pages)
        """
        results: list[dict[str, Any]] = []
        page = 1

        while len(results) < limit:
            # Build the URL path
            if collection:
                url = f"{API_BASE}/collections/{collection}/"
            elif fmt:
                url = f"{API_BASE}/{fmt}/"
            else:
                url = f"{API_BASE}/search/"

            params: dict[str, Any] = {"q": query, "sp": page, "c": 50}
            if dates:
                params["dates"] = dates
            # If we specified a collection URL but also want format filtering
            if collection and fmt:
                params["fa"] = f"original-format:{fmt}"

            try:
                data = self._api_request(url, params)
            except Exception as e:
                logger.error("LOC search error (query=%r, page=%d): %s", query, page, e)
                break

            items = data.get("results", [])
            if not items:
                break

            results.extend(items)
            page += 1

            # Stop if we've hit the last page
            pagination = data.get("pagination", {})
            if page > pagination.get("total", 1):
                break

        return results[:limit]

    # ── Item detail ────────────────────────────────────────────────────────

    def get_item_detail(self, item_url: str) -> dict[str, Any] | None:
        """Fetch full metadata for a single LOC item."""
        try:
            data = self._api_request(item_url)
            return data
        except Exception as e:
            logger.error("LOC item detail error (%s): %s", item_url, e)
            return None

    # ── Metadata extraction ────────────────────────────────────────────────

    def _extract_metadata(self, result: dict[str, Any]) -> dict[str, Any] | None:
        """Convert a LOC search result into our standard metadata format."""
        try:
            # LOC results have varying structure — some fields may be missing
            item_id = result.get("id")
            if not item_id:
                return None

            title = result.get("title")
            if not title:
                return None

            # Description: combine description and notes
            desc_parts = []
            description = result.get("description")
            if description:
                if isinstance(description, list):
                    desc_parts.extend(description)
                else:
                    desc_parts.append(str(description))
            notes = result.get("notes")
            if notes:
                if isinstance(notes, list):
                    desc_parts.extend(notes)
                else:
                    desc_parts.append(str(notes))
            full_description = " ".join(desc_parts) if desc_parts else None

            # Author/creator
            contributors = result.get("contributor") or result.get("creator") or []
            if isinstance(contributors, list):
                author = contributors[0] if contributors else None
            else:
                author = str(contributors)

            # URLs
            page_url = result.get("url") or item_id
            if page_url and not page_url.startswith("http"):
                page_url = f"{API_BASE}{page_url}"

            # Image URLs — prefer IIIF full-res for external_url (what we download),
            # use search result image_url only as fallback
            image_url = None
            thumbnail_url = None

            resource_id = _extract_resource_id(item_id)
            if resource_id:
                # IIIF full-resolution URL (what gets downloaded on approval)
                image_url = (
                    f"https://tile.loc.gov/image-services/iiif/"
                    f"{resource_id}/full/pct:100/0/default.jpg"
                )
                # IIIF 300px thumbnail for grid display
                thumbnail_url = (
                    f"https://tile.loc.gov/image-services/iiif/"
                    f"{resource_id}/full/300,/0/default.jpg"
                )

            # Fallback: use image_url from search results if no IIIF
            if not image_url:
                image_urls = result.get("image_url")
                if image_urls:
                    if isinstance(image_urls, list):
                        for candidate_url in reversed(image_urls):
                            if isinstance(candidate_url, str):
                                image_url = candidate_url
                                break
                    elif isinstance(image_urls, str):
                        image_url = image_urls

            # Last resort: try the gallery thumbnail
            if not image_url:
                thumb = result.get("thumb_gallery")
                if thumb:
                    image_url = thumb
                else:
                    # Skip items with no image at all
                    return None

            if not thumbnail_url:
                thumbnail_url = image_url

            # Tags/subjects
            subjects = result.get("subject") or []
            if isinstance(subjects, list):
                tags = [s for s in subjects[:20] if isinstance(s, str)]
            else:
                tags = [str(subjects)]

            # License — LOC items are typically public domain
            rights = result.get("rights") or []
            license_text = _determine_license(rights)

            # Source categories (collection names)
            collections = result.get("partof") or result.get("collection") or []
            if isinstance(collections, list):
                source_categories = [str(c) for c in collections[:10] if c]
            elif collections:
                source_categories = [str(collections)]
            else:
                source_categories = None

            # Use LOC item path as external_id for deduplication
            external_id = item_id.rstrip("/")

            return {
                "source": CandidateSource.LOC.value,
                "external_id": external_id,
                "external_url": image_url,
                "thumbnail_url": thumbnail_url,
                "page_url": page_url,
                "title": title[:500],
                "description": full_description[:5000] if full_description else None,
                "author": author[:500] if author else None,
                "tags": tags if tags else None,
                "license": license_text,
                "license_url": None,  # LOC doesn't provide per-item license URLs
                "source_categories": source_categories,
            }
        except Exception as e:
            logger.warning("Error extracting LOC metadata from result: %s", e)
            return None

    # ── Crawl orchestrator ─────────────────────────────────────────────────

    def crawl_panama(
        self,
        db: Session,
        queries: list[dict[str, str]] | None = None,
        max_per_query: int = 150,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """
        Run all LOC search queries and upsert results into the media_candidates table.

        ``progress_callback(done, total, current)`` is invoked after each query so a
        background-job runner can report progress (#277). It is optional — direct
        callers (scripts/tests) can omit it.

        Returns stats about the crawl.
        """
        search_queries = queries or LOC_SEARCH_QUERIES
        now = datetime.now(UTC)
        stats = {
            "queries_executed": 0,
            "images_found": 0,
            "images_new": 0,
            "images_skipped": 0,
            "errors": [],
        }

        total = len(search_queries)

        for index, q in enumerate(search_queries, start=1):
            query_text = q["query"]
            collection = q.get("collection")
            fmt = q.get("format")
            dates = q.get("dates")

            logger.info(
                "LOC search: query=%r collection=%r format=%r dates=%r",
                query_text,
                collection,
                fmt,
                dates,
            )

            try:
                results = self.search(
                    query=query_text,
                    collection=collection,
                    fmt=fmt,
                    dates=dates,
                    limit=max_per_query,
                )
                stats["queries_executed"] += 1

                metadata_list = []
                for result in results:
                    meta = self._extract_metadata(result)
                    if meta:
                        metadata_list.append(meta)

                stats["images_found"] += len(metadata_list)
                logger.info(
                    "LOC query %r: %d results, %d with extractable metadata",
                    query_text,
                    len(results),
                    len(metadata_list),
                )

                # Upsert into database
                new_count, skip_count = self._upsert_candidates(db, metadata_list, now)
                stats["images_new"] += new_count
                stats["images_skipped"] += skip_count

            except Exception as e:
                msg = f"Error in LOC search query={query_text!r}: {e}"
                logger.error(msg)
                stats["errors"].append(msg)

            if progress_callback is not None:
                progress_callback(index, total, query_text)

        return stats

    def _upsert_candidates(
        self,
        db: Session,
        metadata_list: list[dict[str, Any]],
        crawled_at: datetime,
    ) -> tuple[int, int]:
        """Insert new candidates, skip existing. Returns (new_count, skip_count)."""
        new_count = 0
        skip_count = 0

        for meta in metadata_list:
            existing = db.execute(
                select(MediaCandidate).where(
                    MediaCandidate.source == meta["source"],
                    MediaCandidate.external_id == meta["external_id"],
                )
            ).scalar_one_or_none()

            if existing:
                # Merge tags and categories
                if meta.get("tags"):
                    old_tags = set(existing.tags or [])
                    new_tags = old_tags | set(meta["tags"])
                    if new_tags != old_tags:
                        existing.tags = sorted(new_tags)
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


def get_best_image_url(page_url: str) -> str | None:
    """
    Fetch the LOC item detail API and return the best available image URL.

    LOC items have multiple image sizes in their 'files' array:
    - _150px.jpg — tiny thumbnail
    - v.jpg — medium (~1024px)
    - r.jpg — large (~2000px)
    - u.tif — original TIFF (huge, skip)

    We prefer r.jpg (large JPEG), then v.jpg, then the largest from image_url.
    """
    try:
        # Route through the SSRF-validating client — page_url comes from
        # candidate metadata, so this fetch (and the variant HEAD probes below)
        # are attacker-influenceable (#72).
        with make_hardened_client(headers={"User-Agent": _USER_AGENT}) as client:
            response = client.get(page_url, params={"fo": "json"})
            response.raise_for_status()
            data = response.json()

            # Look through resources -> files for JPEG images, sorted by size
            best_url = None
            best_size = 0

            for resource in data.get("resources", []):
                for file_group in resource.get("files", []):
                    for f in file_group:
                        mime = f.get("mimetype", "")
                        url = f.get("url", "")
                        size = f.get("size", 0) or 0

                        # Skip non-JPEG and TIFFs (too large)
                        if mime not in ("image/jpeg", "image/jpg"):
                            continue

                        # Prefer the largest JPEG that's not the tiny thumbnail
                        if size > best_size and size < 10_000_000:  # cap at 10MB
                            best_url = url
                            best_size = size

            if best_url:
                return best_url

            # Fallback: try URL suffix variants from image_url or resource image
            # LOC storage-services URLs follow a pattern: .../{id}_150px.jpg
            # Larger variants: {id}r.jpg (large), {id}v.jpg (medium)
            candidate_urls = []
            item = data.get("item", {})
            image_urls = item.get("image_url", [])
            if isinstance(image_urls, list):
                candidate_urls.extend(u.split("#")[0] for u in image_urls if isinstance(u, str))
            for resource in data.get("resources", []):
                img = resource.get("image")
                if img:
                    candidate_urls.append(img.split("#")[0])

            for base_url in candidate_urls:
                if "_150px.jpg" in base_url:
                    # Try r.jpg (large) and v.jpg (medium) variants
                    base = base_url.replace("_150px.jpg", "")
                    for suffix in ["r.jpg", "v.jpg"]:
                        test_url = f"{base}{suffix}"
                        try:
                            resp = client.head(test_url)
                            if resp.status_code == 200:
                                return test_url
                        except Exception:
                            # Best-effort probe: skip variants that error or aren't
                            # 200 and try the next one. Outer except logs if all fail.
                            continue

        return None
    except Exception as e:
        logger.warning("Failed to fetch best image URL from %s: %s", page_url, e)
        return None


def _extract_resource_id(item_id: str) -> str | None:
    """
    Extract the IIIF resource ID from a LOC item ID/URL.

    LOC item IDs look like: /item/2007660946/ or https://www.loc.gov/item/2007660946/
    The resource ID for IIIF is typically: service:pnp:pan:6a{xxxxx} for panoramic photos,
    but varies by collection. We'll try the numeric ID directly.
    """
    if not item_id:
        return None

    # Extract the numeric/string ID from the URL path
    parts = item_id.rstrip("/").split("/")
    for part in reversed(parts):
        if part and part != "item" and part != "resource":
            return part

    return None


def _determine_license(rights: Any) -> str:
    """Determine license text from LOC rights field."""
    if not rights:
        return "pd-us-gov"

    rights_text = ""
    if isinstance(rights, list):
        rights_text = " ".join(str(r) for r in rights).lower()
    elif isinstance(rights, str):
        rights_text = rights.lower()

    if "no known restrictions" in rights_text:
        return "pd-no-known-restrictions"
    if "public domain" in rights_text:
        return "pd-us-gov"
    if "copyright" in rights_text:
        return "copyright-check-required"

    return "pd-us-gov"
