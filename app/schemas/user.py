import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    google_id: str


class UserRead(UserBase):
    id: uuid.UUID
    google_id: str
    plan: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserFromGoogle(BaseModel):
    """Data extracted from a Google ID token."""
    google_id: str
    email: str
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
