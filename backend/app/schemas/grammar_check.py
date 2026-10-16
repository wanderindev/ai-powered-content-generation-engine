"""Schemas for the grammar-check endpoints (#409 LLM pass; Grammarly apply flow)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GrammarFixItem(BaseModel):
    """One FIND/REPLACE pair the proofread returned, applied or skipped."""

    find: str
    replace: str
    applied: bool
    skipped_reason: str | None = None


class GrammarCheckResponse(BaseModel):
    """Result of one grammar-check run over one language of an article."""

    article_id: int
    language: str
    fixes: list[GrammarFixItem]
    applied_count: int
    skipped_count: int
    checked_at: datetime


class EsAccuracyCheckResponse(BaseModel):
    """Result of one Spanish-vs-English accuracy check (#557).

    No ``checked_at``: the check records nothing on the article besides its
    edits, so a re-run is always allowed and zero fixes is a success.
    """

    article_id: int
    fixes: list[GrammarFixItem]
    applied_count: int
    skipped_count: int


# --- Grammarly web-editor flow (PIC Companion extension) --------------------


class GrammarSourceResponse(BaseModel):
    """One language of an article, body only, rendered to HTML for proofreading.

    The About / Continue Reading series sections are stripped server-side; the
    client never holds them. ``fingerprint`` is the SHA-256 of the *full* stored
    column and must be echoed back on apply so a concurrent edit is detected.
    """

    article_id: int
    language: str
    title: str
    body_html: str
    has_about: bool
    has_continue: bool
    fingerprint: str
    block_count: int


class GrammarAcceptedItem(BaseModel):
    """One suggestion the operator (or the extension's policy) accepted."""

    category: str | None = None
    kind: str | None = None
    before: str
    after: str


class GrammarApplyRequest(BaseModel):
    """Corrected body HTML plus the accepted suggestions, for one language."""

    body_html: str
    fingerprint: str
    tool: Literal["grammarly"] = "grammarly"
    dry_run: bool = False
    accepted: list[GrammarAcceptedItem] = Field(default_factory=list)
    # ``stamp=False`` writes the corrected body (and its audit rows) but leaves
    # ``grammar_checked_<lang>_at`` unset, so the article stays in the queue —
    # the extension sends it when Grammarly's final score is still below the
    # operator's bar (96 or less as of 2026-09-22). ``score`` is that final
    # overall score, recorded in the log line only.
    stamp: bool = True
    score: int | None = Field(default=None, ge=0, le=100)


class GrammarApplyResponse(BaseModel):
    """Outcome of a grammar-apply call.

    ``changed`` compares the round-tripped body against the stored one;
    ``written`` says whether the content column was rewritten (never on a dry
    run, never when ``accepted`` is empty — then only the stamp is set).
    """

    article_id: int
    language: str
    changed: bool
    written: bool
    accepted_count: int
    block_count: int
    checked_at: datetime | None
    diff_preview: list[str]
    # False when the caller asked not to stamp (``stamp=False``) — the body may
    # still have been written; ``checked_at`` is None in that case.
    stamped: bool = False
    # Meaning check (#518): paragraphs Sonnet reverted before the write, and
    # paragraphs it filed for human review (written, but flagged).
    meaning_checked: bool = False
    reverted_count: int = 0
    review_count: int = 0


class GrammarReviewDiffItem(BaseModel):
    """One paragraph the meaning check flagged for a human (#518)."""

    id: int
    article_id: int
    language: str
    block_index: int
    before_text: str
    after_text: str
    reason: str
    resolved_at: datetime | None = None
    created_at: datetime


class GrammarReviewDiffList(BaseModel):
    article_id: int
    pending_count: int
    diffs: list[GrammarReviewDiffItem]
