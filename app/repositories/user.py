import uuid
from typing import Optional

from sqlalchemy import select
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
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_or_create(self, google_id: str, email: str, full_name: Optional[str] = None, avatar_url: Optional[str] = None) -> User:
        # First try to find by google_id
        user = self.get_by_google_id(google_id)
        
        # If not found, try by email (legacy Clerk users)
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
            if changed:
                self.db.commit()
                self.db.refresh(user)
            return user
            
        return self.create(google_id, email, full_name, avatar_url)
