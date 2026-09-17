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
    user = user_repo.resolve_or_raise(current_user)

    usage_service = UsageLimitService(db)
    summary = usage_service.get_usage_summary(user.id)

    return summary
