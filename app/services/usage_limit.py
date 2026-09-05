from datetime import date
from typing import Optional
import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.repositories.usage import UsageRepository
from app.repositories.user import UserRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


class UsageLimitExceeded(Exception):
    """Raised when a user exceeds their plan limits."""

    def __init__(self, message: str, limit_type: str):
        self.message = message
        self.limit_type = limit_type
        super().__init__(self.message)


class UsageLimitService:
    def __init__(self, db: Session):
        self.db = db
        self.usage_repo = UsageRepository(db)
        self.user_repo = UserRepository(db)

    def validate_video_limits(
        self, user_id: uuid.UUID, duration_minutes: Optional[int]
    ) -> None:
        """Check if user can process another video within their free plan limits.

        Rules:
        - Videos < 15 minutes: max 10 per day
        - Videos 15–30 minutes: max 2 per day
        - Videos > 30 minutes: rejected (too long)

        ``duration_minutes`` is None when no metadata source could report a
        runtime. Rejecting on an unknown runtime would block legitimate videos
        whenever the probe is rate-limited, so the video is admitted against the
        short-video bucket and the missing runtime is logged.
        """
        if duration_minutes is None:
            logger.warning("video_duration_unknown_skipping_cap", user_id=str(user_id))
            duration_minutes = 0

        # Hard cap: reject videos longer than 30 minutes
        if duration_minutes > settings.free_max_duration_minutes:
            raise UsageLimitExceeded(
                message=f"Video duration {duration_minutes}min exceeds maximum {settings.free_max_duration_minutes}min",
                limit_type="video_duration",
            )

        usage = self.usage_repo.get_today(user_id)

        # Videos under 15 minutes: enforce daily limit of 10
        if duration_minutes < 15:
            if getattr(usage, "short_videos_processed", 0) >= settings.free_daily_short_videos:
                raise UsageLimitExceeded(
                    message=f"Daily limit of {settings.free_daily_short_videos} short videos (<15 min) reached.",
                    limit_type="daily_short_videos",
                )
            return

        # Videos 15–30 minutes: enforce daily limit of 2
        if usage.videos_processed >= settings.free_daily_videos:
            raise UsageLimitExceeded(
                message=f"Daily limit of {settings.free_daily_videos} videos (15-30 min) reached.",
                limit_type="daily_videos",
            )

    def record_usage(self, user_id: uuid.UUID, duration_minutes: int) -> None:
        """Record completed video processing usage."""
        self.usage_repo.increment_usage(user_id, duration_minutes)
        logger.info("usage_recorded", user_id=str(user_id), minutes=duration_minutes)

    def get_usage_summary(self, user_id: uuid.UUID) -> dict:
        """Get current usage summary for a user."""
        usage = self.usage_repo.get_today(user_id)
        total_videos = self.usage_repo.get_total_videos(user_id)
        
        short_processed = getattr(usage, "short_videos_processed", 0)

        remaining = max(0, settings.free_daily_videos - usage.videos_processed)
        remaining_short = max(0, settings.free_daily_short_videos - short_processed)

        return {
            "date": date.today(),
            "videos_processed": usage.videos_processed,
            "short_videos_processed": short_processed,
            "minutes_processed": usage.minutes_processed,
            "daily_limit": settings.free_daily_videos,
            "daily_short_limit": settings.free_daily_short_videos,
            "remaining_videos": remaining,
            "remaining_short_videos": remaining_short,
            "max_duration_minutes": settings.free_max_duration_minutes,
            "remaining_minutes": max(
                0,
                (settings.free_daily_videos * settings.free_max_duration_minutes)
                - usage.minutes_processed,
            ),
            "total_videos": total_videos,
        }
