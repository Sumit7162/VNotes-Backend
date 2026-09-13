import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.user import TimestampMixin

# Postgres is the deployment target, where JSONB is the right storage for a
# question list; the generic JSON variant keeps the models importable against
# any other backend (a local SQLite scratch database, for instance).
JSONColumn = JSON().with_variant(JSONB, "postgresql")


class QuizDifficulty:
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    MIXED = "mixed"


class Quiz(TimestampMixin, Base):
    """One generated set of questions.

    A quiz is never reused: every "give quiz" press generates a fresh row, so
    the questions and the order of their options differ each time even for the
    same notes. Keeping each generation as its own row is what lets an old
    attempt still be reviewed exactly as it was answered.
    """

    __tablename__ = "quizzes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    difficulty: Mapped[str] = mapped_column(
        String(20), default=QuizDifficulty.MIXED, nullable=False
    )
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # [{id, question, options: [...], correct_index, explanation, topic}]
    # The correct answers live here and are never sent to the browser before a
    # submission - grading happens server-side.
    questions: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)


class QuizAttempt(TimestampMixin, Base):
    """One completed run through a quiz, kept for the quiz dashboard."""

    __tablename__ = "quiz_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Whole percent, 0-100. Stored rather than derived so the dashboard can sort
    # and average without recomputing every attempt.
    score_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{question_id, selected_index, correct_index, is_correct}] - the full
    # answer sheet, so a past attempt can be reviewed question by question.
    answers: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
