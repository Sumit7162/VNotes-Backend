import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.user import TimestampMixin


class UsageTracking(TimestampMixin, Base):
    __tablename__ = "usage_tracking"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_user_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    videos_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    short_videos_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    minutes_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
