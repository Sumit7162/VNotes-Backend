from datetime import date
from typing import Optional

from pydantic import BaseModel


class UsageRead(BaseModel):
    date: date
    videos_processed: int
    short_videos_processed: int = 0
    minutes_processed: int
    daily_limit: int
    daily_short_limit: int = 10
    remaining_videos: int
    remaining_short_videos: int = 10
    max_duration_minutes: int
    remaining_minutes: int


class UsageSummary(BaseModel):
    total_videos: int
    today: UsageRead
