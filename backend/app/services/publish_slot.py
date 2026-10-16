"""Pick an article's publish instant when the pipeline finishes it (#558).

The driver's ``SET_PUBLISH_DATE`` step used to carry a date fixed at planning
time (day+1 under daily cadence), but with the operator waits an article takes
several days, so that date was already in the past when the step ran. The
publish endpoint now picks the slot itself when called without
``published_at``; this module is that rule.

The rule
--------
* A slot is **12:00 UTC** (7am Panama; Panama is UTC-5 with no DST).
* The earliest candidate is the **next Panama calendar day** after "now".
* **One series per day.** A day is taken when any article *outside* this
  article's series (another series' member or a standalone article) has a
  ``published_at`` that falls on that Panama calendar day — whatever its
  status or time of day, so a manually published article blocks its day too.
  The search walks forward a day at a time until it finds a free one.
* **One date per series, and replays converge (#495).** When the article
  already has a ``published_at`` it is kept; otherwise, when another member of
  its series already has one (an earlier sibling's ``SET_PUBLISH_DATE``), that
  instant is reused. Only the first member to publish picks a fresh slot.

Series membership is ``Article.series_parent_id``: a series is the parent
(``series_parent_id IS NULL`` with children) plus every article whose
``series_parent_id`` points at it.

Concurrency is not guarded: the driver is a single serial loop, so two series
never pick a slot at the same instant.
"""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.article import Article

# Panama is UTC-5 all year.
PANAMA_OFFSET = timedelta(hours=-5)
# 12:00 UTC = 7am Panama.
SLOT_TIME = time(12, 0, tzinfo=UTC)


def _panama_day(instant: datetime) -> date:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return (instant.astimezone(UTC) + PANAMA_OFFSET).date()


def series_member_ids(db: Session, article: Article) -> list[int]:
    """Ids of every article in ``article``'s series (just itself if standalone)."""
    root_id = article.series_parent_id or article.id
    rows = (
        db.query(Article.id)
        .filter((Article.id == root_id) | (Article.series_parent_id == root_id))
        .all()
    )
    return [row[0] for row in rows]


def pick_publish_slot(db: Session, article: Article, now: datetime | None = None) -> datetime:
    """Return the instant ``article`` should publish at (see module docstring)."""
    if article.published_at is not None:
        return article.published_at

    member_ids = series_member_ids(db, article)
    sibling_dates = [
        row[0]
        for row in db.query(Article.published_at)
        .filter(Article.id.in_(member_ids), Article.published_at.isnot(None))
        .all()
    ]
    if sibling_dates:
        return min(sibling_dates)

    now = now or datetime.now(UTC)
    candidate = _panama_day(now) + timedelta(days=1)
    # Anything that could fall on the candidate Panama day or later.
    window_start = datetime.combine(candidate, time(0, 0), tzinfo=UTC) - PANAMA_OFFSET
    taken = {
        _panama_day(row[0])
        for row in db.query(Article.published_at)
        .filter(
            Article.published_at.isnot(None),
            Article.published_at >= window_start,
            Article.id.notin_(member_ids),
        )
        .all()
    }
    while candidate in taken:
        candidate += timedelta(days=1)
    return datetime.combine(candidate, SLOT_TIME)
