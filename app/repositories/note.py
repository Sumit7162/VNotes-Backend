import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.note import Note


class NoteRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_video_id(self, video_id: uuid.UUID) -> Optional[Note]:
        stmt = select(Note).where(Note.video_id == video_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_id(self, note_id: uuid.UUID) -> Optional[Note]:
        stmt = select(Note).where(Note.id == note_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_by_user(self, user_id: uuid.UUID, limit: int = 50) -> list[Note]:
        from app.models.video import Video
        stmt = (
            select(Note)
            .join(Video, Note.video_id == Video.id)
            .where(Video.user_id == user_id)
            .order_by(Note.created_at.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def create(
        self, video_id: uuid.UUID, markdown_content: str, model_used: Optional[str] = None
    ) -> Note:
        note = Note(video_id=video_id, markdown_content=markdown_content, model_used=model_used)
        self.db.add(note)
        self.db.commit()
        self.db.refresh(note)
        return note
