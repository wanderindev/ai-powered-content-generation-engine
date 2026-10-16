"""Response shapes for the knowledge-search admin debug endpoint (#626)."""

from pydantic import BaseModel, ConfigDict


class KnowledgeChunkHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    source_type: str
    source_id: int
    article_id: int | None
    language: str
    chunk_index: int
    heading_path: str | None
    content: str
    meta: dict | None
    rank: int
    vector_rank: int | None
    vector_distance: float | None
    fts_rank: int | None
    fts_score: float | None
    rrf_score: float | None
    rerank_rank: int | None
    rerank_score: float | None


class KnowledgeArticleHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rank: int
    article_id: int | None
    title: str | None
    title_es: str | None
    slug: str | None
    slug_es: str | None
    best: KnowledgeChunkHit
    chunks: list[KnowledgeChunkHit]


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str
    mode: str
    visibility: str
    reranked: bool
    degraded: list[str]
    timings_ms: dict[str, float]
    results: list[KnowledgeArticleHit]
    chunks: list[KnowledgeChunkHit]
