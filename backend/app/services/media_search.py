"""Ranked, forgiving media search (#590) — the one helper behind the picker
(``GET /media-library/media``), the library grid (``GET /media-library/candidates``)
and the public library (``GET /public-media``).

Search runs on the stored generated ``search_vector`` columns of ``media`` and
``media_candidates`` (migration 0041, SQL in ``app/models/search_sql.py``):
one ``english`` config over ``pic_unaccent``-ed text, so plurals fold
("workers" ↔ "worker") and accents don't matter ("Colón" ↔ "colon").

Semantics:

* **OR, ranked.** Every query word that is a stopword in neither English nor
  Spanish becomes one OR-ed term; rows are ordered by ``ts_rank`` with weights
  A (title) > B (alt text / caption / search_terms / tags / categories) >
  C (candidate description) > D (credit). OR is deliberate: the picker sends a
  long LLM ``image_query`` sentence, which under AND would match nothing.
* **Trigram fallback.** Only when full-text search finds nothing (a typo, or a
  query of nothing but stopwords) does it fall back to ``word_similarity`` ≥
  ``TRIGRAM_THRESHOLD`` per token against the raw text. No trigram index backs
  it: the function form is not index-usable, and it only runs on a miss.

User input only ever reaches SQL as bound parameters; ``literal_column`` is
used for fixed constants (regconfigs, the empty tsvector, the weight letter).
"""

import re

from sqlalchemy import Select, Text, cast, false, func, literal_column, select
from sqlalchemy.dialects.postgresql import TSQUERY, TSVECTOR
from sqlalchemy.orm import Session

from app.models.media import Media, media_tags
from app.models.media_candidate import MediaCandidate
from app.models.tag import Tag

MAX_SEARCH_TOKENS = 16
TRIGRAM_THRESHOLD = 0.6
TRIGRAM_MIN_TOKEN_LEN = 3

_EN = literal_column("'english'::regconfig")
_ES = literal_column("'spanish'::regconfig")
_EMPTY_TSVECTOR = literal_column("''::tsvector", type_=TSVECTOR)

# The generated columns are Table columns, deliberately not ORM attributes (so
# no entity statement ever names them before migration 0041 is applied); this
# module is their only reader.
_MEDIA_VECTOR = Media.__table__.c.search_vector
_CANDIDATE_VECTOR = MediaCandidate.__table__.c.search_vector


def tokenize(search: str | None) -> list[str]:
    """Lowercased word tokens, de-duplicated in order, capped at MAX_SEARCH_TOKENS.

    ``\\w`` is Unicode-aware, so ``colón`` stays one token.
    """
    if not search:
        return []
    seen: dict[str, None] = {}
    for tok in re.findall(r"\w+", search.lower()):
        seen.setdefault(tok, None)
    return list(seen)[:MAX_SEARCH_TOKENS]


def or_tsquery_text(db: Session, tokens: list[str]) -> str | None:
    """The OR-ed tsquery text for ``tokens``, as Postgres itself renders it.

    One round trip: per token, the English ``plainto_tsquery`` of the unaccented
    token (its stem, or empty for an English stopword) and the node count of the
    Spanish ``plainto_tsquery`` of the raw token (0 for a Spanish stopword, so
    ``del`` / ``de`` / ``en`` don't match every bilingual ``search_terms`` row).
    A token survives only if it is a stopword in neither language. Returns
    ``None`` when nothing survives.
    """
    if not tokens:
        return None
    cols = []
    for tok in tokens:
        cols.append(cast(func.plainto_tsquery(_EN, func.pic_unaccent(tok)), Text))
        cols.append(func.numnode(func.plainto_tsquery(_ES, tok)))
    row = db.execute(select(*cols)).one()

    kept: list[str] = []
    for i in range(len(tokens)):
        en_text, es_nodes = row[2 * i], row[2 * i + 1]
        if en_text and en_text.strip() and es_nodes and es_nodes > 0:
            if en_text not in kept:
                kept.append(en_text)
    if not kept:
        return None
    return " | ".join(f"({t})" for t in kept)


def _tag_vector():
    """Weight-B vector over the Media's own ``media_tags`` names (EN + ES)."""
    tag_doc = (
        select(func.string_agg(func.concat_ws(" ", Tag.name, Tag.name_es), " "))
        .select_from(media_tags.join(Tag, Tag.id == media_tags.c.tag_id))
        .where(media_tags.c.media_id == Media.id)
        .scalar_subquery()
    )
    return func.setweight(
        func.to_tsvector(_EN, func.pic_unaccent(func.coalesce(tag_doc, ""))),
        literal_column("'B'"),
        type_=TSVECTOR,
    )


def media_search_vector():
    """Search vector for a query with ``Media`` and (outer-joined) ``MediaCandidate``
    in FROM: the media's own vector, its candidate's, and its tag names."""
    candidate_vec = func.coalesce(_CANDIDATE_VECTOR, _EMPTY_TSVECTOR, type_=TSVECTOR)
    return _MEDIA_VECTOR.op("||", return_type=TSVECTOR)(candidate_vec).op(
        "||", return_type=TSVECTOR
    )(_tag_vector())


def media_trigram_text():
    """Raw (unaccented, lowercased) text the trigram fallback compares against."""
    return func.pic_unaccent(
        func.lower(
            func.concat_ws(
                " ",
                Media.title,
                Media.alt_text,
                Media.caption,
                Media.search_terms,
                MediaCandidate.title,
                func.array_to_string(MediaCandidate.tags, " "),
            )
        )
    )


def candidate_search_vector():
    """Search vector for a ``MediaCandidate`` query."""
    return _CANDIDATE_VECTOR


def candidate_trigram_text():
    """Raw (unaccented, lowercased) candidate text for the trigram fallback."""
    return func.pic_unaccent(
        func.lower(
            func.concat_ws(
                " ",
                MediaCandidate.title,
                MediaCandidate.description,
                func.array_to_string(MediaCandidate.tags, " "),
                func.array_to_string(MediaCandidate.source_categories, " "),
            )
        )
    )


def _count(db: Session, query: Select) -> int:
    return db.execute(select(func.count()).select_from(query.subquery())).scalar() or 0


def apply_ranked_search(
    db: Session, query: Select, search: str | None, *, vector, trigram_text
) -> tuple[Select, int]:
    """Filter ``query`` by ``search`` and put relevance first in its ordering.

    Returns ``(query, total)``. Callers append their tiebreak ``.order_by(...)``
    and paginate. OR semantics so a long ``image_query`` ranks instead of
    emptying; ``ts_rank`` weights A (title) > B (alt/caption/search_terms/
    tags/categories) > C (description) > D (credit). Falls back to trigram
    ``word_similarity`` only when full-text search matches nothing. A search
    with no word tokens leaves ``query`` unfiltered.
    """
    tokens = tokenize(search)
    if not tokens:
        return query, _count(db, query)

    qtext = or_tsquery_text(db, tokens)
    if qtext is not None:
        # Postgres's own tsquery output, cast back — never through to_tsquery,
        # which would re-stem the stems.
        tsq = cast(qtext, TSQUERY)
        fts = query.where(vector.op("@@")(tsq))
        total = _count(db, fts)
        if total > 0:
            return fts.order_by(func.ts_rank(vector, tsq).desc()), total

    long_tokens = [t for t in tokens if len(t) >= TRIGRAM_MIN_TOKEN_LEN]
    if not long_tokens:
        return query.where(false()), 0
    sims = [func.word_similarity(func.pic_unaccent(t), trigram_text) for t in long_tokens]
    score = func.greatest(*sims) if len(sims) > 1 else sims[0]
    fb = query.where(score >= TRIGRAM_THRESHOLD)
    return fb.order_by(score.desc()), _count(db, fb)
