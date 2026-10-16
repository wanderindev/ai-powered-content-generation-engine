"""Knowledge-layer admin debug surface (#626, epic #617).

``GET /admin/dashboard/knowledge/search`` returns :func:`retrieval.search`'s
raw ranked output (groups, the full chunk list, per-stage ranks / scores and
timings) for inspecting retrieval by hand. Admin-gated like the rest of
``/admin/dashboard``; deliberately not on ``DRIVER_ALLOWED_CALLS``.

``mode=vector`` with no ``VOYAGE_API_KEY`` answers **503** (the "empty
credentials disable a feature" rule) and a failed query embedding **502**;
``hybrid`` degrades to FTS and reports it in ``degraded`` instead.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import validate_admin_or_allowlisted_driver_token
from app.core.database import get_db
from app.models.enums import KnowledgeSourceType, Language
from app.schemas.knowledge import KnowledgeSearchResponse
from app.services import embeddings
from app.services.knowledge import retrieval

router = APIRouter(
    prefix="/admin/dashboard/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(validate_admin_or_allowlisted_driver_token)],
)


@router.get("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    q: str = Query(..., min_length=1, max_length=1000, description="Search query"),
    mode: Literal["vector", "fts", "hybrid"] = "hybrid",
    rerank: bool = False,
    k: int = Query(retrieval.DEFAULT_K, ge=1, le=50),
    language: list[Language] | None = Query(None),
    source_type: list[KnowledgeSourceType] | None = Query(None),
    visibility: Literal["public", "internal"] = "public",
    facts_only: bool = False,
    exclude_article_id: list[int] | None = Query(None),
    candidates: int = Query(retrieval.DEFAULT_CANDIDATES, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Raw ranked knowledge-search results, for debugging retrieval."""
    try:
        result = retrieval.search(
            db,
            q,
            mode=mode,
            rerank=rerank,
            k=k,
            languages=[lang.value for lang in language] if language else None,
            source_types=[st.value for st in source_type] if source_type else None,
            visibility=visibility,
            exclude_article_ids=exclude_article_id or (),
            facts_only=facts_only,
            candidates=candidates,
        )
    except embeddings.EmbeddingsDisabledError:
        raise HTTPException(
            status_code=503, detail="Vector search is disabled: VOYAGE_API_KEY is not set"
        ) from None
    except embeddings.EmbeddingsError as exc:
        raise HTTPException(status_code=502, detail=f"Query embedding failed: {exc}") from None
    return result
