"""Admin schemas for cross-article consistency findings (#631, shadow mode)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ConsistencyFindingItem(BaseModel):
    """One claim that contradicts another article's fact or a KB fact."""

    id: int
    article_id: int
    claim_id: int | None = None
    claim_text: str
    other_source_type: str
    other_source_id: int
    other_article_id: int | None = None
    other_article_title: str | None = None
    other_article_slug: str | None = None
    other_text: str
    verdict: str
    explanation: str
    similarity: float | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    created_at: datetime


class ConsistencyFindingList(BaseModel):
    article_id: int
    open_count: int
    findings: list[ConsistencyFindingItem]


class ConsistencyResolveRequest(BaseModel):
    resolution: Literal["DISMISSED", "RESOLVED"]
