"""Article publish / unpublish / approve: the one implementation (#642).

Three callers share these functions so there is no second copy of the rules:

* ``PATCH /admin/dashboard/articles/{id}/publish`` and ``.../unpublish``
  (``app/api/dashboard.py``; the agent driver's ``SET_PUBLISH_DATE`` uses
  the publish route);
* ``PATCH /admin/dashboard/{entity}/{id}/status`` (the status transition
  rules, for suggestions, research and articles);
* the MCP approval executor (``app/services/mcp_approvals.py``), which runs
  a publish / unpublish / approve request when an admin approves it.

None of them commits: the caller's commit writes the article (and the
Instagram promo that follows its date) together with whatever else the
caller records, so the MCP executor can run one inside a savepoint and
record ``FAILED`` with nothing half-applied. Failures are domain exceptions;
the routes map them to 404 / 400 with the same messages as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models.article import Article
from app.models.enums import ContentStatus
from app.services.instagram_scheduler import follow_article_publish_date
from app.services.publish_slot import pick_publish_slot
from app.services.series_sections import ABOUT_MARKER_EN

# Allowed status transitions of a suggestion, research or article.
VALID_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"APPROVED", "REJECTED"},
    "APPROVED": {"PENDING", "REJECTED"},
    "REJECTED": {"PENDING", "APPROVED"},
}


class ArticleNotFoundError(LookupError):
    """No article has the given id."""


class PublishPreconditionError(ValueError):
    """The article does not meet a publish precondition (message says which)."""


class InvalidStatusTransitionError(ValueError):
    """The requested status change is not in :data:`VALID_TRANSITIONS`."""


def _load(db: Session, article_id: int, *, with_series: bool = False) -> Article:
    query = db.query(Article)
    if with_series:
        query = query.options(joinedload(Article.series_children))
    article = query.filter(Article.id == article_id).first()
    if article is None:
        raise ArticleNotFoundError("Article not found")
    return article


def publish_blockers(article: Article) -> list[str]:
    """Every unmet publish precondition, in the order the publish checks them.

    Empty means :func:`publish_article` would succeed (barring a slug /
    database error). ``article.series_children`` is read, so load it.
    """
    blockers: list[str] = []
    if article.status != ContentStatus.APPROVED.value:
        blockers.append("Article must be APPROVED before publishing")
    if not article.content_es or not article.content_es.strip():
        blockers.append("Article must have Spanish content before publishing")
    if article.feature_image_id is None:
        blockers.append("Article must have a feature image before publishing")
    is_series = article.series_parent_id is not None or len(article.series_children) > 0
    if is_series and not (article.content and ABOUT_MARKER_EN in article.content):
        blockers.append("Series articles must have series sections before publishing")
    return blockers


def publish_article(db: Session, article_id: int, published_at: datetime | None = None) -> Article:
    """Set ``published_at`` on an article that meets every publish precondition.

    With no ``published_at`` the server picks it (#558): the article's own
    existing date, else a series sibling's, else the next free 12:00 UTC day
    (``app/services/publish_slot.py``), so a replay converges. A change of
    date moves the article's derived, still-scheduled Instagram promo with it
    (#588). Raises :class:`ArticleNotFoundError` or
    :class:`PublishPreconditionError` (the first unmet precondition) before
    writing anything. Does not commit.
    """
    article = _load(db, article_id, with_series=True)
    blockers = publish_blockers(article)
    if blockers:
        raise PublishPreconditionError(blockers[0])

    old_published_at = article.published_at
    if published_at is not None:
        article.published_at = published_at
    else:
        article.published_at = pick_publish_slot(db, article)
    follow_article_publish_date(db, article.id, old_published_at, article.published_at)
    return article


def unpublish_article(db: Session, article_id: int) -> Article:
    """Clear ``published_at`` and unschedule the derived Instagram promo (#588).

    Raises :class:`ArticleNotFoundError`. Does not commit.
    """
    article = _load(db, article_id)
    old_published_at = article.published_at
    article.published_at = None
    follow_article_publish_date(db, article.id, old_published_at, None)
    return article


def transition_status(record: Any, new_status: str) -> None:
    """Move a suggestion / research / article to ``new_status``.

    Validates against :data:`VALID_TRANSITIONS` (raises
    :class:`InvalidStatusTransitionError`) and keeps ``approved_at`` in step.
    Does not commit.
    """
    current_status = record.status
    if new_status not in VALID_TRANSITIONS.get(current_status, set()):
        raise InvalidStatusTransitionError(
            f"Invalid transition from {current_status} to {new_status}"
        )
    record.status = new_status
    if new_status == ContentStatus.APPROVED.value:
        record.approved_at = datetime.now(UTC)
    elif current_status == ContentStatus.APPROVED.value:
        record.approved_at = None


def approve_article(db: Session, article_id: int) -> Article:
    """Set an article to APPROVED (the status route's article transition).

    Raises :class:`ArticleNotFoundError` or
    :class:`InvalidStatusTransitionError` (e.g. already APPROVED). Does not
    commit.
    """
    article = _load(db, article_id)
    transition_status(article, ContentStatus.APPROVED.value)
    return article
