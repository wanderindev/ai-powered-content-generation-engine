"""Publish date picked when the series is finished, not at planning (#558)."""

from datetime import UTC, datetime, timedelta

import pytest
from app.models.article import Article
from app.models.instagram_post import InstagramPost
from app.models.media import Media
from app.services.instagram_scheduler import follow_article_publish_date
from app.services.publish_slot import pick_publish_slot
from app.services.series_sections import ABOUT_MARKER_EN

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # 10:00 Panama


def _at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


# --- The rule (service, deterministic "now") --------------------------------


def test_next_day_noon_utc_when_nothing_is_scheduled(test_db, article_factory):
    article = article_factory(published_at=None)
    assert pick_publish_slot(test_db, article, now=NOW) == _at(6)


def test_next_day_is_the_panama_day(test_db, article_factory):
    """At 22:00 Panama on the 5th it is already the 6th in UTC; the slot is
    still the 6th (the next Panama day), not the 7th."""
    article = article_factory(published_at=None)
    late_evening = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)
    assert pick_publish_slot(test_db, article, now=late_evening) == _at(6)


def test_taken_day_moves_to_the_next_free_day(test_db, article_factory):
    article = article_factory(published_at=None)
    article_factory(title="Other standalone", published_at=_at(6))
    # A manual publish at 22:00 Panama on the 7th (03:00 UTC on the 8th)
    # takes the 7th, not the 8th.
    article_factory(title="Other manual", published_at=_at(8, hour=3))
    assert pick_publish_slot(test_db, article, now=NOW) == _at(8)


def test_existing_date_is_kept(test_db, article_factory):
    article = article_factory(published_at=_at(20))
    article_factory(title="Other", published_at=_at(6))
    assert pick_publish_slot(test_db, article, now=NOW) == _at(20)


def test_series_member_reuses_sibling_date(test_db, article_factory):
    parent = article_factory(title="Parent", published_at=_at(9))
    child = article_factory(title="Child", series_parent=parent, published_at=None)
    other = article_factory(title="Other child", series_parent=parent, published_at=None)
    # Even though another series took the 9th later on, the series stays together.
    article_factory(title="Another series", published_at=_at(9, hour=13))
    assert pick_publish_slot(test_db, child, now=NOW) == _at(9)
    assert pick_publish_slot(test_db, other, now=NOW) == _at(9)


# --- The endpoint ------------------------------------------------------------


def _seed_media(test_db, filename: str) -> Media:
    now = datetime.now(UTC)
    media = Media(
        filename=filename,
        original_filename=filename,
        file_path=f"/static/images/articles/{filename}",
        file_size=1234,
        mime_type="image/jpeg",
        media_type="IMAGE",
        source="GENERATED",
        title="Test media",
        created_at=now,
        updated_at=now,
    )
    test_db.add(media)
    test_db.commit()
    return media


def _ready(test_db, article_factory, **kwargs):
    article = article_factory(published_at=None, **kwargs)
    article.content_es = "spanish"
    article.feature_image_id = _seed_media(test_db, filename=f"a{article.id}.jpg").id
    if article.series_parent_id is not None:
        article.content = f"{ABOUT_MARKER_EN}\n\nbody"
    test_db.commit()
    return article


def _publish(test_client, article_id, **kwargs):
    return test_client.patch(f"/api/v1/admin/dashboard/articles/{article_id}/publish", **kwargs)


def test_publish_without_date_picks_the_slot_at_run_time(
    test_client, admin_token, test_db, article_factory
):
    article = _ready(test_db, article_factory)
    # Tomorrow (Panama) is taken by another article; the slot moves a day.
    today_panama = (datetime.now(UTC) - timedelta(hours=5)).date()
    tomorrow = datetime.combine(today_panama + timedelta(days=1), datetime.min.time(), UTC)
    article_factory(title="Other", published_at=tomorrow + timedelta(hours=12))

    response = _publish(test_client, article.id)
    assert response.status_code == 200
    test_db.refresh(article)
    assert article.published_at == tomorrow + timedelta(days=1, hours=12)

    # Replay converges on the same date (#495), with or without an empty body.
    assert _publish(test_client, article.id, json={}).status_code == 200
    test_db.refresh(article)
    assert article.published_at == tomorrow + timedelta(days=1, hours=12)


def test_publish_without_date_shares_the_series_date(
    test_client, admin_token, test_db, article_factory
):
    parent = _ready(test_db, article_factory, title="Parent")
    parent.content = f"{ABOUT_MARKER_EN}\n\nbody"
    test_db.commit()
    child = _ready(test_db, article_factory, title="Child", series_parent=parent)

    assert _publish(test_client, parent.id).status_code == 200
    assert _publish(test_client, child.id).status_code == 200
    test_db.refresh(parent)
    test_db.refresh(child)
    assert parent.published_at is not None
    assert child.published_at == parent.published_at
    assert parent.published_at.hour == 12


def test_publish_with_explicit_date_is_unchanged(
    test_client, admin_token, test_db, article_factory
):
    """The admin UI's manual date picking keeps working as before."""
    article = _ready(test_db, article_factory)
    response = _publish(test_client, article.id, json={"published_at": "2026-01-01T00:00:00Z"})
    assert response.status_code == 200
    test_db.refresh(article)
    assert article.published_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_publish_without_date_still_gated(test_client, admin_token, test_db, article_factory):
    article = article_factory(published_at=None)
    article.content_es = "spanish"
    test_db.commit()
    response = _publish(test_client, article.id)
    assert response.status_code == 400
    assert "feature image" in response.json()["detail"]
    test_db.refresh(article)
    assert article.published_at is None


# --- The article's Instagram promo follows a re-date (#588) ------------------

OLD_DATE = datetime(2027, 3, 10, 12, 0, tzinfo=UTC)
DERIVED = datetime(2027, 3, 11, 17, 0, tzinfo=UTC)  # day after, 17:00 UTC
EXPLICIT = datetime(2027, 4, 1, 15, 0, tzinfo=UTC)


def _dated(test_db, article_factory):
    article = _ready(test_db, article_factory)
    article.published_at = OLD_DATE
    test_db.commit()
    return article


def _promo(test_db, article, **overrides):
    fields = {
        "post_type": "ARTICLE_PROMO",
        "status": "APPROVED",
        "title": "Promo",
        "caption": "c",
        "article_id": article.id,
        "image_url": "https://cdn.example.com/p.jpg",
        "scheduled_at": DERIVED,
    }
    fields.update(overrides)
    post = InstagramPost(**fields)
    test_db.add(post)
    test_db.commit()
    return post


def test_derived_promo_follows_an_explicit_re_date(
    test_client, admin_token, test_db, article_factory
):
    article = _dated(test_db, article_factory)
    derived = _promo(test_db, article)
    explicit = _promo(test_db, article, scheduled_at=EXPLICIT)

    response = _publish(test_client, article.id, json={"published_at": "2027-03-20T12:00:00Z"})
    assert response.status_code == 200
    test_db.expire_all()
    assert test_db.get(InstagramPost, derived.id).scheduled_at == datetime(
        2027, 3, 21, 17, 0, tzinfo=UTC
    )
    assert test_db.get(InstagramPost, explicit.id).scheduled_at == EXPLICIT


@pytest.mark.parametrize(
    "overrides",
    [{"publish_error": "x"}, {"publishing_started_at": datetime.now(UTC)}],
    ids=["failed", "claimed"],
)
def test_failed_or_claimed_promo_does_not_move(
    test_client, admin_token, test_db, article_factory, overrides
):
    article = _dated(test_db, article_factory)
    promo = _promo(test_db, article, **overrides)

    response = _publish(test_client, article.id, json={"published_at": "2027-03-20T12:00:00Z"})
    assert response.status_code == 200
    test_db.expire_all()
    assert test_db.get(InstagramPost, promo.id).scheduled_at == DERIVED


def test_driver_replay_keeps_the_derived_promo(
    test_client, admin_token, test_db, article_factory
):
    article = _dated(test_db, article_factory)
    promo = _promo(test_db, article)

    assert _publish(test_client, article.id).status_code == 200
    test_db.expire_all()
    assert test_db.get(Article, article.id).published_at == OLD_DATE
    assert test_db.get(InstagramPost, promo.id).scheduled_at == DERIVED


def test_unpublish_unschedules_the_derived_promo_only(
    test_client, admin_token, test_db, article_factory
):
    article = _dated(test_db, article_factory)
    derived = _promo(test_db, article)
    explicit = _promo(test_db, article, scheduled_at=EXPLICIT)

    response = test_client.patch(f"/api/v1/admin/dashboard/articles/{article.id}/unpublish")
    assert response.status_code == 200
    test_db.expire_all()
    moved = test_db.get(InstagramPost, derived.id)
    assert moved.scheduled_at is None
    assert moved.derived_state == "APPROVED"
    assert test_db.get(InstagramPost, explicit.id).scheduled_at == EXPLICIT


def test_follow_is_a_no_op_without_an_old_date(test_db, article_factory):
    article = _ready(test_db, article_factory)
    _promo(test_db, article)
    assert follow_article_publish_date(test_db, article.id, None, OLD_DATE) == 0
