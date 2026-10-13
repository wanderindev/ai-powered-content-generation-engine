from typing import Literal

from pydantic import BaseModel, EmailStr, field_validator

from app.schemas._common import normalize_email


class SubscribeRequest(BaseModel):
    email: EmailStr
    source: Literal["educators", "blog"]
    language: Literal["en", "es"]

    _normalize_email = field_validator("email")(normalize_email)


class SubscribeResponse(BaseModel):
    success: bool
    message: str


class ConfirmRequest(BaseModel):
    token: str
    language: Literal["en", "es"]


class ConfirmResponse(BaseModel):
    success: bool
    confirmed: bool
    email: str | None = None
    message: str | None = None


class UnsubscribeRequest(BaseModel):
    email: EmailStr

    _normalize_email = field_validator("email")(normalize_email)


class UnsubscribeResponse(BaseModel):
    success: bool
    unsubscribed: bool
    message: str | None = None
