"""Tests for the editorial-calendar endpoint (articles + Instagram posts)."""

from datetime import UTC, datetime, timedelta

from app.models import ArticleSuggestion, Research
from app.models.article import Article
from app.models.instagram_post import InstagramPost

URL = "/api/v1/admin/dashboard/editorial-calendar"


def _month_url(dt: datetime) -> str:
    """Query the month the timestamp actually falls in.

    Seeds are relative to ``now`` (the live/future distinction needs real time),
    so near the UTC month rollover ``now ± a few hours`` can land in two
    different months — asking for ``now``'s month for everything made the suite
    fail for a few hours around each month boundary (#488).
    """
    return f"{URL}?year={dt.year}&month={dt.month}"


def _seed_article(test_db, category_factory, *, title, published_at, status="APPROVED"):
    category = category_factory()
    suggestion = ArticleSuggestion(
        title=title,
        category_id=category.id,
        status="APPROVED",
        main_topic=title,
        sub_topics=[],
        point_of_view="narrative",
    )
    test_db.add(suggestion)
    test_db.flush()
    research = Research(suggestion_id=suggestion.id, content="x", status="APPROVED")
    test_db.add(research)
    test_db.flush()
    article = Article(
        research_id=research.id,
        category_id=category.id,
        title=title,
        status=status,
        published_at=published_at,
    )
    test_db.add(article)
    test_db.commit()
    return article


def _seed_ig(test_db, **overrides):
    fields = {
        "post_type": "SABIAS_QUE",
        "status": "APPROVED",
        "title": "IG post",
        "image_url": "https://cdn.example.com/i.jpg",
    }
    fields.update(overrides)
    post = InstagramPost(**fields)
    test_db.add(post)
    test_db.commit()
    return post


class TestEditorialCalendar:
    def test_month_window_and_states(self, test_db, test_client, admin_token, category_factory):
        now = datetime.now(UTC)
        live = _seed_article(
            test_db, category_factory, title="Live", published_at=now - timedelta(hours=1)
        )
        future = _seed_article(
            test_db, category_factory, title="Future", published_at=now + timedelta(hours=6)
        )
        scheduled = _seed_ig(
            test_db, title="Queued", scheduled_at=now + timedelta(hours=3)
        )
        published = _seed_ig(
            test_db,
            title="Out",
            scheduled_at=now - timedelta(hours=4),
            published_at=now - timedelta(hours=3),
            ig_media_id="M1",
        )
        failed = _seed_ig(
            test_db,
            title="Broken",
            scheduled_at=now - timedelta(hours=2),
            publish_error="token expired",
        )

        def article_row(a):
            resp = test_client.get(_month_url(a.published_at))
            assert resp.status_code == 200
            return {r["id"]: r for r in resp.json()["articles"]}[a.id]

        def ig_row(p):
            when = p.published_at or p.scheduled_at
            resp = test_client.get(_month_url(when))
            assert resp.status_code == 200
            return {r["id"]: r for r in resp.json()["instagram_posts"]}[p.id]

        assert article_row(live)["is_live"] is True
        assert article_row(future)["is_live"] is False

        assert ig_row(scheduled)["state"] == "scheduled"
        assert ig_row(published)["state"] == "published"
        # A published post keys off published_at, not its original scheduled slot.
        assert ig_row(published)["when"] != ig_row(scheduled)["when"]
        assert ig_row(failed)["state"] == "failed"

    def test_other_months_are_excluded(self, test_db, test_client, admin_token, category_factory):
        now = datetime.now(UTC)
        _seed_article(
            test_db, category_factory, title="Old", published_at=now - timedelta(days=400)
        )
        _seed_ig(test_db, title="Far", scheduled_at=now + timedelta(days=400))
        _seed_ig(test_db, title="Never", scheduled_at=None)  # unscheduled draft — no date

        resp = test_client.get(f"{URL}?year={now.year}&month={now.month}")
        body = resp.json()
        assert body["articles"] == []
        assert body["instagram_posts"] == []

    def test_unapproved_article_with_date_is_not_live(
        self, test_db, test_client, admin_token, category_factory
    ):
        now = datetime.now(UTC)
        pending = _seed_article(
            test_db,
            category_factory,
            title="Pending",
            published_at=now - timedelta(hours=1),
            status="PENDING",
        )
        resp = test_client.get(_month_url(pending.published_at))
        articles = {a["id"]: a for a in resp.json()["articles"]}
        assert articles[pending.id]["is_live"] is False

    def test_requires_admin(self, test_client):
        now = datetime.now(UTC)
        assert test_client.get(f"{URL}?year={now.year}&month={now.month}").status_code == 401
