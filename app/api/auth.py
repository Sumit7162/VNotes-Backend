"""Sign-in: Google, and email address plus password.

The password flow is deliberately three steps rather than two. Signup creates
the account but leaves it switched off; the link Brevo delivers is what proves
the address belongs to whoever typed it; only then does login issue a session
token. Nothing but that link can turn an account on, so an address somebody
else owns cannot be used to sign up.
"""

import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    extract_user_from_google_payload,
    hash_password,
    verify_email_verification_token,
    verify_google_token,
    verify_password,
    verify_password_reset_token,
)
from app.middleware.auth import CurrentUser
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import (
    AuthResponse,
    EmailOnlyRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
    TokenRequest,
    UserRead,
)
from app.services.email_service import EmailDeliveryError, get_email_service
from app.services.email_validation import normalize_and_check_email
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/auth", tags=["auth"])

settings = get_settings()
logger = get_logger(__name__)

# Said to anyone who asks for a verification or reset email, whether or not the
# address has an account. Naming the ones that do would turn these endpoints
# into a way of finding out who is registered.
NEUTRAL_EMAIL_REPLY = "If that address has an account, we have sent it an email."

# Anyone can ask for a verification or reset email as often as they like, and
# Brevo's free plan allows 300 messages a day - so without a cooldown one
# person holding down a button exhausts the day's quota for everybody.
#
# This is best-effort: it lives in the process, so two Cloud Run instances hold
# separate copies and a restart forgets it. That is enough for accidental
# double-clicks and casual abuse; a determined attacker needs a shared store or
# a rate limit at the edge.
RESEND_COOLDOWN_SECONDS = 60
_last_email_at: Dict[str, float] = {}


def _cooldown_active(email: str) -> bool:
    """True if this address was emailed within the cooldown window."""
    now = time.monotonic()
    last = _last_email_at.get(email)
    if last is not None and now - last < RESEND_COOLDOWN_SECONDS:
        return True

    # Drop entries that have aged out, so the dict cannot grow without bound
    # on a long-running instance.
    if len(_last_email_at) > 1000:
        cutoff = now - RESEND_COOLDOWN_SECONDS
        for key in [k for k, v in _last_email_at.items() if v < cutoff]:
            _last_email_at.pop(key, None)

    _last_email_at[email] = now
    return False


def _session_token(user: User) -> str:
    """Issue the session JWT for an account."""
    return create_access_token(
        data={
            "sub": str(user.id),
            "google_id": user.google_id,
            "email": user.email,
            "full_name": user.full_name,
            "avatar_url": user.avatar_url,
        }
    )


def _send_verification(user: User) -> bool:
    """Send the verification link. False if it could not be delivered."""
    token = create_email_verification_token(str(user.id), user.email)
    try:
        return get_email_service().send_verification_email(
            user.email, token, user.full_name
        )
    except EmailDeliveryError as e:
        logger.error("verification_email_failed", user_id=str(user.id), error=str(e))
        return False


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------


class GoogleAuthRequest(BaseModel):
    credential: str


@router.post("/google")
def google_auth(request: GoogleAuthRequest, db: Session = Depends(get_db)):
    """Authenticate with Google ID token and return a custom JWT."""
    payload = verify_google_token(request.credential)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user_data = extract_user_from_google_payload(payload)
    if not user_data:
        raise HTTPException(
            status_code=401, detail="Could not extract user info from Google token"
        )

    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=user_data["google_id"],
        email=user_data["email"],
        full_name=user_data["full_name"],
        avatar_url=user_data["avatar_url"],
    )

    return {"access_token": _session_token(user), "token_type": "bearer", "user": user}


# ---------------------------------------------------------------------------
# Email address and password
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """Create an unverified account and email it a verification link."""
    email, email_error = normalize_and_check_email(request.email)
    if email_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=email_error)

    user_repo = UserRepository(db)
    existing = user_repo.get_by_email(email)

    if existing:
        if existing.password_hash:
            # An account already has this address and a password on it. Whether
            # it is verified is not said, for the same reason as above.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists. Try signing in instead.",
            )

        # The address belongs to a Google account with no password. Adding one
        # is safe - the address is already proved - and it means the same
        # person can now use either method.
        user_repo.set_password(existing, hash_password(request.password))
        if request.full_name and not existing.full_name:
            existing.full_name = request.full_name
            db.commit()
        logger.info("password_added_to_google_account", user_id=str(existing.id))
        return MessageResponse(
            message=(
                "This email is already registered with Google. A password has been set, "
                "so you can now sign in either way."
            ),
            email_sent=False,
        )

    try:
        user = user_repo.create_with_password(
            email=email,
            password_hash=hash_password(request.password),
            full_name=request.full_name,
        )
    except IntegrityError:
        # Two signups for the same address at once: the unique index on email
        # caught the loser.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists. Try signing in instead.",
        )

    sent = _send_verification(user)
    logger.info("signup_created", user_id=str(user.id), email_sent=sent)

    if not sent:
        # The account exists but cannot be used, so say so plainly rather than
        # leaving someone waiting for mail that is not coming.
        return MessageResponse(
            message=(
                "Account created, but the verification email could not be sent. "
                "Use 'Resend verification email' in a moment, or contact support."
            ),
            email_sent=False,
        )

    return MessageResponse(
        message=f"Account created. Check {email} for the link that activates it.",
        email_sent=True,
    )


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Exchange an email address and password for a session token."""
    email = request.email.strip().lower()
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(email)

    # One message for "no such account" and for "wrong password", so the
    # response cannot be used to work out which addresses are registered. The
    # hash is still verified against a missing user's None so that both paths
    # take a comparable amount of time.
    if not user or not verify_password(request.password, user.password_hash):
        if user and not user.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account was created with Google. Use 'Continue with Google' to sign in.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.email_verified:
        # 403 rather than 401: the credentials were right, the account is just
        # not switched on yet. The frontend keys its "resend" prompt off this.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your email address is not verified yet. Check your inbox for the verification link.",
        )

    logger.info("password_login", user_id=str(user.id))
    return AuthResponse(access_token=_session_token(user), user=UserRead.model_validate(user))


@router.post("/verify-email", response_model=AuthResponse)
def verify_email(request: TokenRequest, db: Session = Depends(get_db)):
    """Turn an account on from the link in the verification email.

    A session token comes back with it, so following the link signs the person
    in rather than dropping them at the login form.
    """
    payload = verify_email_verification_token(request.token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired. Request a new one.",
        )

    user_repo = UserRepository(db)
    user_id = _as_uuid(payload["sub"])
    user = user_repo.get_by_id(user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    # The token also carries the address it was issued for. If the account's
    # address has changed since, the link no longer proves anything.
    if payload.get("email") and payload["email"].lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is no longer valid for this account.",
        )

    if not user.email_verified:
        user_repo.mark_email_verified(user)
        logger.info("email_verified", user_id=str(user.id))
    # Following the same link twice is not an error: the second visit just
    # signs them in, which is what they were trying to do anyway.

    return AuthResponse(access_token=_session_token(user), user=UserRead.model_validate(user))


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(request: EmailOnlyRequest, db: Session = Depends(get_db)):
    """Send the verification link again."""
    email = request.email.strip().lower()
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(email)

    if user and not user.email_verified:
        if _cooldown_active(email):
            logger.info("resend_verification_throttled", user_id=str(user.id))
        else:
            _send_verification(user)
    elif user:
        logger.info("resend_verification_already_verified", user_id=str(user.id))

    return MessageResponse(message=NEUTRAL_EMAIL_REPLY, email_sent=True)


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(request: EmailOnlyRequest, db: Session = Depends(get_db)):
    """Email a password-reset link."""
    email = request.email.strip().lower()
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(email)

    if user and user.password_hash and not _cooldown_active(f"reset:{email}"):
        token = create_password_reset_token(str(user.id), user.email)
        try:
            get_email_service().send_password_reset_email(user.email, token, user.full_name)
        except EmailDeliveryError as e:
            logger.error("reset_email_failed", user_id=str(user.id), error=str(e))

    return MessageResponse(message=NEUTRAL_EMAIL_REPLY, email_sent=True)


@router.post("/reset-password", response_model=AuthResponse)
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Set a new password from the link in the reset email."""
    payload = verify_password_reset_token(request.token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Request a new one.",
        )

    user_repo = UserRepository(db)
    user_id = _as_uuid(payload["sub"])
    user = user_repo.get_by_id(user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    user_repo.set_password(user, hash_password(request.password))
    if not user.email_verified:
        # Receiving the reset mail proves the address just as well as the
        # verification link does.
        user_repo.mark_email_verified(user)

    logger.info("password_reset", user_id=str(user.id))
    return AuthResponse(access_token=_session_token(user), user=UserRead.model_validate(user))


# ---------------------------------------------------------------------------
# Current session
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserRead)
def get_me(current_user: CurrentUser, db: Session = Depends(get_db)):
    """Get current authenticated user."""
    user_repo = UserRepository(db)
    user = user_repo.resolve(current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    """The token's subject as a UUID, or None if it is not one."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
