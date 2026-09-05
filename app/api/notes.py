import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.middleware.auth import CurrentUser
from app.repositories.note import NoteRepository
from app.repositories.user import UserRepository
from app.schemas.note import NoteRead

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("/{video_id}", response_model=NoteRead)
def get_notes_for_video(
    video_id: str,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Get AI-generated notes for a specific video."""
    note_repo = NoteRepository(db)
    note = note_repo.get_by_video_id(uuid.UUID(video_id))

    if not note:
        raise HTTPException(status_code=404, detail="Notes not found for this video")

    return note


@router.get("", response_model=list[NoteRead])
def list_notes(
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """List all notes for the current user."""
    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=current_user.google_id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
    )

    note_repo = NoteRepository(db)
    notes = note_repo.list_by_user(user.id)

    return notes
