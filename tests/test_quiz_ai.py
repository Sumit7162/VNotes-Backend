"""Tests for turning a model reply into a usable quiz.

The model is the unreliable part of this feature: it fences its JSON, answers
with a letter instead of an index, repeats an option, or returns one broken
question in an otherwise good batch. These cover those shapes, plus the
randomisation the feature is built around - a quiz has to differ run to run.
"""

import random

import pytest

from app.services import quiz_ai
from app.services.quiz_ai import QuizGenerationError, _extract_json, _normalize_question, _trim_notes


def question(**overrides) -> dict:
    base = {
        "question": "What does a hash map give you on average lookup?",
        "options": ["O(1)", "O(n)", "O(log n)", "O(n log n)"],
        "correct_index": 0,
        "explanation": "Hashing goes straight to the bucket.",
        "topic": "Hash maps",
    }
    base.update(overrides)
    return base


class TestExtractJson:
    def test_reads_a_bare_array(self):
        assert _extract_json('[{"question": "a"}]') == [{"question": "a"}]

    def test_reads_a_fenced_array(self):
        raw = 'Here you go:\n```json\n[{"question": "a"}]\n```\nHope that helps!'
        assert _extract_json(raw) == [{"question": "a"}]

    def test_reads_an_array_wrapped_in_an_object(self):
        assert _extract_json('{"questions": [{"question": "a"}]}') == [{"question": "a"}]

    def test_reads_an_array_buried_in_prose(self):
        raw = 'Sure. [{"question": "a"}] Let me know if you want more.'
        assert _extract_json(raw) == [{"question": "a"}]

    def test_rejects_a_reply_with_no_json(self):
        with pytest.raises(QuizGenerationError):
            _extract_json("I am unable to help with that.")


class TestNormalizeQuestion:
    def test_keeps_the_right_answer_after_shuffling(self):
        # Whatever position the option lands in, correct_index has to follow it.
        for seed in range(25):
            result = _normalize_question(question(), random.Random(seed))
            assert result is not None
            assert result["options"][result["correct_index"]] == "O(1)"

    def test_accepts_a_letter_answer(self):
        result = _normalize_question(question(correct_index=None, answer="C"), random.Random(1))
        assert result["options"][result["correct_index"]] == "O(log n)"

    def test_accepts_the_answer_text_itself(self):
        result = _normalize_question(question(correct_index=None, answer="O(n)"), random.Random(1))
        assert result["options"][result["correct_index"]] == "O(n)"

    def test_drops_duplicate_options(self):
        result = _normalize_question(
            question(options=["O(1)", "O(1)", "O(n)", "O(log n)"]), random.Random(1)
        )
        assert sorted(result["options"]) == ["O(1)", "O(log n)", "O(n)"]
        assert result["options"][result["correct_index"]] == "O(1)"

    def test_rejects_an_out_of_range_answer(self):
        assert _normalize_question(question(correct_index=9), random.Random(1)) is None

    def test_rejects_too_few_options(self):
        assert _normalize_question(question(options=["yes", "no"]), random.Random(1)) is None

    def test_rejects_an_empty_question(self):
        assert _normalize_question(question(question="  "), random.Random(1)) is None

    def test_gives_every_question_its_own_id(self):
        ids = {_normalize_question(question(), random.Random(1))["id"] for _ in range(10)}
        assert len(ids) == 10


class TestTrimNotes:
    def test_short_notes_go_through_whole(self):
        notes = "# Title\n\nA short set of notes."
        assert _trim_notes(notes, random.Random(1)) == notes

    def test_long_notes_are_windowed_and_keep_the_heading(self):
        notes = "# Graph Theory\n\n" + "\n".join(f"Line {i} about graphs." for i in range(4000))
        trimmed = _trim_notes(notes, random.Random(1))
        assert len(trimmed) <= quiz_ai.MAX_CONTEXT_CHARS + len("# Graph Theory") + 2
        assert trimmed.startswith("# Graph Theory")

    def test_the_window_moves_between_runs(self):
        notes = "# Graph Theory\n\n" + "\n".join(f"Line {i} about graphs." for i in range(4000))
        windows = {_trim_notes(notes, random.SystemRandom()) for _ in range(6)}
        assert len(windows) > 1


class TestGenerate:
    def test_builds_a_quiz_from_a_model_reply(self, monkeypatch):
        payload = [question(question=f"Question {i}?") for i in range(5)]
        monkeypatch.setattr(
            quiz_ai.GroqAIService, "complete", lambda self, prompt, temperature=0.3: str(payload).replace("'", '"')
        )

        quiz = quiz_ai.QuizAIService().generate("# Notes\n\nSome material.", question_count=5)
        assert len(quiz.questions) == 5
        assert all(q["options"][q["correct_index"]] == "O(1)" for q in quiz.questions)

    def test_drops_broken_questions_but_keeps_the_batch(self, monkeypatch):
        import json

        payload = json.dumps(
            [question(question=f"Question {i}?") for i in range(4)]
            + [{"question": "Broken", "options": ["only one"], "correct_index": 0}]
        )
        monkeypatch.setattr(
            quiz_ai.GroqAIService, "complete", lambda self, prompt, temperature=0.3: payload
        )

        quiz = quiz_ai.QuizAIService().generate("# Notes\n\nSome material.", question_count=10)
        assert len(quiz.questions) == 4

    def test_rejects_a_batch_that_is_almost_all_broken(self, monkeypatch):
        import json

        payload = json.dumps([{"question": "Broken", "options": ["a"], "correct_index": 0}] * 5)
        monkeypatch.setattr(
            quiz_ai.GroqAIService, "complete", lambda self, prompt, temperature=0.3: payload
        )

        with pytest.raises(QuizGenerationError):
            quiz_ai.QuizAIService().generate("# Notes\n\nSome material.")

    def test_rejects_empty_notes(self):
        with pytest.raises(QuizGenerationError):
            quiz_ai.QuizAIService().generate("   ")

    def test_previous_questions_are_listed_as_off_limits(self, monkeypatch):
        import json

        seen: dict = {}

        def capture(self, prompt, temperature=0.3):
            seen["prompt"] = prompt
            return json.dumps([question(question=f"Question {i}?") for i in range(4)])

        monkeypatch.setattr(quiz_ai.GroqAIService, "complete", capture)

        quiz_ai.QuizAIService().generate(
            "# Notes\n\nSome material.",
            avoid_questions=["What is a spanning tree?"],
        )
        assert "What is a spanning tree?" in seen["prompt"]

    def test_two_generations_differ(self, monkeypatch):
        import json

        payload = json.dumps([question(question=f"Question {i}?") for i in range(8)])
        monkeypatch.setattr(
            quiz_ai.GroqAIService, "complete", lambda self, prompt, temperature=0.3: payload
        )

        service = quiz_ai.QuizAIService()
        orders = {
            tuple(q["question"] for q in service.generate("# Notes\n\nMaterial.").questions)
            for _ in range(8)
        }
        # Same model reply each time; the shuffling is what has to vary.
        assert len(orders) > 1
