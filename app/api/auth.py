from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import verify_google_token, extract_user_from_google_payload, create_access_token
from app.middleware.auth import CurrentUser
from app.repositories.user import UserRepository
from app.schemas.user import UserRead
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleAuthRequest(BaseModel):
    credential: str

@router.post("/google")
def google_auth(request: GoogleAuthRequest, db: Session = Depends(get_db)):
    """Authenticate with Google ID token and return a custom JWT."""
    payload = verify_google_token(request.credential)
    if not payload:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user_data = extract_user_from_google_payload(payload)
    if not user_data:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Could not extract user info from Google token")

    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=user_data["google_id"],
        email=user_data["email"],
        full_name=user_data["full_name"],
        avatar_url=user_data["avatar_url"],
    )

    # Create our custom JWT
    access_token = create_access_token(data={
        "sub": str(user.id),
        "google_id": user.google_id,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
    })

    return {"access_token": access_token, "token_type": "bearer", "user": user}

@router.get("/me", response_model=UserRead)
def get_me(current_user: CurrentUser, db: Session = Depends(get_db)):
    """Get current authenticated user."""
    user_repo = UserRepository(db)
    user = user_repo.get_by_google_id(current_user.google_id)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    return user
