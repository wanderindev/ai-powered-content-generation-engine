"""Request / response shapes for the public Ask PIC endpoint (#635, epic #619)."""

from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    # The real length rule (ask_pic_max_question_chars) is applied by the
    # service so a violation is recorded as INVALID and counts toward the
    # limits; this cap only bounds the request body.
    question: str = Field(..., max_length=10_000)
    # UI language hint; anything but "en" / "es" is ignored (the question
    # text then decides).
    lang: str | None = Field(None, max_length=10)


class AskCitation(BaseModel):
    article_id: int
    title: str
    # Site-relative path in the reader's language: /en/articles/<slug> or
    # /es/articulos/<slug_es>.
    url: str


class AskResponse(BaseModel):
    status: Literal["answered", "not_covered", "refused"]
    answer: str | None = Field(
        description="Untrusted model output: render as plain text, never as HTML."
    )
    citations: list[AskCitation]
    language: Literal["en", "es"]


class AskStatusResponse(BaseModel):
    """``GET /api/v1/ask/status``: whether the entry points should show (#636)."""

    enabled: bool
    max_question_chars: int
