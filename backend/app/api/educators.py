"""API endpoints for educator access to educational materials.

Low-friction, token-based gate (#50/#51/#52/#53):

* ``POST /educators/access``   — returning educator enters email → token or "not registered"
* ``POST /educators/signup``   — new educator submits info → access granted immediately
* ``POST /educators/session``  — frontend re-validates a stored token on mount
* ``POST /educators/unsubscribe`` — token-authenticated opt-out

The 6-digit verify-code and email-confirmation flows are gone; the mailing-list
double-opt-in lives in the separate ``/subscribe`` router.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.educator import (
    EducatorAccessRequest,
    EducatorAccessResponse,
    EducatorSessionRequest,
    EducatorSessionResponse,
    EducatorSignupRequest,
    EducatorSignupResponse,
    EducatorUnsubscribeRequest,
    EducatorUnsubscribeResponse,
)
from app.services import educator_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/educators", tags=["educators"])


@router.post("/access", response_model=EducatorAccessResponse)
def educator_access(
    request: EducatorAccessRequest,
    db: Session = Depends(get_db),
):
    """Returning educator: grant a fresh 7-day token if the email is registered."""
    try:
        result = educator_service.access(
            db=db,
            email=request.email,
            language=request.language,
        )
    except Exception as e:
        logger.error(f"Educator access failed: {e}")
        raise HTTPException(status_code=502, detail="Service error")

    return EducatorAccessResponse(**result)


@router.post("/signup", response_model=EducatorSignupResponse)
def educator_signup(
    request: EducatorSignupRequest,
    db: Session = Depends(get_db),
):
    """Register a new educator and grant access immediately."""
    try:
        result = educator_service.signup(
            db=db,
            email=request.email,
            language=request.language,
            name=request.name,
            institution=request.institution,
            source=request.source,
            mailing_list_optin=request.mailing_list,
        )
    except Exception as e:
        logger.error(f"Educator signup failed: {e}")
        raise HTTPException(status_code=502, detail="Service error")

    return EducatorSignupResponse(**result)


@router.post("/session", response_model=EducatorSessionResponse)
def educator_session(
    request: EducatorSessionRequest,
    db: Session = Depends(get_db),
):
    """Validate a stored session token (frontend mount check)."""
    try:
        result = educator_service.session(db=db, token=request.token)
    except Exception as e:
        logger.error(f"Educator session failed: {e}")
        raise HTTPException(status_code=502, detail="Service error")

    return EducatorSessionResponse(**result)


@router.post("/unsubscribe", response_model=EducatorUnsubscribeResponse)
def educator_unsubscribe(
    request: EducatorUnsubscribeRequest,
    db: Session = Depends(get_db),
):
    """Unsubscribe the caller's own row, authenticated by their session token."""
    try:
        result = educator_service.unsubscribe(db=db, token=request.token)
    except Exception as e:
        logger.error(f"Educator unsubscribe failed: {e}")
        raise HTTPException(status_code=502, detail="Service error")

    return EducatorUnsubscribeResponse(**result)
