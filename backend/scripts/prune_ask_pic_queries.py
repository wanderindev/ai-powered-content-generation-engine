"""Prune Ask PIC query rows past the retention window (#634, epic #619).

Deletes ``ask_pic_queries`` rows whose ``created_at`` is older than
``--retention-days`` (default: ``ASK_PIC_RETENTION_DAYS``, 30). Whole rows go,
not just the question text: the ``ip_hash`` is per-person data too, and the
rate limits and daily USD cap only ever read the current hour / UTC day.
Nothing references ``ask_pic_queries``, so deleting is safe.

Usage:
    python -m scripts.prune_ask_pic_queries --dry-run            # Preview
    python -m scripts.prune_ask_pic_queries                      # Execute
    python -m scripts.prune_ask_pic_queries --retention-days 7   # Custom window
"""

import argparse
import logging
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.ask_pic_query import AskPicQuery
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def prune_ask_pic_queries(
    db: Session, *, retention_days: int | None = None, dry_run: bool = False
) -> int:
    """Delete rows created more than ``retention_days`` ago.

    Returns the number of rows deleted (or, in dry-run mode, that would be).
    """
    if retention_days is None:
        retention_days = get_settings().ask_pic_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    query = db.query(AskPicQuery).filter(AskPicQuery.created_at < cutoff)
    count = query.count()

    if dry_run:
        logger.info(
            "DRY RUN: %d ask_pic_queries row(s) created before %s would be deleted.",
            count,
            cutoff,
        )
        db.rollback()
        return count

    query.delete(synchronize_session=False)
    db.commit()
    logger.info("Deleted %d ask_pic_queries row(s) created before %s.", count, cutoff)
    return count


def main():
    parser = argparse.ArgumentParser(description="Prune Ask PIC query rows past retention")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the count without deleting",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Delete rows older than this many days (default: ASK_PIC_RETENTION_DAYS)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        prune_ask_pic_queries(db, retention_days=args.retention_days, dry_run=args.dry_run)
    finally:
        db.close()


if __name__ == "__main__":
    main()
