import uuid
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import verify_custom_token
from app.schemas.user import AuthenticatedUser

security_scheme = HTTPBearer()


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
) -> AuthenticatedUser:
    """Extract and validate the current user from a custom JWT."""
    token = credentials.credentials
    payload = verify_custom_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # "sub" is the account's own id and is on every token issued now. google_id
    # is only on tokens from before password sign-in existed - a password
    # account has no Google identity at all - so either one is enough.
    user_id = _parse_uuid(payload.get("sub"))
    google_id = payload.get("google_id")
    email = payload.get("email")

    if not email or (not user_id and not google_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not extract user information from token",
        )

    return AuthenticatedUser(
        user_id=user_id,
        google_id=google_id,
        email=email,
        full_name=payload.get("full_name"),
        avatar_url=payload.get("avatar_url"),
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
