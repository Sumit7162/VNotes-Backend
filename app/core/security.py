from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from google.oauth2 import id_token
from google.auth.transport import requests

from app.core.config import get_settings
from app.utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Claim that separates a session token from the single-purpose tokens carried
# by verification and password-reset links.
SESSION_PURPOSE = "session"


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
    # Marks this as a session token. Verification and reset links are signed
    # with the same secret, so without a purpose claim one of those links could
    # be pasted in as a bearer token and would open the account it was meant to
    # verify. The middleware refuses anything whose purpose is not SESSION.
    to_encode.update({"exp": expire, "purpose": SESSION_PURPOSE})
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
        # Tokens issued before the purpose claim existed carry no purpose and
        # are still sessions; anything stamped with another purpose is a link
        # token being replayed as a session and is refused.
        purpose = payload.get("purpose")
        if purpose is not None and purpose != SESSION_PURPOSE:
            logger.warning("custom_token_wrong_purpose", purpose=purpose)
            return None
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


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

# bcrypt truncates silently at 72 bytes, so a password longer than that is
# rejected up front rather than quietly having its tail ignored - otherwise two
# different long passwords could open the same account.
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """Hash a plain password with bcrypt, returning the encoded hash."""
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_BYTES:
        raise ValueError("Password is too long")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """Check a plain password against a stored bcrypt hash."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:BCRYPT_MAX_BYTES], password_hash.encode("utf-8"))
    except ValueError:
        # A hash that is not valid bcrypt, e.g. left over from another scheme.
        logger.warning("password_hash_unreadable")
        return False


# --------------------------------------------------------------------------
# Single-purpose links sent by email
# --------------------------------------------------------------------------
#
# A verification or reset link carries a signed JWT rather than a row in a
# table: the signature proves we issued it and the expiry limits the damage if
# the mail is read by someone else, so nothing has to be stored or cleaned up.
# The "purpose" claim stops a verification link from being replayed as a
# password reset, or either one from being used as a session token.

EMAIL_VERIFY_PURPOSE = "email_verify"
PASSWORD_RESET_PURPOSE = "password_reset"


def create_link_token(user_id: str, email: str, purpose: str, expires_delta: timedelta) -> str:
    """Create a short-lived, single-purpose token for a link sent by email."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "purpose": purpose,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_link_token(token: str, purpose: str) -> Optional[dict]:
    """Verify a link token and confirm it was issued for `purpose`."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        logger.info("link_token_expired", purpose=purpose)
        return None
    except jwt.PyJWTError as e:
        logger.warning("link_token_invalid", purpose=purpose, error=str(e))
        return None

    if payload.get("purpose") != purpose:
        logger.warning("link_token_wrong_purpose", expected=purpose, got=payload.get("purpose"))
        return None
    if not payload.get("sub"):
        return None
    return payload


def create_email_verification_token(user_id: str, email: str) -> str:
    return create_link_token(
        user_id,
        email,
        EMAIL_VERIFY_PURPOSE,
        timedelta(hours=settings.email_verification_expire_hours),
    )


def verify_email_verification_token(token: str) -> Optional[dict]:
    return verify_link_token(token, EMAIL_VERIFY_PURPOSE)


def create_password_reset_token(user_id: str, email: str) -> str:
    return create_link_token(
        user_id,
        email,
        PASSWORD_RESET_PURPOSE,
        timedelta(minutes=settings.password_reset_expire_minutes),
    )


def verify_password_reset_token(token: str) -> Optional[dict]:
    return verify_link_token(token, PASSWORD_RESET_PURPOSE)
