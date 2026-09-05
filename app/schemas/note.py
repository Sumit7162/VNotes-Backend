import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NoteRead(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    markdown_content: str
    model_used: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteListResponse(BaseModel):
    notes: list[NoteRead]
    total: int
