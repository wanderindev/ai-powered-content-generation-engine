"""Backfill thumbnails for CDN-hosted media missing them.

Fetches images from DO Spaces and generates 300px JPEG thumbnails.
Only processes JPG/PNG — SVG/PDF/WebM are skipped.

Usage (from inside the backend container):
    python -m scripts.backfill_thumbnails
    python -m scripts.backfill_thumbnails --dry-run
"""

import argparse
import logging
import time

import httpx

from app.core.database import SessionLocal
from app.models.media import Media
from app.services.image_storage import ImageStorageService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

THUMBABLE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def main():
    parser = argparse.ArgumentParser(description="Backfill missing thumbnails")
    parser.add_argument("--dry-run", action="store_true", help="List what would be processed")
    args = parser.parse_args()

    db = SessionLocal()
    storage = ImageStorageService()

    candidates = (
        db.query(Media)
        .filter(
            Media.thumbnail_path.is_(None),
            Media.file_path.isnot(None),
            ~Media.file_path.contains("/static/"),
        )
        .order_by(Media.id)
        .all()
    )

    thumbable = [
        m for m in candidates
        if any(m.file_path.lower().endswith(ext) for ext in THUMBABLE_EXTS)
    ]

    logger.info("Found %d media without thumbnails, %d are thumbable", len(candidates), len(thumbable))

    if args.dry_run:
        for m in thumbable:
            logger.info("  Would process: id=%d %s", m.id, m.file_path)
        return

    success = 0
    failed = 0

    for m in thumbable:
        try:
            # Download from CDN
            resp = httpx.get(m.file_path, follow_redirects=True, timeout=60)
            resp.raise_for_status()

            # Derive storage key from CDN URL
            cdn_prefix = storage.cdn_url + "/"
            if m.file_path.startswith(cdn_prefix):
                key = m.file_path[len(cdn_prefix):]
            else:
                logger.warning("Unexpected file_path format: id=%d %s", m.id, m.file_path)
                failed += 1
                continue

            thumb_url = storage.generate_thumbnail(resp.content, key)
            if thumb_url:
                m.thumbnail_path = thumb_url
                db.commit()
                success += 1
                logger.info("OK id=%d -> %s", m.id, thumb_url)
            else:
                failed += 1
                logger.warning("Thumbnail generation returned None for id=%d", m.id)

            time.sleep(0.2)  # gentle on DO Spaces

        except Exception as e:
            failed += 1
            logger.error("Failed id=%d: %s", m.id, e)

    logger.info("Done: %d success, %d failed", success, failed)


if __name__ == "__main__":
    main()