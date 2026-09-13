"""End-to-end tests for the quiz endpoints.

These walk the route a learner actually takes - generate a quiz from notes,
answer it, get it graded, then find it again on the dashboard - because the
parts that matter most here are the joins between those steps: that the answer
key never goes out with the questions, that grading is done server-side, and
that an attempt can still be replayed afterwards.
"""

import uuid

import pytest

from app.api import quizzes as quizzes_api
from app.middleware.auth import get_current_user
from app.main import app
from app.models.note import Note
from app.models.user import User
from app.models.video import Video, VideoStatus
from app.schemas.user import UserFromGoogle
from app.services.quiz_ai import GeneratedQuiz, QuizGenerationError

NOTES = """# Binary Search Trees - Study Notes

## Key Points
- **In-order traversal** of a BST yields the keys in sorted order.
- Lookup is O(h), where h is the height of the tree.
"""


def fake_questions(count: int = 5) -> list[dict]:
    return [
        {
            "id": f"q{i}",
            "question": f"Question {i}?",
            "options": ["right", "wrong one", "wrong two", "wrong three"],
            # A different correct position per question, so a client that
            # guessed "always index 0" would not pass by accident.
            "correct_index": i % 4,
            "explanation": f"Because of reason {i}.",
            "topic": "Binary search trees",
        }
        for i in range(count)
    ]


@pytest.fixture
def authed(client, db_session, monkeypatch):
    """A signed-in user with one completed video and a set of notes."""
    user = User(
        id=uuid.uuid4(),
        google_id="google-quiz-user",
        email="quiz@example.com",
        full_name="Quiz User",
    )
    db_session.add(user)

    video = Video(
        id=uuid.uuid4(),
        user_id=user.id,
        youtube_url="https://youtube.com/watch?v=abc",
        title="Binary Search Trees",
        status=VideoStatus.COMPLETED,
        duration_seconds=600,
    )
    db_session.add(video)

    note = Note(id=uuid.uuid4(), video_id=video.id, markdown_content=NOTES, model_used="test-model")
    db_session.add(note)
    db_session.commit()

    app.dependency_overrides[get_current_user] = lambda: UserFromGoogle(
        google_id=user.google_id, email=user.email, full_name=user.full_name
    )

    # The AI is stubbed: these tests are about the endpoints, not the model.
    monkeypatch.setattr(
        quizzes_api.QuizAIService,
        "generate",
        lambda self, **kwargs: GeneratedQuiz(
            questions=fake_questions(kwargs.get("question_count", 5)),
            model_used="test-model",
        ),
    )

    yield {"client": client, "user": user, "video": video, "note": note}

    app.dependency_overrides.pop(get_current_user, None)


def generate(authed, **body) -> dict:
    payload = {"video_id": str(authed["video"].id), "question_count": 5, **body}
    response = authed["client"].post("/api/quizzes/generate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


class TestGenerate:
    def test_returns_questions_without_the_answer_key(self, authed):
        quiz = generate(authed)

        assert quiz["question_count"] == 5
        assert len(quiz["questions"]) == 5
        for question in quiz["questions"]:
            assert set(question) == {"id", "question", "options", "topic"}
            # The single most important assertion in this file.
            assert "correct_index" not in question

    def test_rejects_a_video_with_no_notes(self, authed, db_session):
        bare = Video(
            id=uuid.uuid4(),
            user_id=authed["user"].id,
            youtube_url="https://youtube.com/watch?v=xyz",
            title="No notes yet",
            status=VideoStatus.COMPLETED,
        )
        db_session.add(bare)
        db_session.commit()

        response = authed["client"].post(
            "/api/quizzes/generate", json={"video_id": str(bare.id)}
        )
        assert response.status_code == 404

    def test_rejects_another_users_video(self, authed, db_session):
        stranger = User(
            id=uuid.uuid4(), google_id="someone-else", email="other@example.com"
        )
        db_session.add(stranger)
        their_video = Video(
            id=uuid.uuid4(),
            user_id=stranger.id,
            youtube_url="https://youtube.com/watch?v=private",
            status=VideoStatus.COMPLETED,
        )
        db_session.add(their_video)
        db_session.commit()

        response = authed["client"].post(
            "/api/quizzes/generate", json={"video_id": str(their_video.id)}
        )
        assert response.status_code == 403

    def test_surfaces_an_ai_failure(self, authed, monkeypatch):
        def boom(self, **kwargs):
            raise QuizGenerationError("Groq rate limit exceeded")

        monkeypatch.setattr(quizzes_api.QuizAIService, "generate", boom)

        response = authed["client"].post(
            "/api/quizzes/generate", json={"video_id": str(authed["video"].id)}
        )
        assert response.status_code == 502
        assert "rate limit" in response.json()["detail"]

    def test_earlier_questions_are_passed_on_as_off_limits(self, authed, monkeypatch):
        generate(authed)

        seen: dict = {}

        def capture(self, **kwargs):
            seen.update(kwargs)
            return GeneratedQuiz(questions=fake_questions(5), model_used="test-model")

        monkeypatch.setattr(quizzes_api.QuizAIService, "generate", capture)
        generate(authed)

        assert "Question 0?" in seen["avoid_questions"]


class TestSubmit:
    def test_grades_a_perfect_run(self, authed):
        quiz = generate(authed)
        # The client cannot see the key, so the test reproduces it the same way
        # the fixture built it.
        answers = [
            {"question_id": q["id"], "selected_index": index % 4}
            for index, q in enumerate(quiz["questions"])
        ]

        response = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit",
            json={"answers": answers, "duration_seconds": 90},
        )
        assert response.status_code == 200

        result = response.json()
        assert result["score_percent"] == 100
        assert result["correct_count"] == 5
        assert result["duration_seconds"] == 90
        assert all(answer["is_correct"] for answer in result["answers"])
        # The review does carry the key - grading has happened by now.
        assert all("correct_index" in answer for answer in result["answers"])

    def test_grades_a_mixed_run(self, authed):
        quiz = generate(authed)
        answers = [
            {"question_id": q["id"], "selected_index": (index % 4 if index < 3 else (index + 1) % 4)}
            for index, q in enumerate(quiz["questions"])
        ]

        result = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers}
        ).json()

        assert result["correct_count"] == 3
        assert result["score_percent"] == 60

    def test_unanswered_questions_count_as_wrong(self, authed):
        quiz = generate(authed)
        answers = [{"question_id": quiz["questions"][0]["id"], "selected_index": 0}]

        result = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers}
        ).json()

        assert result["total_questions"] == 5
        assert result["correct_count"] == 1
        skipped = [a for a in result["answers"] if a["selected_index"] is None]
        assert len(skipped) == 4

    def test_an_out_of_range_choice_is_treated_as_unanswered(self, authed):
        quiz = generate(authed)
        answers = [{"question_id": q["id"], "selected_index": 99} for q in quiz["questions"]]

        result = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers}
        ).json()

        assert result["correct_count"] == 0
        assert all(answer["selected_index"] is None for answer in result["answers"])

    def test_rejects_another_users_quiz(self, authed, db_session):
        quiz = generate(authed)
        app.dependency_overrides[get_current_user] = lambda: UserFromGoogle(
            google_id="intruder", email="intruder@example.com"
        )

        response = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit", json={"answers": []}
        )
        assert response.status_code == 403


class TestDashboard:
    def test_is_empty_before_any_attempt(self, authed):
        data = authed["client"].get("/api/quizzes/dashboard").json()
        assert data["total_attempts"] == 0
        assert data["recent_attempts"] == []
        assert data["by_video"] == []

    def test_aggregates_attempts(self, authed):
        # Two runs on the same notes: one perfect, one all wrong.
        first = generate(authed)
        authed["client"].post(
            f"/api/quizzes/{first['id']}/submit",
            json={
                "answers": [
                    {"question_id": q["id"], "selected_index": index % 4}
                    for index, q in enumerate(first["questions"])
                ]
            },
        )

        second = generate(authed)
        authed["client"].post(
            f"/api/quizzes/{second['id']}/submit",
            json={
                "answers": [
                    {"question_id": q["id"], "selected_index": (index + 1) % 4}
                    for index, q in enumerate(second["questions"])
                ]
            },
        )

        data = authed["client"].get("/api/quizzes/dashboard").json()
        assert data["total_attempts"] == 2
        assert data["best_score"] == 100
        assert data["videos_quizzed"] == 1
        assert data["total_questions_answered"] == 10
        assert len(data["recent_attempts"]) == 2
        # Newest first.
        assert (
            data["recent_attempts"][0]["created_at"] >= data["recent_attempts"][1]["created_at"]
        )

        by_video = data["by_video"][0]
        assert by_video["attempts"] == 2
        assert by_video["best_score"] == 100
        assert by_video["title"] == "Binary Search Trees"

    def test_an_attempt_can_be_replayed_in_full(self, authed):
        quiz = generate(authed)
        submitted = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit",
            json={
                "answers": [
                    {"question_id": q["id"], "selected_index": 0} for q in quiz["questions"]
                ]
            },
        ).json()

        detail = authed["client"].get(f"/api/quizzes/attempts/{submitted['id']}").json()
        assert detail["id"] == submitted["id"]
        assert len(detail["answers"]) == 5
        # The wording has to survive, or the review page has nothing to show.
        assert detail["answers"][0]["question"] == "Question 0?"
        assert detail["answers"][0]["options"]
        assert detail["answers"][0]["explanation"] == "Because of reason 0."

    def test_attempts_can_be_filtered_by_video(self, authed):
        quiz = generate(authed)
        authed["client"].post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": []})

        mine = authed["client"].get(
            "/api/quizzes/attempts", params={"video_id": str(authed["video"].id)}
        ).json()
        assert len(mine) == 1

        other = authed["client"].get(
            "/api/quizzes/attempts", params={"video_id": str(uuid.uuid4())}
        ).json()
        assert other == []

    def test_an_attempt_can_be_deleted(self, authed):
        quiz = generate(authed)
        attempt = authed["client"].post(
            f"/api/quizzes/{quiz['id']}/submit", json={"answers": []}
        ).json()

        assert authed["client"].delete(f"/api/quizzes/attempts/{attempt['id']}").status_code == 204
        assert authed["client"].get(f"/api/quizzes/attempts/{attempt['id']}").status_code == 404
