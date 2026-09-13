import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.quiz import Quiz, QuizAttempt


class QuizRepository:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Quizzes
    # ------------------------------------------------------------------

    def create(
        self,
        note_id: uuid.UUID,
        video_id: uuid.UUID,
        user_id: uuid.UUID,
        questions: list[dict],
        title: Optional[str] = None,
        difficulty: str = "mixed",
        model_used: Optional[str] = None,
    ) -> Quiz:
        quiz = Quiz(
            note_id=note_id,
            video_id=video_id,
            user_id=user_id,
            questions=questions,
            question_count=len(questions),
            title=title,
            difficulty=difficulty,
            model_used=model_used,
        )
        self.db.add(quiz)
        self.db.commit()
        self.db.refresh(quiz)
        return quiz

    def get_by_id(self, quiz_id: uuid.UUID) -> Optional[Quiz]:
        return self.db.execute(select(Quiz).where(Quiz.id == quiz_id)).scalar_one_or_none()

    def recent_question_texts(
        self, video_id: uuid.UUID, user_id: uuid.UUID, quiz_limit: int = 4
    ) -> list[str]:
        """Questions from this user's last few quizzes on the same video.

        Fed back into the prompt as a do-not-repeat list, which is what keeps a
        second attempt on the same notes from being the same quiz again.
        """
        stmt = (
            select(Quiz.questions)
            .where(Quiz.video_id == video_id, Quiz.user_id == user_id)
            .order_by(Quiz.created_at.desc())
            .limit(quiz_limit)
        )
        texts: list[str] = []
        for (questions,) in self.db.execute(stmt).all():
            for question in questions or []:
                text = (question or {}).get("question")
                if text:
                    texts.append(str(text))
        return texts

    # ------------------------------------------------------------------
    # Attempts
    # ------------------------------------------------------------------

    def create_attempt(
        self,
        quiz_id: uuid.UUID,
        video_id: uuid.UUID,
        user_id: uuid.UUID,
        total_questions: int,
        correct_count: int,
        score_percent: int,
        answers: list[dict],
        title: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> QuizAttempt:
        attempt = QuizAttempt(
            quiz_id=quiz_id,
            video_id=video_id,
            user_id=user_id,
            total_questions=total_questions,
            correct_count=correct_count,
            score_percent=score_percent,
            answers=answers,
            title=title,
            duration_seconds=duration_seconds,
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    def get_attempt(self, attempt_id: uuid.UUID) -> Optional[QuizAttempt]:
        return self.db.execute(
            select(QuizAttempt).where(QuizAttempt.id == attempt_id)
        ).scalar_one_or_none()

    def list_attempts(
        self,
        user_id: uuid.UUID,
        video_id: Optional[uuid.UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[QuizAttempt]:
        stmt = select(QuizAttempt).where(QuizAttempt.user_id == user_id)
        if video_id:
            stmt = stmt.where(QuizAttempt.video_id == video_id)
        stmt = stmt.order_by(QuizAttempt.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def count_attempts(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(QuizAttempt).where(QuizAttempt.user_id == user_id)
        return int(self.db.execute(stmt).scalar_one() or 0)

    def totals(self, user_id: uuid.UUID) -> dict:
        """Headline numbers for the quiz dashboard, aggregated in the database."""
        stmt = select(
            func.count(QuizAttempt.id),
            func.coalesce(func.sum(QuizAttempt.total_questions), 0),
            func.coalesce(func.sum(QuizAttempt.correct_count), 0),
            func.coalesce(func.avg(QuizAttempt.score_percent), 0),
            func.coalesce(func.max(QuizAttempt.score_percent), 0),
            func.count(func.distinct(QuizAttempt.video_id)),
        ).where(QuizAttempt.user_id == user_id)

        attempts, answered, correct, average, best, videos = self.db.execute(stmt).one()
        return {
            "total_attempts": int(attempts or 0),
            "total_questions_answered": int(answered or 0),
            "total_correct": int(correct or 0),
            "average_score": round(float(average or 0)),
            "best_score": int(best or 0),
            "videos_quizzed": int(videos or 0),
        }

    def stats_by_video(self, user_id: uuid.UUID, limit: int = 20) -> list[dict]:
        stmt = (
            select(
                QuizAttempt.video_id,
                func.max(QuizAttempt.title),
                func.count(QuizAttempt.id),
                func.max(QuizAttempt.score_percent),
                func.avg(QuizAttempt.score_percent),
                func.max(QuizAttempt.created_at),
            )
            .where(QuizAttempt.user_id == user_id)
            .group_by(QuizAttempt.video_id)
            .order_by(func.max(QuizAttempt.created_at).desc())
            .limit(limit)
        )

        rows = self.db.execute(stmt).all()
        return [
            {
                "video_id": video_id,
                "title": title,
                "attempts": int(attempts or 0),
                "best_score": int(best or 0),
                "average_score": round(float(average or 0)),
                "last_attempt_at": last_at,
            }
            for video_id, title, attempts, best, average, last_at in rows
        ]

    def delete_attempt(self, attempt: QuizAttempt) -> None:
        self.db.delete(attempt)
        self.db.commit()
