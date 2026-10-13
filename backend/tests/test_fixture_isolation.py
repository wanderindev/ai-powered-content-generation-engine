"""Regression guards for the `test_db` fixture's isolation contract (#205).

The fixture commits for real per test and resets the database with
``TRUNCATE ... RESTART IDENTITY CASCADE`` at teardown. These tests pin the three
behaviors that the previous single-outer-transaction + rollback fixture got
wrong, so a future fixture change that regresses any of them fails loudly here.
"""

from app.models.category import Taxonomy


def test_committed_rows_survive_a_later_rollback(test_db, taxonomy_factory):
    """#205: a ``rollback()`` after a commit must not wipe committed rows.

    The old fixture shared one never-committed connection transaction, so a
    service-layer ``db.rollback()`` discarded the test's own seeded rows. With
    real per-test commits, a rollback only drops uncommitted work.
    """
    taxonomy_factory(name="Committed Seed")

    # Uncommitted work a service would discard on error:
    test_db.add(Taxonomy(name="Doomed Work", description="rolled back"))
    test_db.rollback()

    names = [t.name for t in test_db.query(Taxonomy).all()]
    assert names == ["Committed Seed"]


# The two tests below are a pair: each creates the first taxonomy of its test
# and asserts it gets id == 1. They pass together only if RESTART IDENTITY
# resets the sequence between tests (#233). Under the old fixture the sequence
# was non-transactional and climbed across the rollback, so the second test
# would have seen id == 2.
def test_identity_sequence_resets_between_tests_a(taxonomy_factory):
    """#233: identity sequence starts fresh each test (part A)."""
    assert taxonomy_factory(name="First A").id == 1


def test_identity_sequence_resets_between_tests_b(taxonomy_factory):
    """#233: identity sequence starts fresh each test (part B)."""
    assert taxonomy_factory(name="First B").id == 1


def test_updated_at_advances_across_separate_transactions(test_db, article_factory):
    """#140: ``onupdate=func.now()`` is observable because the insert and the
    update happen in separate committed transactions, so ``now()`` advances
    (it was frozen at transaction start under the single-transaction fixture).
    """
    article = article_factory(title="Original")
    created = article.created_at
    original_updated = article.updated_at

    article.title = "Changed"
    test_db.commit()  # separate transaction -> now() advances
    test_db.refresh(article)

    assert article.updated_at > original_updated
    assert article.created_at == created  # insert timestamp unchanged
