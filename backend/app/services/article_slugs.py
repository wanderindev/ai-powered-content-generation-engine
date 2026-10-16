"""Keep the persisted article slug columns in sync with the titles (#474).

Slugs used to be Python properties recomputed from the title on every read, which
made them unmatchable in SQL and let a title edit silently move a live URL. They
are now columns, and this module owns the only edit-time write path.

Policy:

- **Frozen once live.** An article is frozen when it is ``APPROVED`` *or* has a
  ``published_at``. ``/articles/by-slug`` serves any APPROVED article regardless
  of ``published_at``, so an APPROVED article's URL is already reachable; and
  unpublishing clears ``published_at`` while leaving the status APPROVED, so
  freezing on APPROVED keeps a URL stable across unpublish/republish.
- **Collisions fail loudly.** No suffix de-duplication: two articles that
  slugify to the same string is an editorial problem, not something to paper
  over with ``-2``.
"""

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.enums import ContentStatus
from app.utils.slug import generate_slug

__all__ = ["SlugCollisionError", "sync_article_slugs"]


class SlugCollisionError(ValueError):
    """A recomputed slug is empty or already belongs to another article."""


def _is_frozen(article: Article) -> bool:
    return article.status == ContentStatus.APPROVED.value or article.published_at is not None


def sync_article_slugs(db: Session, article: Article) -> None:
    """Recompute ``slug`` / ``slug_es`` from the titles unless the article is live.

    Raises:
        SlugCollisionError: the English title slugifies to an empty string, or one
            of the new slugs is already in use by a different article (in either
            language column — a Spanish slug that equals another article's English
            slug is a real collision, because the lookup matches both columns).
    """
    if _is_frozen(article):
        return

    new_slug = generate_slug(article.title or "")
    if not new_slug:
        raise SlugCollisionError(f"Title {article.title!r} does not produce a usable slug")
    new_slug_es = generate_slug(article.title_es) if article.title_es else None

    candidates = {new_slug}
    if new_slug_es:
        candidates.add(new_slug_es)

    query = db.query(Article.id, Article.slug, Article.slug_es).filter(
        or_(Article.slug.in_(candidates), Article.slug_es.in_(candidates))
    )
    if article.id is not None:
        query = query.filter(Article.id != article.id)

    # Checked before assignment so an autoflush cannot turn a reportable
    # collision into a raw IntegrityError.
    for other_id, other_slug, other_slug_es in query.all():
        clashing = candidates.intersection({other_slug, other_slug_es} - {None})
        if clashing:
            raise SlugCollisionError(
                f"Slug '{sorted(clashing)[0]}' already in use by article {other_id}"
            )

    article.slug = new_slug
    article.slug_es = new_slug_es
