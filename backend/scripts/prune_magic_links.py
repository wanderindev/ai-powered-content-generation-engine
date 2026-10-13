"""Prune long-expired magic-link rows.

Deletes rows from ``magic_links`` whose ``expires_at`` is older than a grace
window (default 30 days) after expiry. The grace window preserves a short
forensic trail for recently-issued tokens (matching log-file retention).

The predicate keys solely on expiry-plus-grace, NOT on ``used_at``:
``ADMIN_ACCESS`` links are reusable within their validity window even after
first use, so ``used_at`` is not a safe "done" signal.

``order_id`` and ``booking_id`` are nullable FKs with no cascade, and nothing
references ``magic_links`` as a parent, so deleting rows is safe.

Usage:
    python -m scripts.prune_magic_links --dry-run                # Preview
    python -m scripts.prune_magic_links                          # Execute (grace 30d)
    python -m scripts.prune_magic_links --grace-days 60          # Custom grace window
"""

import argparse
import logging
from datetime import UTC, datetime, timedelta

from app.core.database import SessionLocal
from app.models.magic_link import MagicLink
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def prune_magic_links(db: Session, *, grace_days: int = 30, dry_run: bool = False) -> int:
    """Delete magic-link rows expired longer than ``grace_days`` ago.

    The cutoff is a timezone-aware UTC datetime to match the ``expires_at``
    ``timestamptz`` column. Returns the number of rows deleted (or, in dry-run
    mode, the number that would be deleted).
    """
    cutoff = datetime.now(UTC) - timedelta(days=grace_days)
    query = db.query(MagicLink).filter(MagicLink.expires_at < cutoff)
    count = query.count()

    if dry_run:
        logger.info(
            "DRY RUN: %d magic-link row(s) expired before %s would be deleted.",
            count,
            cutoff,
        )
        db.rollback()
        return count

    query.delete(synchronize_session=False)
    db.commit()
    logger.info(
        "Deleted %d magic-link row(s) expired before %s.",
        count,
        cutoff,
    )
    return count


def main():
    parser = argparse.ArgumentParser(description="Prune long-expired magic-link rows")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the count without deleting",
    )
    parser.add_argument(
        "--grace-days",
        type=int,
        default=30,
        help="Delete rows expired more than this many days ago (default: 30)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        prune_magic_links(db, grace_days=args.grace_days, dry_run=args.dry_run)
    finally:
        db.close()


if __name__ == "__main__":
    main()
