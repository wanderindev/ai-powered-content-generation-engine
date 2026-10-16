"""Semantic duplicate-topic detection for article suggestions (#630, epic #618).

Two steps, both run by :mod:`app.services.suggestion_generation` only while
``SUGGESTION_DEDUP_ENABLED`` is on (off = generation is byte-for-byte the
pre-#630 code path: same prompt, same calls, same rows):

1. **Steer** (:func:`covered_article_titles`): one Voyage *query* embedding of
   the category (name + description), then ``retrieval.search`` in vector mode,
   ``internal`` visibility, ARTICLE chunks only. The titles of the nearest
   articles (at most ``suggestion_dedup_steer_titles``) go into the prompt as
   "already covered". Today's prompt lists only same-category *suggestion*
   titles, so an article from another category on the same topic was invisible.
2. **Flag** (:func:`flag_duplicates`): each new suggestion's title + main
   topic is embedded as a *document* and compared with every article centroid
   (``related.nearest_articles``, the #629 centroid, internal visibility) and
   every older suggestion's stored vector. The closest match at cosine
   similarity >= ``suggestion_dedup_min_similarity`` is written to
   ``duplicate_article_id`` / ``duplicate_suggestion_id`` +
   ``duplicate_similarity``. **Advisory only**: nothing is blocked or deleted.

**Suggestion vectors are stored on the row** (``article_suggestions.embedding``,
migration 0045), not re-embedded each run and not added to knowledge_chunks:
the new suggestions have to be embedded for the article comparison anyway, so
storing them costs nothing, and older rows are filled lazily — each run also
embeds up to :data:`BACKFILL_CAP` older suggestions that have no vector yet, in
the same Voyage request. Steady-state cost is one embedding per new suggestion;
no backfill script or knowledge_chunks CHECK change is needed. Document input
type on both sides keeps suggestion-to-suggestion and suggestion-to-centroid
comparisons symmetric.

**Failure mode.** Every step degrades: disabled embeddings, a Voyage failure
or any retrieval / SQL error means "no steer" / "no flag", logged once, and
suggestion generation carries on. **Connection pool**: each function commits
before its Voyage call so no pooled connection is pinned through it.
"""

import logging

from sqlalchemy import or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.enums import KnowledgeSourceType
from app.services import embeddings
from app.services.knowledge import related, retrieval

logger = logging.getLogger(__name__)

FEATURE = "suggestion_dedup"
# Older suggestions without a stored vector embedded per run (lazy backfill).
BACKFILL_CAP = 200

_TABLE = ArticleSuggestion.__table__


def suggestion_text(title: str, main_topic: str | None) -> str:
    """The text a suggestion is embedded from: title, then main topic."""
    return f"{title}\n\n{main_topic}" if main_topic else title


def covered_article_titles(
    db: Session,
    *,
    category_name: str,
    category_description: str | None,
    limit: int,
) -> list[str]:
    """Titles of the articles nearest the category, best first; ``[]`` on any failure."""
    if limit < 1:
        return []
    query = f"{category_name}: {category_description}" if category_description else category_name
    # Release the connection the caller's reads pinned before the Voyage call.
    db.commit()
    try:
        vector = embeddings.embed_query(query, feature=FEATURE)
    except embeddings.EmbeddingsDisabledError:
        logger.info("suggestion dedup: embeddings disabled; no steer")
        return []
    except Exception:
        logger.warning("suggestion dedup: steer embedding failed; no steer", exc_info=True)
        return []
    try:
        result = retrieval.search(
            db,
            query,
            mode="vector",
            k=limit,
            visibility="internal",
            source_types=[KnowledgeSourceType.ARTICLE.value],
            query_vector=vector,
        )
        titles: list[str] = []
        for hit in result.results:
            if hit.title and hit.title not in titles:
                titles.append(hit.title)
        return titles[:limit]
    except Exception:
        db.rollback()
        logger.warning("suggestion dedup: steer retrieval failed; no steer", exc_info=True)
        return []
    finally:
        db.commit()


def _nearest_suggestion(
    db: Session, vector: list[float], exclude_ids: list[int]
) -> tuple[int, float] | None:
    cols = _TABLE.c
    distance = cols.embedding.cosine_distance(vector)
    row = db.execute(
        select(cols.id, distance.label("distance"))
        .where(cols.embedding.is_not(None), cols.id.not_in(exclude_ids))
        .order_by(distance, cols.id)
        .limit(1)
    ).first()
    return (row.id, 1.0 - float(row.distance)) if row else None


def flag_duplicates(db: Session, suggestions: list[ArticleSuggestion]) -> None:
    """Embed, store and flag ``suggestions`` (already committed); never raises."""
    if not suggestions:
        return
    settings = get_settings()
    floor = settings.suggestion_dedup_min_similarity
    new_ids = [s.id for s in suggestions]
    new_texts = [suggestion_text(s.title, s.main_topic) for s in suggestions]
    try:
        cols = _TABLE.c
        backfill = db.execute(
            select(cols.id, cols.title, cols.main_topic)
            .where(cols.embedding.is_(None), cols.id.not_in(new_ids))
            .order_by(cols.id)
            .limit(BACKFILL_CAP)
        ).all()
        db.commit()  # no connection held through the Voyage call
    except Exception:
        db.rollback()
        logger.warning("suggestion dedup: reading suggestions failed; no flag", exc_info=True)
        return

    backfill_ids = [row.id for row in backfill]
    texts = new_texts + [suggestion_text(row.title, row.main_topic) for row in backfill]
    try:
        vectors = embeddings.embed_documents(texts, feature=FEATURE)
    except embeddings.EmbeddingsDisabledError:
        logger.info("suggestion dedup: embeddings disabled; no flag")
        return
    except Exception:
        logger.warning("suggestion dedup: embedding failed; no flag", exc_info=True)
        return

    try:
        for sid, vector in zip(new_ids + backfill_ids, vectors, strict=True):
            # updated_at = itself suppresses the column's onupdate=now(): storing
            # a vector (the lazy backfill especially) is not a user edit.
            db.execute(
                update(_TABLE)
                .where(_TABLE.c.id == sid)
                .values(embedding=vector, updated_at=_TABLE.c.updated_at)
            )
        db.commit()

        for sid, vector in zip(new_ids, vectors[: len(new_ids)], strict=True):
            best: tuple[str, int, float] | None = None
            hits = related.nearest_articles(db, vector, k=1, visibility="internal")
            if hits:
                best = ("article", hits[0].article_id, hits[0].similarity)
            sugg = _nearest_suggestion(db, vector, new_ids)
            if sugg and (best is None or sugg[1] > best[2]):
                best = ("suggestion", sugg[0], sugg[1])
            if best is None or best[2] < floor:
                continue
            kind, match_id, similarity = best
            # Core, not ORM attributes: the columns are off the mapper (model doc).
            db.execute(
                update(_TABLE)
                .where(_TABLE.c.id == sid)
                .values(
                    duplicate_article_id=match_id if kind == "article" else None,
                    duplicate_suggestion_id=match_id if kind == "suggestion" else None,
                    duplicate_similarity=similarity,
                )
            )
            logger.info(
                "suggestion dedup: suggestion %s possible duplicate of %s %s (%.3f)",
                sid,
                kind,
                match_id,
                similarity,
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("suggestion dedup: matching failed; no flag", exc_info=True)


def clear_embedding(db: Session, suggestion_id: int) -> None:
    """Drop a suggestion's stored vector and possible-duplicate flag.

    Called when its title / main topic changed: the vector and the flag both
    described the old text. The next flag-on run re-embeds it through the lazy
    backfill, but does not re-flag it (only new suggestions are flagged). The
    pending ORM changes are flushed first, outside the guard, so an unrelated
    flush failure raises with its real cause instead of being logged as a
    failed clear. The UPDATE runs in a SAVEPOINT so a database that has not run
    migration 0045 yet (no column) leaves the caller's transaction intact.
    """
    db.flush()
    try:
        with db.begin_nested():
            db.execute(
                update(_TABLE)
                .where(_TABLE.c.id == suggestion_id)
                .values(
                    embedding=None,
                    duplicate_article_id=None,
                    duplicate_suggestion_id=None,
                    duplicate_similarity=None,
                )
            )
    except DBAPIError:
        logger.warning("suggestion dedup: could not clear the stored vector", exc_info=True)


def duplicate_fields(db: Session, suggestion_ids: list[int]) -> dict[int, dict]:
    """The stored duplicate match per suggestion id, with the match's title / slug.

    Core over ``__table__.c`` (the columns are off the mapper). Inside a
    SAVEPOINT: against a database without migration 0045 it returns ``{}``
    (callers show no flag) and leaves the caller's transaction intact.
    """
    if not suggestion_ids:
        return {}
    cols = _TABLE.c
    try:
        with db.begin_nested():
            rows = db.execute(
                select(
                    cols.id,
                    cols.duplicate_article_id,
                    cols.duplicate_suggestion_id,
                    cols.duplicate_similarity,
                ).where(
                    cols.id.in_(suggestion_ids),
                    or_(
                        cols.duplicate_article_id.is_not(None),
                        cols.duplicate_suggestion_id.is_not(None),
                    ),
                )
            ).all()
    except DBAPIError:
        logger.warning("suggestion dedup: duplicate fields unavailable", exc_info=True)
        return {}
    article_ids = {r.duplicate_article_id for r in rows if r.duplicate_article_id}
    match_ids = {r.duplicate_suggestion_id for r in rows if r.duplicate_suggestion_id}
    articles = (
        {
            a.id: a
            for a in db.execute(
                select(Article.id, Article.title, Article.slug).where(Article.id.in_(article_ids))
            )
        }
        if article_ids
        else {}
    )
    titles = (
        dict(db.execute(select(cols.id, cols.title).where(cols.id.in_(match_ids))).all())
        if match_ids
        else {}
    )
    out = {}
    for r in rows:
        art = articles.get(r.duplicate_article_id)
        out[r.id] = {
            "duplicate_article_id": r.duplicate_article_id,
            "duplicate_article_title": art.title if art else None,
            "duplicate_article_slug": art.slug if art else None,
            "duplicate_suggestion_id": r.duplicate_suggestion_id,
            "duplicate_suggestion_title": titles.get(r.duplicate_suggestion_id),
            "duplicate_similarity": r.duplicate_similarity,
        }
    return out


__all__ = [
    "BACKFILL_CAP",
    "clear_embedding",
    "covered_article_titles",
    "duplicate_fields",
    "flag_duplicates",
    "suggestion_text",
]
