import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class VideoProcessRequest(BaseModel):
    youtube_url: str


class TranscriptProcessRequest(BaseModel):
    """A transcript the user uploaded or pasted, to be turned into notes.

    The frontend reads an uploaded file in the browser and sends its text here,
    so a paste and a file upload arrive as the same request. Caption
    scaffolding is stripped server-side, so raw .srt/.vtt content is accepted
    exactly as it comes out of the file.
    """

    transcript: str = Field(..., description="Raw transcript text, plain or caption-formatted")
    title: Optional[str] = Field(None, max_length=500, description="Title for the generated notes")


class VideoRead(BaseModel):
    id: uuid.UUID
    # Null when the notes came from an uploaded transcript rather than a URL.
    youtube_url: Optional[str] = None
    source: str = "youtube"
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
