"""One-time cleanup: merge duplicate tags, delete orphans, clear media_tags.

Steps:
1. Merge normalized duplicates (e.g., singular/plural variants)
2. Delete all media_tags associations (so backfill_media_tags starts fresh)
3. Delete tags not used by any article (orphaned + media-only become unused after step 2)
4. Report final state

Usage:
    python -m scripts.cleanup_tags --dry-run   # Preview changes
    python -m scripts.cleanup_tags              # Execute cleanup
"""

import argparse
import logging
import re

from sqlalchemy import delete, func, select, update

from app.core.database import SessionLocal
from app.models.media import media_tags
from app.models.tag import Tag, article_tags

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Manual merges: (keep_id, remove_id, reason)
MERGES = [
    (536, 255, "historical chronicle <- historical chronicles (plural)"),
    (64, 62, "Veragua <- Veraguas (plural)"),
    (520, 517, "lock construction <- lock wall construction (too specific)"),
    (513, 549, "maritime operations <- battleship transit (too specific)"),
    (513, 551, "maritime operations <- battlecruiser transit (too specific)"),
]

# Junk tags to delete regardless of usage
JUNK_TAG_IDS = [
    463,  # "test"
    464,  # "noriega" (lowercase, out of scope)
    524,  # "1883" (standalone year)
]


def normalize_tag(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[-_]", " ", s)
    s = re.sub(r"\s+", " ", s)
    if len(s) > 3 and s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    return s


def main():
    parser = argparse.ArgumentParser(description="Clean up tags before re-tagging media")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    args = parser.parse_args()

    db = SessionLocal()

    try:
        # --- Step 1: Merge duplicates ---
        logger.info("=== Step 1: Merge duplicate tags ===")
        for keep_id, remove_id, reason in MERGES:
            keep_tag = db.get(Tag, keep_id)
            remove_tag = db.get(Tag, remove_id)
            if not keep_tag or not remove_tag:
                logger.warning("  Skip merge: tag %d or %d not found", keep_id, remove_id)
                continue

            logger.info("  Merge: %s", reason)

            if not args.dry_run:
                # Move article_tags associations from remove -> keep (skip if already exists)
                existing_article_ids = set(
                    r[0] for r in db.execute(
                        select(article_tags.c.article_id)
                        .where(article_tags.c.tag_id == keep_id)
                    ).all()
                )
                remove_article_ids = set(
                    r[0] for r in db.execute(
                        select(article_tags.c.article_id)
                        .where(article_tags.c.tag_id == remove_id)
                    ).all()
                )
                to_move = remove_article_ids - existing_article_ids
                if to_move:
                    db.execute(
                        update(article_tags)
                        .where(
                            article_tags.c.tag_id == remove_id,
                            article_tags.c.article_id.in_(to_move),
                        )
                        .values(tag_id=keep_id)
                    )
                # Delete remaining associations for the removed tag
                db.execute(
                    delete(article_tags).where(article_tags.c.tag_id == remove_id)
                )
                # Move media_tags similarly
                existing_media_ids = set(
                    r[0] for r in db.execute(
                        select(media_tags.c.media_id)
                        .where(media_tags.c.tag_id == keep_id)
                    ).all()
                )
                remove_media_ids = set(
                    r[0] for r in db.execute(
                        select(media_tags.c.media_id)
                        .where(media_tags.c.tag_id == remove_id)
                    ).all()
                )
                to_move_media = remove_media_ids - existing_media_ids
                if to_move_media:
                    db.execute(
                        update(media_tags)
                        .where(
                            media_tags.c.tag_id == remove_id,
                            media_tags.c.media_id.in_(to_move_media),
                        )
                        .values(tag_id=keep_id)
                    )
                db.execute(
                    delete(media_tags).where(media_tags.c.tag_id == remove_id)
                )
                # Delete the tag
                db.execute(delete(Tag).where(Tag.id == remove_id))

        # --- Step 2: Delete junk tags ---
        logger.info("=== Step 2: Delete junk tags ===")
        for junk_id in JUNK_TAG_IDS:
            tag = db.get(Tag, junk_id)
            if not tag:
                continue
            logger.info("  Delete junk: [%d] %s", tag.id, tag.name)
            if not args.dry_run:
                db.execute(delete(article_tags).where(article_tags.c.tag_id == junk_id))
                db.execute(delete(media_tags).where(media_tags.c.tag_id == junk_id))
                db.execute(delete(Tag).where(Tag.id == junk_id))

        # --- Step 3: Clear all media_tags ---
        logger.info("=== Step 3: Clear all media_tags ===")
        media_tags_count = db.execute(
            select(func.count()).select_from(media_tags)
        ).scalar()
        logger.info("  Will delete %d media_tags associations", media_tags_count)
        if not args.dry_run:
            db.execute(delete(media_tags))

        # --- Step 4: Delete tags not used by any article ---
        logger.info("=== Step 4: Delete tags with no article associations ===")
        article_tag_ids = set(
            r[0] for r in db.execute(
                select(article_tags.c.tag_id).distinct()
            ).all()
        )
        all_tags = db.execute(select(Tag)).scalars().all()
        to_delete = [t for t in all_tags if t.id not in article_tag_ids]
        logger.info("  Will delete %d tags with no article usage", len(to_delete))
        for t in sorted(to_delete, key=lambda x: x.name):
            logger.info("    [%d] %s", t.id, t.name)

        if not args.dry_run:
            ids_to_delete = [t.id for t in to_delete]
            if ids_to_delete:
                db.execute(delete(Tag).where(Tag.id.in_(ids_to_delete)))

        if not args.dry_run:
            db.commit()
            # Final state
            remaining = db.execute(select(func.count(Tag.id))).scalar()
            logger.info("=== Done. %d tags remaining (all used by articles) ===", remaining)
        else:
            logger.info("=== DRY RUN complete. No changes made. ===")
            db.rollback()

    finally:
        db.close()


if __name__ == "__main__":
    main()