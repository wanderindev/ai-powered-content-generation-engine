"""``--derive-twin-keys``: measure the #713 twin key before a reindex writes it.

The key lives in ``knowledge_chunks.meta["twin_key"]`` and only a reindex
writes it, so a database indexed before #713 has none and Ask PIC falls back
to no language dedup. To measure the key against such a database without
writing to it, this wraps ``ask_pic.search_passages``: after each search it
re-chunks every retrieved chunk's article with the current chunker (the same
``indexer.wanted_chunks`` the indexer runs) and copies each chunk's key onto
the hit whose (language, content) matches. A hit whose text no longer matches
(the article changed after its last index) is left without a key, exactly as
the endpoint would see it. Reads only; one article query per article, cached
for the run.
"""

from __future__ import annotations

from app.models.article import Article
from app.models.enums import KnowledgeSourceType
from app.services import ask_pic
from app.services.knowledge.chunker import TWIN_KEY
from app.services.knowledge.indexer import wanted_chunks

_ARTICLE = KnowledgeSourceType.ARTICLE.value


class TwinKeyDeriver:
    """Fills missing twin keys on search results; counts what it did."""

    def __init__(self) -> None:
        self._keys: dict[int, dict[tuple[str, str], str]] = {}
        self.filled = 0
        self.unmatched = 0

    def _article_keys(self, db, article_id: int) -> dict[tuple[str, str], str]:
        if article_id not in self._keys:
            article = db.get(Article, article_id)
            chunks = wanted_chunks(article) if article is not None else []
            self._keys[article_id] = {
                (c.meta["language"], c.content): c.meta[TWIN_KEY] for c in chunks
            }
        return self._keys[article_id]

    def fill(self, db, result) -> None:
        for hit in result.chunks:
            if hit.source_type != _ARTICLE or hit.article_id is None:
                continue
            if (hit.meta or {}).get(TWIN_KEY):
                continue
            key = self._article_keys(db, hit.article_id).get((hit.language, hit.content))
            if key is None:
                self.unmatched += 1
                continue
            hit.meta = {**(hit.meta or {}), TWIN_KEY: key}
            self.filled += 1


def install() -> TwinKeyDeriver:
    """Wrap ``ask_pic.search_passages`` so every result carries derived keys."""
    deriver = TwinKeyDeriver()
    original = ask_pic.search_passages

    def search_passages(db, question, extra_queries=None):
        result = original(db, question, extra_queries)
        deriver.fill(db, result)
        return result

    ask_pic.search_passages = search_passages
    return deriver
