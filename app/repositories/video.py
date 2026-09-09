import uuid
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.video import Video, VideoSource


class VideoRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, video_id: uuid.UUID) -> Optional[Video]:
        stmt = select(Video).where(Video.id == video_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_user(self, user_id: uuid.UUID, limit: int = 50, offset: int = 0) -> list[Video]:
        stmt = (
            select(Video)
            .where(Video.user_id == user_id)
            .order_by(Video.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_by_user(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Video).where(Video.user_id == user_id)
        return self.db.execute(stmt).scalar_one()

    def create(
        self,
        user_id: uuid.UUID,
        youtube_url: Optional[str] = None,
        title: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        source: str = VideoSource.YOUTUBE,
    ) -> Video:
        video = Video(
            user_id=user_id,
            youtube_url=youtube_url,
            title=title,
            duration_seconds=duration_seconds,
            source=source,
        )
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video

    def update_status(
        self,
        video: Video,
        status: str,
        title: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        error_message: Optional[str] = None,
        clear_error: bool = False,
        file_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        clear_media_paths: bool = False,
    ) -> Video:
        video.status = status
        if title is not None:
            video.title = title
        if duration_seconds is not None:
            video.duration_seconds = duration_seconds
        # `error_message=None` means "leave it alone", so clearing a stale error
        # from an earlier attempt needs its own flag.
        if clear_error:
            video.error_message = None
        if error_message is not None:
            video.error_message = error_message
        # Same convention as error_message: None means "leave it alone", so
        # dropping a path once its file is gone needs its own flag.
        if clear_media_paths:
            video.file_path = None
            video.audio_path = None
        if file_path is not None:
            video.file_path = file_path
        if audio_path is not None:
            video.audio_path = audio_path
        self.db.commit()
        self.db.refresh(video)
        return video

    def delete(self, video: Video) -> None:
        """Delete a video record. Associated notes are removed by CASCADE."""
        self.db.delete(video)
        self.db.commit()
