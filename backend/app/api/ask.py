"""Ask Panama in Context: public, unauthenticated Q&A (#635, epic #619).

``POST /api/v1/ask`` ``{question, lang?}``; the flow is
``app.services.ask_pic.answer_question``. HTTP mapping:

* **200** — ``answered`` (answer + citations), ``not_covered`` (abstained
  below the retrieval floor, the model said so, no valid citation survived,
  or the model's reply was malformed) and ``refused`` (the model declined).
  All three share one response shape.
* **422** — the question is blank or longer than
  ``ask_pic_max_question_chars`` (recorded ``INVALID``, counts toward the
  limits); also FastAPI's own body validation.
* **429** — ``RATE_LIMITED`` (per-IP hour / global day) or
  ``BUDGET_EXCEEDED`` (the daily USD cap, at admission or before the model
  call). Both mean "try later"; the detail says which.
* **502** — any unexpected failure (a row admitted before it is closed
  ``ERROR``; a failure before admission writes none).
* **503** — the kill switch (``ask_pic_enabled`` off, or no IP-hash secret);
  no row is written. Checked inside the handler, after FastAPI's body
  validation, so a malformed body is a 422 even while the feature is off.

Every non-200 answer from the handler — the 503 included — is a returned
``JSONResponse``, never a raised ``HTTPException``, and the whole handler
(``answer_question`` too) sits in a guard that turns any exception into the
502, logging the exception class only. sentry-sdk's FastAPI integration
captures a raised 5xx (or any unhandled exception), so the deliberately-off
state and the expected failures create no Sentry event at all. Anything that
still escapes (a framework-level 5xx) is captured with frame locals, and
FastAPI's own request frames hold the parsed ``body`` — the reader's question
(#9) — above this module. That is closed structurally, not here:
``app.core.observability.scrub_pii_event`` drops every frame's ``vars`` from
any event for ``/api/v1/ask`` while keeping the exception and its frames.
FastAPI's own 422 body validation is a 4xx and is not captured.

``GET /api/v1/ask/status`` (#636) answers ``{enabled, max_question_chars}``
from settings alone, so the frontend can hide its entry points while the
switch is off without posting a question.

``answer`` is **untrusted model output**: render it as plain text, never as
HTML (#636).

Cross-origin like the rest of the public API (``CORSMiddleware``).
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.ask_pic import AskCitation, AskRequest, AskResponse, AskStatusResponse
from app.services import ask_pic
from app.services.ask_pic import Outcome
from app.services.ask_pic_controls import (
    UNAVAILABLE_DETAIL,
    ask_pic_available,
    hash_client_ip,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ask"])

_ERRORS = {
    Outcome.RATE_LIMITED: (429, "Too many questions; please try again later"),
    Outcome.BUDGET_EXCEEDED: (429, "Ask Panama in Context has reached today's limit"),
    Outcome.ERROR: (502, "The question could not be answered right now"),
}


@router.get("/ask/status", response_model=AskStatusResponse)
def ask_status():
    """Whether the Ask PIC entry points should show (#636).

    Reads settings only: no DB session, no request body, no limit counted, so
    the frontend can call it on every blog / article view without spending
    the per-IP quota or probing the POST. ``enabled`` mirrors the POST's own
    kill-switch check (``ask_pic_available``), and ``max_question_chars``
    lets the form enforce the same length rule the service records as
    INVALID.
    """
    return AskStatusResponse(
        enabled=ask_pic_available(),
        max_question_chars=get_settings().ask_pic_max_question_chars,
    )


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, db: Session = Depends(get_db)):
    """Answer a reader's question from published articles, with citations."""
    # Defence in depth (#9): nothing may escape this frame, whose ``body``
    # holds the question; see the module docstring.
    try:
        if not ask_pic_available():
            # Returned, not raised: a raised 503 is a Sentry event, and the
            # switch defaults off, so every POST would be one.
            return JSONResponse(status_code=503, content={"detail": UNAVAILABLE_DETAIL})
        return _ask(body, request, db)
    except Exception as exc:  # noqa: BLE001
        ask_pic.safe_rollback(db)
        logger.warning("ask_pic route failed: %s (details withheld)", type(exc).__name__)
        code, detail = _ERRORS[Outcome.ERROR]
        return JSONResponse(status_code=code, content={"detail": detail})


def _ask(body: AskRequest, request: Request, db: Session):
    result = ask_pic.answer_question(
        db,
        question=body.question,
        language_hint=body.lang,
        ip_hash=hash_client_ip(request),
    )
    if result.outcome == Outcome.INVALID:
        return JSONResponse(status_code=422, content={"detail": result.detail})
    if result.outcome in _ERRORS:
        code, detail = _ERRORS[result.outcome]
        return JSONResponse(status_code=code, content={"detail": detail})
    return AskResponse(
        status=result.outcome.value,
        answer=result.answer or None,
        citations=[
            AskCitation(article_id=c.article_id, title=c.title, url=c.url) for c in result.citations
        ],
        language=result.language,
    )
