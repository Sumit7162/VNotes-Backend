import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import get_settings

settings = get_settings()


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    google_id: str


class UserRead(UserBase):
    id: uuid.UUID
    # Null for an account that signs in with an email address and a password.
    google_id: Optional[str] = None
    plan: str
    email_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuthenticatedUser(BaseModel):
    """Whoever the bearer token in the current request belongs to.

    `user_id` is the reliable identity and is present on every token this API
    issues now. `google_id` is kept for tokens issued before password sign-in
    existed, which are still valid until they expire.
    """

    user_id: Optional[uuid.UUID] = None
    google_id: Optional[str] = None
    email: str
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


# The old name, kept so nothing that still imports it breaks.
UserFromGoogle = AuthenticatedUser


def _validate_password(value: str) -> str:
    """Reject passwords that are trivially guessable.

    Length does most of the work; the letter-and-digit rule only rules out the
    "12345678" class of password without pushing people into the unmemorable
    symbol soup that longer rules produce.
    """
    minimum = settings.password_min_length
    if len(value) < minimum:
        raise ValueError(f"Password must be at least {minimum} characters long")
    if len(value.encode("utf-8")) > 72:
        # bcrypt ignores anything past 72 bytes, so a longer password would
        # silently not be fully checked.
        raise ValueError("Password must be at most 72 characters long")
    if not any(c.isalpha() for c in value):
        raise ValueError("Password must contain at least one letter")
    if not any(c.isdigit() for c in value):
        raise ValueError("Password must contain at least one number")
    return value


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        return _validate_password(value)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EmailOnlyRequest(BaseModel):
    """Body for the endpoints that take nothing but an address."""

    email: EmailStr


class TokenRequest(BaseModel):
    """Body for the endpoints that take a token out of an emailed link."""

    token: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        return _validate_password(value)


class AuthResponse(BaseModel):
    """A successful sign-in: the session token plus the account it belongs to."""

    access_token: str
    token_type: str = "bearer"
    user: UserRead


class MessageResponse(BaseModel):
    """A flow that finished without issuing a token, e.g. signup."""

    message: str
    # False when Brevo is unconfigured or refused, so the caller can tell the
    # user the link could not be delivered instead of claiming it was sent.
    email_sent: bool = True
