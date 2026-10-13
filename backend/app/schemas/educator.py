"""Schemas for the educator access gate (low-friction, token-based)."""

from typing import Literal

from pydantic import BaseModel, EmailStr, field_validator

from app.schemas._common import normalize_email


class EducatorAccessRequest(BaseModel):
    """Returning educator enters their email."""

    email: EmailStr
    language: Literal["en", "es"] = "es"

    _normalize_email = field_validator("email")(normalize_email)


class EducatorAccessResponse(BaseModel):
    granted: bool
    status: str  # "granted" | "not_registered"
    token: str | None = None
    expires_at: str | None = None


class EducatorSignupRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    institution: str | None = None
    source: str = "media_library"
    language: Literal["en", "es"] = "es"
    mailing_list: bool = False

    _normalize_email = field_validator("email")(normalize_email)


class EducatorSignupResponse(BaseModel):
    success: bool
    granted: bool
    token: str | None = None
    expires_at: str | None = None
    mailing_list_pending: bool = False


class EducatorSessionRequest(BaseModel):
    """Frontend re-validates its stored token on mount."""

    token: str


class EducatorSessionResponse(BaseModel):
    valid: bool
    expires_at: str | None = None


class EducatorUnsubscribeRequest(BaseModel):
    """Unsubscribe is authenticated by the caller's own session token (#51)."""

    token: str


class EducatorUnsubscribeResponse(BaseModel):
    success: bool
    message: str
