from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from google.oauth2 import id_token
from google.auth.transport import requests

from app.core.config import get_settings
from app.utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


def verify_google_token(token: str) -> Optional[dict]:
    """Verify a Google ID token and return the decoded payload."""
    try:
        idinfo = id_token.verify_oauth2_token(
            token, requests.Request(), settings.google_client_id
        )
        return idinfo
    except ValueError as e:
        logger.warning("google_token_verification_failed", error=str(e))
        return None
    except Exception as e:
        logger.error("google_token_verification_error", error=str(e))
        return None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a custom JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def verify_custom_token(token: str) -> Optional[dict]:
    """Verify our custom JWT and return the decoded payload."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("custom_token_expired")
        return None
    except jwt.PyJWTError as e:
        logger.warning("custom_token_invalid", error=str(e))
        return None


def extract_user_from_google_payload(payload: dict) -> Optional[dict]:
    """Extract user info from a Google ID token payload."""
    sub = payload.get("sub")
    if not sub:
        return None

    email = payload.get("email")
    full_name = payload.get("name")
    avatar_url = payload.get("picture")

    return {
        "google_id": sub,
        "email": email or f"{sub}@google.placeholder",
        "full_name": full_name,
        "avatar_url": avatar_url,
    }
