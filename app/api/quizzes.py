import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.middleware.auth import CurrentUser
from app.models.quiz import Quiz, QuizAttempt, QuizDifficulty
from app.repositories.note import NoteRepository
from app.repositories.quiz import QuizRepository
from app.repositories.user import UserRepository
from app.repositories.video import VideoRepository
from app.schemas.quiz import (
    QuizAnswerReview,
    QuizAttemptDetail,
    QuizAttemptSummary,
    QuizDashboard,
    QuizGenerateRequest,
    QuizRead,
    QuizSubmitRequest,
)
from app.services.quiz_ai import QuizAIService, QuizGenerationError
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])

VALID_DIFFICULTIES = {
    QuizDifficulty.EASY,
    QuizDifficulty.MEDIUM,
    QuizDifficulty.HARD,
    QuizDifficulty.MIXED,
}


def _current_user_id(current_user: CurrentUser, db: Session) -> uuid.UUID:
    user_repo = UserRepository(db)
    user = user_repo.get_or_create(
        google_id=current_user.google_id,
        email=current_user.email,
        full_name=current_user.full_name,
        avatar_url=current_user.avatar_url,
    )
    return user.id


def _public_questions(quiz: Quiz) -> list[dict]:
    """The quiz as the taker sees it - answer key removed."""
    return [
        {
            "id": question["id"],
            "question": question["question"],
            "options": question["options"],
            "topic": question.get("topic"),
        }
        for question in quiz.questions or []
    ]


def _quiz_response(quiz: Quiz) -> dict:
    return {
        "id": quiz.id,
        "video_id": quiz.video_id,
        "note_id": quiz.note_id,
        "title": quiz.title,
        "difficulty": quiz.difficulty,
        "question_count": quiz.question_count,
        "model_used": quiz.model_used,
        "created_at": quiz.created_at,
        "questions": _public_questions(quiz),
    }


def _attempt_review(attempt: QuizAttempt, quiz: Optional[Quiz]) -> list[QuizAnswerReview]:
    """Rebuild the full answer sheet for a past attempt.

    The attempt stores what was chosen; the quiz stores the wording. If the quiz
    row is gone the attempt still renders, just without the question text.
    """
    by_id = {q["id"]: q for q in (quiz.questions if quiz else []) or []}

    reviews: list[QuizAnswerReview] = []
    for answer in attempt.answers or []:
        question = by_id.get(answer.get("question_id"), {})
        reviews.append(
            QuizAnswerReview(
                question_id=answer.get("question_id", ""),
                question=question.get("question", "This question is no longer available."),
                options=question.get("options", []),
                selected_index=answer.get("selected_index"),
                correct_index=answer.get("correct_index", question.get("correct_index", -1)),
                is_correct=bool(answer.get("is_correct")),
                explanation=question.get("explanation"),
                topic=question.get("topic"),
            )
        )
    return reviews


@router.post("/generate", response_model=QuizRead)
def generate_quiz(
    request: QuizGenerateRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Build a brand new quiz from a video's notes.

    Called every time the learner starts a quiz - nothing is reused, which is
    what makes the questions and their option order different on each attempt.
    """
    user_id = _current_user_id(current_user, db)

    video_repo = VideoRepository(db)
    video = video_repo.get_by_id(request.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to quiz on this video")

    note_repo = NoteRepository(db)
    note = note_repo.get_by_video_id(request.video_id)
    if not note:
        raise HTTPException(
            status_code=404,
            detail="No notes for this video yet. Generate notes before taking a quiz.",
        )

    difficulty = request.difficulty if request.difficulty in VALID_DIFFICULTIES else QuizDifficulty.MIXED

    quiz_repo = QuizRepository(db)
    avoid = quiz_repo.recent_question_texts(video.id, user_id)

    try:
        generated = QuizAIService().generate(
            markdown=note.markdown_content,
            title=video.title or "Study Notes",
            question_count=request.question_count,
            difficulty=difficulty,
            avoid_questions=avoid,
        )
    except QuizGenerationError as e:
        # The AI is the one thing here that can fail for reasons the learner can
        # act on (rate limits, a bad response), so the message is passed through.
        raise HTTPException(status_code=502, detail=str(e))

    quiz = quiz_repo.create(
        note_id=note.id,
        video_id=video.id,
        user_id=user_id,
        questions=generated.questions,
        title=video.title,
        difficulty=difficulty,
        model_used=generated.model_used,
    )

    logger.info(
        "quiz_created",
        quiz_id=str(quiz.id),
        video_id=str(video.id),
        user_id=str(user_id),
        questions=quiz.question_count,
    )
    return _quiz_response(quiz)


@router.get("/dashboard", response_model=QuizDashboard)
def quiz_dashboard(
    current_user: CurrentUser,
    recent_limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Headline stats, recent attempts and a per-video breakdown."""
    user_id = _current_user_id(current_user, db)
    quiz_repo = QuizRepository(db)

    totals = quiz_repo.totals(user_id)
    recent = quiz_repo.list_attempts(user_id, limit=recent_limit)
    by_video = quiz_repo.stats_by_video(user_id)

    return QuizDashboard(
        **totals,
        recent_attempts=[QuizAttemptSummary.model_validate(a) for a in recent],
        by_video=by_video,
    )


@router.get("/attempts", response_model=list[QuizAttemptSummary])
def list_attempts(
    current_user: CurrentUser,
    video_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Every past attempt, newest first; optionally just one video's."""
    user_id = _current_user_id(current_user, db)
    quiz_repo = QuizRepository(db)
    return quiz_repo.list_attempts(user_id, video_id=video_id, limit=limit, offset=offset)


@router.get("/attempts/{attempt_id}", response_model=QuizAttemptDetail)
def get_attempt(
    attempt_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """One past attempt, question by question, with the answers given."""
    user_id = _current_user_id(current_user, db)
    quiz_repo = QuizRepository(db)

    attempt = quiz_repo.get_attempt(attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    if attempt.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this attempt")

    quiz = quiz_repo.get_by_id(attempt.quiz_id)
    return QuizAttemptDetail(
        **QuizAttemptSummary.model_validate(attempt).model_dump(),
        answers=_attempt_review(attempt, quiz),
    )


@router.delete("/attempts/{attempt_id}", status_code=204)
def delete_attempt(
    attempt_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    user_id = _current_user_id(current_user, db)
    quiz_repo = QuizRepository(db)

    attempt = quiz_repo.get_attempt(attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    if attempt.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this attempt")

    quiz_repo.delete_attempt(attempt)


@router.get("/{quiz_id}", response_model=QuizRead)
def get_quiz(
    quiz_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Re-fetch a quiz in progress, e.g. after a page reload."""
    user_id = _current_user_id(current_user, db)
    quiz_repo = QuizRepository(db)

    quiz = quiz_repo.get_by_id(quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if quiz.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this quiz")

    return _quiz_response(quiz)


@router.post("/{quiz_id}/submit", response_model=QuizAttemptDetail)
def submit_quiz(
    quiz_id: uuid.UUID,
    request: QuizSubmitRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Grade a finished quiz and record the attempt.

    Grading is server-side because the answer key never leaves the server until
    this point; the browser only ever knows which option was clicked.
    """
    user_id = _current_user_id(current_user, db)
    quiz_repo = QuizRepository(db)

    quiz = quiz_repo.get_by_id(quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if quiz.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to submit this quiz")

    selected_by_id = {answer.question_id: answer.selected_index for answer in request.answers}

    graded: list[dict] = []
    correct_count = 0
    for question in quiz.questions or []:
        selected = selected_by_id.get(question["id"])
        # An out-of-range index is treated as unanswered rather than accepted.
        if selected is not None and not 0 <= selected < len(question["options"]):
            selected = None

        is_correct = selected is not None and selected == question["correct_index"]
        if is_correct:
            correct_count += 1

        graded.append(
            {
                "question_id": question["id"],
                "selected_index": selected,
                "correct_index": question["correct_index"],
                "is_correct": is_correct,
            }
        )

    total = len(graded)
    score_percent = round(correct_count * 100 / total) if total else 0

    attempt = quiz_repo.create_attempt(
        quiz_id=quiz.id,
        video_id=quiz.video_id,
        user_id=user_id,
        total_questions=total,
        correct_count=correct_count,
        score_percent=score_percent,
        answers=graded,
        title=quiz.title,
        duration_seconds=request.duration_seconds,
    )

    logger.info(
        "quiz_attempt_recorded",
        attempt_id=str(attempt.id),
        quiz_id=str(quiz.id),
        user_id=str(user_id),
        score=score_percent,
    )

    return QuizAttemptDetail(
        **QuizAttemptSummary.model_validate(attempt).model_dump(),
        answers=_attempt_review(attempt, quiz),
    )
