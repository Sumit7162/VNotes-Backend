import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.usage import UsageTracking


class UsageRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_today(self, user_id: uuid.UUID) -> UsageTracking:
        today = date.today()
        stmt = select(UsageTracking).where(
            UsageTracking.user_id == user_id,
            UsageTracking.date == today,
        )
        usage = self.db.execute(stmt).scalar_one_or_none()

        if usage is None:
            usage = UsageTracking(user_id=user_id, date=today)
            self.db.add(usage)
            self.db.commit()
            self.db.refresh(usage)

        return usage

    def increment_usage(self, user_id: uuid.UUID, minutes: int) -> UsageTracking:
        usage = self.get_today(user_id)
        if minutes < 15:
            usage.short_videos_processed += 1
        else:
            usage.videos_processed += 1
        usage.minutes_processed += minutes
        self.db.commit()
        self.db.refresh(usage)
        return usage

    def get_total_videos(self, user_id: uuid.UUID) -> int:
        from sqlalchemy import func
        stmt = select(func.coalesce(func.sum(UsageTracking.videos_processed), 0)).where(
            UsageTracking.user_id == user_id
        )
        return self.db.execute(stmt).scalar_one()
