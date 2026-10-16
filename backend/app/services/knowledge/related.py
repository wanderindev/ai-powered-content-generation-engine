"""Related articles from stored chunk vectors (#629, epic #618).

:func:`find_related` ranks other articles by the cosine distance between
**article centroids**: the mean (pgvector ``avg``) of each article's embedded
``ARTICLE`` chunks, in both languages. It makes no embedding or rerank call, so
it costs nothing per request beyond SQL. The public endpoint
``GET /articles/{id}/related`` and MCP ``find_related`` (D3) both use it.

**Why centroids.** Replayed read-only against the prod index (2026-10-09, 75
public articles, k=4, the same exclusions), compared with best-chunk-pair
(``min`` distance over every chunk pair) and centroid-to-best-chunk:

============================  ==============  ==============  ========
method                        same category   max in-degree   p50 ms
============================  ==============  ==============  ========
centroid ↔ centroid           0.81            19              53
best chunk pair               0.78            29              508
centroid ↔ best chunk         0.81            32              38
============================  ==============  ==============  ========

"Same category" (the share of related articles in the source's category) is a
weak relevance proxy, but the centroid ties or wins on it. It also spreads the
recommendations most evenly: "max in-degree" counts how often the most
recommended article appears, and a best-chunk match lets one article with a
generic chunk become a hub. It is about 10× cheaper than pair matching too.

**Languages.** voyage-4 is multilingual and each article is indexed in EN and
ES, so the centroid averages both. The ranking does not depend on the reader's
language, and an article indexed in only one language still takes part. The
endpoint localizes only the labels.

**Exclusions.** The article itself and its series family: the series root
(``series_parent_id`` or the article itself), every child of that root, and
every article from the source's or the root's research document (a series is one research split
into parts). The article page already links all of these.

**Visibility** reuses :func:`retrieval.public_article_criteria` and the
retrieval join. ``public``: the source and every candidate must be APPROVED,
``published_at <= now()`` and outside taxonomy 5. A source that is not visible
returns ``None``, so the endpoint can 404 without saying whether it exists.
``internal``: APPROVED, scheduled included (MCP).

Cost: three indexed statements per call (source lookup, ranking, and the
caller's hydrate). The ranking scans the article chunks once, about 2.7k rows in
prod.
"""

from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, func, or_, select, true
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.category import Category
from app.models.enums import ContentStatus, KnowledgeSourceType
from app.models.knowledge_chunk import EMBEDDING_DIMENSIONS, KnowledgeChunk
from app.services.knowledge.retrieval import (
    VISIBILITIES,
    Visibility,
    _base_from,
    _filters,
    public_article_criteria,
)

DEFAULT_K = 4

_TABLE = KnowledgeChunk.__table__
_ARTICLE = KnowledgeSourceType.ARTICLE.value


@dataclass
class RelatedHit:
    article_id: int
    # Cosine similarity of the two centroids (1 - distance).
    similarity: float


def _centroid(cols):
    return func.avg(cols.embedding, type_=Vector(EMBEDDING_DIMENSIONS))


def find_related(
    db: Session,
    article_id: int,
    *,
    k: int = DEFAULT_K,
    visibility: Visibility = "public",
) -> list[RelatedHit] | None:
    """The ``k`` articles closest to ``article_id``, best first (see module doc).

    Returns ``None`` when the source article is not visible under
    ``visibility``, and ``[]`` when it is visible but has no embedded chunks.
    """
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    if k < 1:
        raise ValueError("k must be >= 1")

    source_q = select(Article.id, Article.series_parent_id, Article.research_id).where(
        Article.id == article_id
    )
    if visibility == "public":
        source_q = source_q.join(Category, Category.id == Article.category_id).where(
            *public_article_criteria()
        )
    else:
        source_q = source_q.where(Article.status == ContentStatus.APPROVED.value)
    source = db.execute(source_q).one_or_none()
    if source is None:
        return None

    root = source.series_parent_id or source.id
    # The source's and the series root's research documents.
    family_research = select(Article.research_id).where(
        Article.id.in_({source.id, root}), Article.research_id.is_not(None)
    )
    family = select(Article.id).where(
        or_(
            Article.id == source.id,
            Article.id == root,
            Article.series_parent_id == root,
            Article.research_id.in_(family_research),
        )
    )

    cols = _TABLE.c
    src = (
        select(_centroid(cols).label("v"))
        .where(
            cols.article_id == source.id,
            cols.source_type == _ARTICLE,
            cols.embedding.is_not(None),
        )
        .cte("related_src")
    )
    criteria = _filters(
        cols,
        visibility=visibility,
        languages=None,
        source_types=[_ARTICLE],
        exclude_article_ids=(),
        facts_only=False,
    )
    cand = (
        select(cols.article_id.label("article_id"), _centroid(cols).label("v"))
        .select_from(_base_from(_TABLE, visibility))
        .where(cols.embedding.is_not(None), cols.article_id.not_in(family), *criteria)
        .group_by(cols.article_id)
        .cte("related_cand")
    )
    distance = cand.c.v.op("<=>", return_type=Float)(src.c.v)
    stmt = (
        select(cand.c.article_id, distance.label("distance"))
        .select_from(cand.join(src, true()))
        .where(src.c.v.is_not(None))
        .order_by(distance, cand.c.article_id)
        .limit(k)
    )
    return [
        RelatedHit(article_id=row.article_id, similarity=1.0 - float(row.distance))
        for row in db.execute(stmt)
    ]


def nearest_articles(
    db: Session,
    vector: list[float],
    *,
    k: int = 1,
    visibility: Visibility = "internal",
) -> list[RelatedHit]:
    """The ``k`` article centroids closest to a caller-supplied ``vector``.

    The same centroid ranking as :func:`find_related`, but against an outside
    vector rather than another article's centroid, and with no exclusions.
    Suggestion dedup (#630) passes the document embedding of a new suggestion
    to ask "which existing article is this closest to?". No Voyage call.
    """
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    if k < 1:
        raise ValueError("k must be >= 1")
    cols = _TABLE.c
    criteria = _filters(
        cols,
        visibility=visibility,
        languages=None,
        source_types=[_ARTICLE],
        exclude_article_ids=(),
        facts_only=False,
    )
    cand = (
        select(cols.article_id.label("article_id"), _centroid(cols).label("v"))
        .select_from(_base_from(_TABLE, visibility))
        .where(cols.embedding.is_not(None), *criteria)
        .group_by(cols.article_id)
        .cte("nearest_cand")
    )
    distance = cand.c.v.cosine_distance(vector)
    stmt = (
        select(cand.c.article_id, distance.label("distance"))
        .order_by(distance, cand.c.article_id)
        .limit(k)
    )
    return [
        RelatedHit(article_id=row.article_id, similarity=1.0 - float(row.distance))
        for row in db.execute(stmt)
    ]


__all__ = ["DEFAULT_K", "RelatedHit", "find_related", "nearest_articles"]
