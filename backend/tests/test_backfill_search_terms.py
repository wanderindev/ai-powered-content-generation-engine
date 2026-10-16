"""Tests for scripts.backfill_search_terms (#589) — generation mocked."""

from unittest.mock import patch

from app.models.media import Media
from scripts.backfill_search_terms import backfill

GEN = "scripts.backfill_search_terms.generate_search_metadata"


def _seed(test_db, n=1, **overrides):
    rows = []
    for i in range(n):
        values = {
            "filename": f"f{i}.jpg",
            "original_filename": f"f{i}.jpg",
            "file_path": f"https://cdn.example.com/f{i}.jpg",
            "file_size": 10,
            "mime_type": "image/jpeg",
            "media_type": "IMAGE",
            "source": "LOCAL",
            "title": f"Title {i}",
            "alt_text": f"Title {i}",
        }
        values.update(overrides)
        m = Media(**values)
        test_db.add(m)
        rows.append(m)
    test_db.commit()
    return [m.id for m in rows]


def _answer_all(items, *, feature):
    assert feature == "media_search_terms_backfill"
    return {i.id: {"search_terms": f"terms {i.id}", "alt_text": f"alt {i.id}"} for i in items}


def test_raising_batch_leaves_rows_null_and_terminates(test_db):
    ids = _seed(test_db, 3)
    with patch(GEN, side_effect=RuntimeError("boom")) as gen:
        assert backfill(test_db, batch_size=2) == 0
    assert gen.call_count == 2  # each row tried once, then skipped
    test_db.expire_all()
    for mid in ids:
        assert test_db.get(Media, mid).search_terms is None


def test_empty_string_rows_are_picked_up(test_db):
    (mid,) = _seed(test_db, 1, search_terms="")
    with patch(GEN, side_effect=_answer_all):
        assert backfill(test_db) == 1
    test_db.expire_all()
    row = test_db.get(Media, mid)
    assert row.search_terms == f"terms {mid}"
    assert row.alt_text == f"alt {mid}"  # default alt (== title) replaced


def test_omitted_id_does_not_loop_forever(test_db):
    ids = _seed(test_db, 2)

    def answer_first_only(items, *, feature):
        return {ids[0]: {"search_terms": "x", "alt_text": None}}

    with patch(GEN, side_effect=answer_first_only) as gen:
        assert backfill(test_db, batch_size=5) == 1
    assert gen.call_count == 1
    test_db.expire_all()
    assert test_db.get(Media, ids[1]).search_terms is None


def test_non_default_alt_text_is_preserved(test_db):
    (mid,) = _seed(test_db, 1, alt_text="Operator alt")
    with patch(GEN, side_effect=_answer_all):
        backfill(test_db)
    test_db.expire_all()
    row = test_db.get(Media, mid)
    assert row.search_terms == f"terms {mid}"
    assert row.alt_text == "Operator alt"


def test_dry_run_makes_progress_without_calling(test_db):
    _seed(test_db, 3)
    with patch(GEN) as gen:
        assert backfill(test_db, batch_size=2, dry_run=True) == 0
    gen.assert_not_called()
