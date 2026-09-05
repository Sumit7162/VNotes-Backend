from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.middleware.auth import CurrentUser
from app.repositories.user import UserRepository
from app.repositories.video import VideoRepository
from app.schemas.video import VideoListResponse, VideoProcessRequest, VideoRead
from app.services.usage_limit import UsageLimitExceeded, UsageLimitService
from app.services.video_processing import VideoProcessingService
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _process_video_background(video_id, user_id, db_url: str):
    """Background task wrapper for video processing."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        service = VideoProcessingService(db)
        service.process_video(video_id, user_id)
    finally:
        db.close()


@router.post("/process", response_model=VideoRead)
async def process_video(
    request: VideoProcessRequest,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Submit a YouTube URL for processing."""
    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=current_user.google_id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
    )

    # Get video info to check duration
    from app.services.youtube_downloader import YouTubeDownloaderService
    downloader = YouTubeDownloaderService()

    try:
        info = downloader.get_video_info(request.youtube_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YouTube URL: {e}")

    duration_seconds = info.get("duration")
    # None means no source could tell us the runtime; the limit check treats that
    # differently from a genuinely zero-length video.
    duration_minutes = duration_seconds // 60 if duration_seconds else None

    # Check usage limits
    usage_service = UsageLimitService(db)
    try:
        usage_service.validate_video_limits(user.id, duration_minutes)
    except UsageLimitExceeded as e:
        raise HTTPException(status_code=429, detail=e.message)

    # Create video record
    video_repo = VideoRepository(db)
    video = video_repo.create(
        user_id=user.id,
        youtube_url=request.youtube_url,
        title=info.get("title"),
        duration_seconds=duration_seconds,
    )

    # Start background processing
    background_tasks.add_task(_process_video_background, video.id, user.id, db.bind.url)

    logger.info("video_processing_started", video_id=str(video.id), user_id=str(user.id))
    return video


@router.get("", response_model=VideoListResponse)
def list_videos(
    current_user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List videos for the current user."""
    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=current_user.google_id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
    )

    video_repo = VideoRepository(db)
    videos = video_repo.get_by_user(user.id, limit=limit, offset=offset)
    total = video_repo.count_by_user(user.id)

    return VideoListResponse(videos=videos, total=total)


@router.get("/{video_id}", response_model=VideoRead)
def get_video(
    video_id: str,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Get a specific video by ID."""
    import uuid
    video_repo = VideoRepository(db)
    video = video_repo.get_by_id(uuid.UUID(video_id))

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return video


@router.delete("/{video_id}", status_code=204)
def delete_video(
    video_id: str,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Delete a video and its associated resources."""
    import uuid
    import os

    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=current_user.google_id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
    )

    video_repo = VideoRepository(db)
    video = video_repo.get_by_id(uuid.UUID(video_id))

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this video")

    # Clean up files on disk
    for path in [video.file_path, video.audio_path]:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("file_cleanup_failed", path=path)

    # Clean up transcript and notes files
    from app.core.config import get_settings
    settings = get_settings()
    for directory, ext in [(settings.transcript_dir, ".txt"), (settings.notes_dir, ".md")]:
        filepath = os.path.join(directory, f"{video_id}{ext}")
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                logger.warning("file_cleanup_failed", path=filepath)

    video_repo.delete(video)
    logger.info("video_deleted", video_id=video_id, user_id=str(user.id))
