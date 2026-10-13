import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.subscribe import (
    ConfirmRequest,
    ConfirmResponse,
    SubscribeRequest,
    SubscribeResponse,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from app.services import mailing_list

logger = logging.getLogger(__name__)

router = APIRouter(tags=["subscribe"])


@router.post("/subscribe", response_model=SubscribeResponse)
def subscribe(
    request: SubscribeRequest,
    db: Session = Depends(get_db),
) -> SubscribeResponse:
    """
    Subscribe an email to the mailing list.

    Adds the email to the `educators` DB table (with `mailing_list=True`)
    and sends a confirmation email via the SMTP relay.
    """
    try:
        result = mailing_list.subscribe(
            email=request.email,
            source=request.source,
            language=request.language,
            db=db,
        )
    except Exception as e:
        logger.error(f"Subscribe failed: {e}")
        raise HTTPException(status_code=502, detail="Mailing list service error")

    return SubscribeResponse(
        success=result.get("success", False),
        message=result.get("message", "Check your email to confirm your subscription"),
    )


@router.post("/confirm-subscription", response_model=ConfirmResponse)
def confirm_subscription(
    request: ConfirmRequest,
    db: Session = Depends(get_db),
) -> ConfirmResponse:
    """Confirm a mailing list subscription using the token from the confirmation email."""
    try:
        result = mailing_list.confirm(
            token=request.token,
            language=request.language,
            db=db,
        )
    except Exception as e:
        logger.error(f"Confirm failed: {e}")
        raise HTTPException(status_code=502, detail="Mailing list service error")

    return ConfirmResponse(
        success=result.get("success", False),
        confirmed=result.get("confirmed", False),
        email=result.get("email"),
        message=result.get("message"),
    )


@router.post("/unsubscribe", response_model=UnsubscribeResponse)
def unsubscribe(
    request: UnsubscribeRequest,
    db: Session = Depends(get_db),
) -> UnsubscribeResponse:
    """Unsubscribe an email from the mailing list."""
    try:
        result = mailing_list.unsubscribe(email=request.email, db=db)
    except Exception as e:
        logger.error(f"Unsubscribe failed: {e}")
        raise HTTPException(status_code=502, detail="Mailing list service error")

    return UnsubscribeResponse(
        success=result.get("success", False),
        unsubscribed=result.get("unsubscribed", False),
        message=result.get("message"),
    )
