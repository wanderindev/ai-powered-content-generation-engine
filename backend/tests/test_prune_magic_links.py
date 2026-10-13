"""Tests for the magic-link pruning script (#65).

Covers ``scripts.prune_magic_links.prune_magic_links``:
- Rows expired beyond the grace window are deleted.
- Rows expired but within the grace window are preserved.
- Still-valid (future-expiry) rows are preserved.
- ``dry_run=True`` reports the count but deletes nothing.
- ``used_at`` does NOT make a within-grace row eligible (the predicate keys on
  expiry-plus-grace only, since ADMIN_ACCESS links are reusable).
"""

from datetime import UTC, datetime, timedelta

from app.models import MagicLink, MagicLinkAction
from scripts.prune_magic_links import prune_magic_links


def _make_link(db, **kwargs) -> MagicLink:
    defaults = dict(
        token="tok-default",
        action=MagicLinkAction.ADMIN_ACCESS.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    defaults.update(kwargs)
    link = MagicLink(**defaults)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def test_long_expired_row_is_deleted(test_db):
    _make_link(
        test_db,
        token="tok-long-expired",
        expires_at=datetime.now(UTC) - timedelta(days=45),
    )

    deleted = prune_magic_links(test_db, grace_days=30)

    assert deleted == 1
    assert test_db.query(MagicLink).count() == 0


def test_expired_within_grace_is_kept(test_db):
    _make_link(
        test_db,
        token="tok-recent-expired",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )

    deleted = prune_magic_links(test_db, grace_days=30)

    assert deleted == 0
    assert test_db.query(MagicLink).count() == 1


def test_future_expiry_row_is_kept(test_db):
    _make_link(
        test_db,
        token="tok-future",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    deleted = prune_magic_links(test_db, grace_days=30)

    assert deleted == 0
    assert test_db.query(MagicLink).count() == 1


def test_dry_run_reports_count_but_deletes_nothing(test_db):
    _make_link(
        test_db,
        token="tok-dry-run",
        expires_at=datetime.now(UTC) - timedelta(days=45),
    )

    would_delete = prune_magic_links(test_db, grace_days=30, dry_run=True)

    assert would_delete == 1
    # Re-query confirms the row is still present.
    assert test_db.query(MagicLink).count() == 1


def test_used_within_grace_row_is_kept(test_db):
    # A used ADMIN_ACCESS link whose expiry is still within the grace window
    # must NOT be deleted: the predicate keys on expiry+grace, not used_at.
    _make_link(
        test_db,
        token="tok-used-recent",
        expires_at=datetime.now(UTC) - timedelta(days=1),
        used_at=datetime.now(UTC) - timedelta(days=1),
    )

    deleted = prune_magic_links(test_db, grace_days=30)

    assert deleted == 0
    assert test_db.query(MagicLink).count() == 1
