import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl


class VideoProcessRequest(BaseModel):
    youtube_url: str


class VideoRead(BaseModel):
    id: uuid.UUID
    youtube_url: str
    title: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VideoListResponse(BaseModel):
    videos: list[VideoRead]
    total: int
