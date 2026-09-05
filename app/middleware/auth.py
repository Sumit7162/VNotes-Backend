from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import verify_custom_token
from app.schemas.user import UserFromGoogle

security_scheme = HTTPBearer()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
) -> UserFromGoogle:
    """Extract and validate the current user from a custom JWT."""
    token = credentials.credentials
    payload = verify_custom_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    google_id = payload.get("google_id")
    email = payload.get("email")
    if not google_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not extract user information from token",
        )

    return UserFromGoogle(
        google_id=google_id,
        email=email,
        full_name=payload.get("full_name"),
        avatar_url=payload.get("avatar_url")
    )


CurrentUser = Annotated[UserFromGoogle, Depends(get_current_user)]
