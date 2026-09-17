import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_google_id(self, google_id: str) -> Optional[User]:
        stmt = select(User).where(User.google_id == google_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        stmt = select(User).where(User.id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, google_id: str, email: str, full_name: Optional[str] = None, avatar_url: Optional[str] = None) -> User:
        user = User(
            google_id=google_id,
            email=email,
            full_name=full_name,
            avatar_url=avatar_url,
            # Google has already proved the address belongs to this person.
            email_verified=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_by_email(self, email: str) -> Optional[User]:
        # Addresses are stored lower-cased, but rows created before that was
        # true may not be, so the comparison is made case-insensitively.
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        return self.db.execute(stmt).scalar_one_or_none()

    def get_or_create(self, google_id: str, email: str, full_name: Optional[str] = None, avatar_url: Optional[str] = None) -> User:
        # First try to find by google_id
        user = self.get_by_google_id(google_id)

        # If not found, try by email (legacy Clerk users, and accounts created
        # with a password that are now signing in with Google for the first
        # time - that links the two sign-in methods to one account).
        if not user:
            user = self.get_by_email(email)
            if user:
                # Link the old account to the new Google ID
                user.google_id = google_id
                self.db.commit()
                self.db.refresh(user)

        if user:
            # Update with latest data from Google
            changed = False
            if email and email != user.email:
                user.email = email
                changed = True
            if full_name and full_name != user.full_name:
                user.full_name = full_name
                changed = True
            if avatar_url and avatar_url != user.avatar_url:
                user.avatar_url = avatar_url
                changed = True
            if not user.email_verified:
                # Signing in with Google proves the address, so an account that
                # signed up with a password and never clicked the link is
                # verified by this.
                user.email_verified = True
                changed = True
            if changed:
                self.db.commit()
                self.db.refresh(user)
            return user

        return self.create(google_id, email, full_name, avatar_url)

    # -- password accounts -------------------------------------------------

    def create_with_password(
        self,
        email: str,
        password_hash: str,
        full_name: Optional[str] = None,
    ) -> User:
        """Create an account that signs in with an email address and password.

        It starts unverified and with no google_id; the verification link is
        what turns it on.
        """
        user = User(
            google_id=None,
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            email_verified=False,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def set_password(self, user: User, password_hash: str) -> User:
        user.password_hash = password_hash
        self.db.commit()
        self.db.refresh(user)
        return user

    def mark_email_verified(self, user: User) -> User:
        user.email_verified = True
        self.db.commit()
        self.db.refresh(user)
        return user

    # -- request-scoped lookup --------------------------------------------

    def resolve(self, current_user) -> Optional[User]:
        """Find the account a validated bearer token belongs to.

        Every token this API issues now carries the account's own id, which is
        the only identifier a password account has. Tokens issued before
        password sign-in existed carry only a google_id, so those fall back to
        the Google path - which also creates the row if it is somehow missing,
        the behaviour every endpoint relied on before.
        """
        user_id = getattr(current_user, "user_id", None)
        if user_id:
            user = self.get_by_id(user_id)
            if user:
                return user

        google_id = getattr(current_user, "google_id", None)
        if google_id:
            return self.get_or_create(
                google_id=google_id,
                email=current_user.email,
                full_name=current_user.full_name,
                avatar_url=current_user.avatar_url,
            )

        return self.get_by_email(current_user.email)

    def resolve_or_raise(self, current_user) -> User:
        """resolve(), but 401 rather than None when the account is gone.

        Endpoints past this point all assume they have a user, and a token
        whose account has been deleted is no longer a usable session - so this
        is the form they call.
        """
        from fastapi import HTTPException, status

        user = self.resolve(current_user)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account no longer exists. Please sign in again.",
            )
        return user
