from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.middleware.auth import CurrentUser
from app.repositories.user import UserRepository
from app.schemas.usage import UsageRead
from app.services.usage_limit import UsageLimitService

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("", response_model=UsageRead)
def get_usage(
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Get current usage stats for the authenticated user."""
    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=current_user.google_id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
    )

    usage_service = UsageLimitService(db)
    summary = usage_service.get_usage_summary(user.id)

    return summary
