import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class QuizGenerateRequest(BaseModel):
    video_id: uuid.UUID
    question_count: int = Field(default=10, ge=3, le=20)
    difficulty: str = Field(default="mixed")


class QuizQuestionPublic(BaseModel):
    """A question as the person taking the quiz sees it.

    Deliberately has no ``correct_index``: the answer key stays on the server
    until the attempt is submitted, so it cannot be read out of the network tab.
    """

    id: str
    question: str
    options: list[str]
    topic: Optional[str] = None


class QuizRead(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    note_id: uuid.UUID
    title: Optional[str] = None
    difficulty: str
    question_count: int
    model_used: Optional[str] = None
    created_at: datetime
    questions: list[QuizQuestionPublic]


class QuizAnswerSubmit(BaseModel):
    question_id: str
    # None means the question was left unanswered.
    selected_index: Optional[int] = None


class QuizSubmitRequest(BaseModel):
    answers: list[QuizAnswerSubmit]
    duration_seconds: Optional[int] = Field(default=None, ge=0)


class QuizAnswerReview(BaseModel):
    """One graded question, with everything needed to explain the result."""

    question_id: str
    question: str
    options: list[str]
    selected_index: Optional[int] = None
    correct_index: int
    is_correct: bool
    explanation: Optional[str] = None
    topic: Optional[str] = None


class QuizAttemptSummary(BaseModel):
    id: uuid.UUID
    quiz_id: uuid.UUID
    video_id: uuid.UUID
    title: Optional[str] = None
    total_questions: int
    correct_count: int
    score_percent: int
    duration_seconds: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class QuizAttemptDetail(QuizAttemptSummary):
    answers: list[QuizAnswerReview]


class QuizVideoStat(BaseModel):
    """Per-video roll-up for the quiz dashboard."""

    video_id: uuid.UUID
    title: Optional[str] = None
    attempts: int
    best_score: int
    average_score: int
    last_attempt_at: datetime


class QuizDashboard(BaseModel):
    total_attempts: int
    total_questions_answered: int
    total_correct: int
    average_score: int
    best_score: int
    videos_quizzed: int
    recent_attempts: list[QuizAttemptSummary]
    by_video: list[QuizVideoStat]
