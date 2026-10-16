"""Backfill search_terms and alt_text for media rows that have none.

Since #589, ``search_terms`` is written at import / PATCH / approve time by
``app.services.media_metadata``; this script catches the rows those paths
missed (a generation failure leaves the column NULL) plus the legacy ``""``
rows an older version of this script wrote on failed batches. It joins media
with media_candidates for the richer crawl metadata (description, categories,
tags, relevance notes) and sends batches to Haiku through ``create_message``.

A generated ``alt_text`` replaces only a default one (blank, or equal to the
title) — an operator-written alt is never overwritten. Rows whose batch fails,
or that the model leaves out of its reply, stay NULL and are skipped for the
rest of the run (so the loop always terminates); the next run retries them.
An imported image whose operator keywords were stored but whose generation
failed is not NULL, so it is skipped here; editing its title / caption /
credit regenerates it.

Usage (from ``backend/``, inside the backend container):
    python -m scripts.backfill_search_terms
    python -m scripts.backfill_search_terms --batch-size 10 --max-batches 5
    python -m scripts.backfill_search_terms --dry-run
"""

import argparse
import logging

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.media import Media
from app.models.media_candidate import MediaCandidate
from app.services.media_metadata import (
    alt_is_default,
    generate_search_metadata,
    media_text_input,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FEATURE = "media_search_terms_backfill"


def backfill(
    db: Session,
    *,
    batch_size: int = 20,
    max_batches: int | None = None,
    dry_run: bool = False,
) -> int:
    """Fill missing ``search_terms``; returns the number of rows updated.

    Does not close ``db`` — the caller owns the session.
    """
    batches_done = 0
    total_updated = 0
    skip_ids: set[int] = set()

    # Skip article feature images — they belong to articles, not the media library.
    feature_image_ids = (
        select(Article.feature_image_id)
        .where(Article.feature_image_id.isnot(None))
        .scalar_subquery()
    )

    while True:
        if max_batches and batches_done >= max_batches:
            break

        query = (
            select(Media, MediaCandidate)
            .outerjoin(MediaCandidate, MediaCandidate.media_id == Media.id)
            .where(or_(Media.search_terms.is_(None), Media.search_terms == ""))
            .where(Media.id.notin_(feature_image_ids))
        )
        if skip_ids:
            query = query.where(Media.id.notin_(skip_ids))
        rows = db.execute(query.order_by(Media.id).limit(batch_size)).all()

        if not rows:
            logger.info("No more media records to process.")
            break

        batch_ids = [media.id for media, _ in rows]
        logger.info("Batch %d: processing %d media records", batches_done + 1, len(rows))

        if dry_run:
            for media, _ in rows:
                logger.info(
                    "  [DRY RUN] Would process: %d — %s", media.id, (media.title or "")[:60]
                )
            skip_ids.update(batch_ids)
            batches_done += 1
            continue

        items = [media_text_input(media, candidate) for media, candidate in rows]
        try:
            results = generate_search_metadata(items, feature=FEATURE)
        except Exception as e:  # noqa: BLE001 — one bad batch must not stop the run
            logger.error("Error processing batch %d: %s", batches_done + 1, e)
            db.rollback()
            # Left NULL (never ""), so a later run retries them.
            skip_ids.update(batch_ids)
            batches_done += 1
            continue

        for media, _ in rows:
            meta = results.get(media.id)
            if not meta:
                skip_ids.add(media.id)  # omitted by the model — don't re-select it
                continue
            media.search_terms = meta["search_terms"]
            if meta["alt_text"] and alt_is_default(media.alt_text, media.title):
                media.alt_text = meta["alt_text"]
            total_updated += 1
            logger.info("  Updated media %d: %s", media.id, (media.title or "")[:50])
        db.commit()
        batches_done += 1

    logger.info("Done. Updated %d media records in %d batches.", total_updated, batches_done)
    return total_updated


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Backfill search_terms and alt_text for media")
    parser.add_argument(
        "--batch-size", type=int, default=20, help="Items per Haiku batch (default: 20)"
    )
    parser.add_argument(
        "--max-batches", type=int, default=None, help="Max batches to process (default: all)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be processed without calling Haiku"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        backfill(
            db,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            dry_run=args.dry_run,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
