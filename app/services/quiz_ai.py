"""Turn a set of generated notes into a multiple-choice quiz.

Every call produces a different quiz from the same notes. Three things make
that true, and all three matter:

1. A random slice of the notes is put in front of the model, along with a
   randomly chosen angle to ask from, so the raw material differs run to run.
2. The questions asked in recent quizzes for the same notes are listed in the
   prompt as material to avoid repeating.
3. The option order is shuffled here, after the model answers, so even a
   question that does come back twice does not have its answer in the same
   position - and "always C" cannot be a strategy.
"""

import json
import random
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from app.services.groq_ai import GroqAIError, GroqAIService
from app.utils.logger import get_logger

logger = get_logger(__name__)

# How much of the notes to put in one prompt. Groq's free tier meters tokens per
# minute, and this keeps a quiz request plus its reply inside that window.
MAX_CONTEXT_CHARS = 9000
OPTIONS_PER_QUESTION = 4
# Beyond this many remembered questions the "don't repeat these" list starts
# crowding out the notes themselves.
MAX_AVOID_QUESTIONS = 40

# Each generation picks one of these, which is the cheapest way to move the
# model off the handful of "obvious" questions a set of notes invites.
ANGLES = [
    "definitions and terminology",
    "cause and effect, and why things work the way they do",
    "applying a concept to a new, concrete situation",
    "comparing and contrasting two ideas from the notes",
    "reading or reasoning about the code, formulas or worked examples",
    "common mistakes and misconceptions a learner would have",
    "the order of steps in a process or derivation",
    "picking the right tool, method or approach for a stated goal",
]

DIFFICULTY_GUIDANCE = {
    "easy": "Keep questions direct: recall and recognition of what the notes state.",
    "medium": "Ask questions that need understanding, not just recall - short reasoning steps.",
    "hard": "Ask demanding questions: multi-step reasoning, edge cases, and subtle distinctions.",
    "mixed": (
        "Vary the difficulty: roughly a third straightforward recall, a third "
        "understanding, and a third demanding multi-step reasoning."
    ),
}

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class QuizGenerationError(Exception):
    """Raised when a usable quiz could not be produced from the notes."""


@dataclass
class GeneratedQuiz:
    questions: list[dict]
    model_used: str


def _trim_notes(markdown: str, rng: random.Random) -> str:
    """Return a slice of the notes to build questions from.

    Short notes go through whole. For longer notes a random window is taken so
    that successive quizzes are drawn from different parts of the material
    instead of forever re-asking about the opening section.
    """
    text = markdown.strip()
    if len(text) <= MAX_CONTEXT_CHARS:
        return text

    start = rng.randint(0, len(text) - MAX_CONTEXT_CHARS)
    # Start on a line boundary so the excerpt does not open mid-sentence.
    newline = text.find("\n", start)
    if newline != -1 and newline - start < 400:
        start = newline + 1

    window = text[start : start + MAX_CONTEXT_CHARS]
    # Always include the document heading: it tells the model what the subject
    # is, which a middle slice on its own may not.
    heading = next((line for line in text.split("\n") if line.startswith("# ")), "")
    return f"{heading}\n\n{window}".strip() if heading else window


def _extract_json(raw: str) -> list:
    """Pull the question array out of a model reply.

    Models wrap JSON in prose or a code fence often enough that giving up on
    the first failure would make the feature flaky.
    """
    candidates: list[str] = []

    fenced = _FENCE.search(raw)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(raw)

    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            for key in ("questions", "quiz", "items"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
            continue
        if isinstance(parsed, list):
            return parsed

    raise QuizGenerationError("The AI response could not be read as quiz JSON.")


def _normalize_question(raw: dict, rng: random.Random) -> Optional[dict]:
    """Validate one question and shuffle its options.

    Returns None for anything malformed: one unusable question should cost that
    question, not the whole quiz.
    """
    if not isinstance(raw, dict):
        return None

    text = str(raw.get("question") or "").strip()
    options = raw.get("options")
    if not text or not isinstance(options, list):
        return None

    cleaned = [str(option).strip() for option in options if str(option).strip()]
    # Duplicated options make a question unanswerable - two right answers, or a
    # give-away. Drop the repeats, preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for option in cleaned:
        key = option.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(option)

    if len(unique) < 3:
        return None
    unique = unique[:OPTIONS_PER_QUESTION]

    # Take the first key that actually carries a value: a model that emits
    # "correct_index": null alongside a filled-in "answer" is still answerable.
    answer = next(
        (
            raw[key]
            for key in ("correct_index", "answer_index", "answer", "correct_answer")
            if raw.get(key) is not None
        ),
        None,
    )
    correct_index: Optional[int] = None

    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        correct_index = answer
    elif isinstance(answer, str):
        label = answer.strip()
        # Models answer either with the letter ("B"), the index, or the option
        # text itself; all three are accepted.
        if len(label) == 1 and label.upper() in "ABCDE":
            correct_index = ord(label.upper()) - ord("A")
        elif label.isdigit():
            correct_index = int(label)
        else:
            correct_index = next(
                (i for i, option in enumerate(unique) if option.casefold() == label.casefold()),
                None,
            )

    if correct_index is None or not 0 <= correct_index < len(unique):
        return None

    correct_option = unique[correct_index]
    shuffled = unique[:]
    rng.shuffle(shuffled)

    return {
        "id": uuid.uuid4().hex[:12],
        "question": text,
        "options": shuffled,
        "correct_index": shuffled.index(correct_option),
        "explanation": str(raw.get("explanation") or "").strip() or None,
        "topic": str(raw.get("topic") or "").strip() or None,
    }


class QuizAIService:
    """Builds quizzes from note markdown using the same Groq client as notes."""

    def __init__(self) -> None:
        self.ai = GroqAIService()

    def generate(
        self,
        markdown: str,
        title: str = "Study Notes",
        question_count: int = 10,
        difficulty: str = "mixed",
        avoid_questions: Optional[list[str]] = None,
    ) -> GeneratedQuiz:
        if not markdown or not markdown.strip():
            raise QuizGenerationError("These notes are empty, so there is nothing to quiz on.")

        rng = random.SystemRandom()
        excerpt = _trim_notes(markdown, rng)
        angle = rng.choice(ANGLES)
        guidance = DIFFICULTY_GUIDANCE.get(difficulty, DIFFICULTY_GUIDANCE["mixed"])
        # A nonce in the prompt stops an identical request from being served
        # from any upstream cache, which would defeat the whole point.
        nonce = uuid.uuid4().hex[:8]

        avoid_block = ""
        recent = [q for q in (avoid_questions or []) if q][:MAX_AVOID_QUESTIONS]
        if recent:
            listed = "\n".join(f"- {q}" for q in recent)
            avoid_block = (
                "\nThe learner has already been asked the questions below in earlier quizzes on\n"
                "this same material. Do NOT ask any of them again, and do not ask a lightly\n"
                "reworded version of them. Find different facts, examples and angles:\n"
                f"{listed}\n"
            )

        prompt = f"""You are an expert examiner writing a multiple-choice quiz from a student's study notes.

Study material title: {title}
Generation id: {nonce}

Study notes:
---
{excerpt}
---

Write exactly {question_count} multiple-choice questions.

Requirements:
- Every question must be answerable from the study notes above. Never invent facts that are not supported by them.
- This quiz should lean towards: {angle}.
- {guidance}
- Each question has exactly {OPTIONS_PER_QUESTION} options, only one of which is correct.
- The wrong options must be plausible and of similar length and specificity to the correct one. Never use "All of the above", "None of the above", or joke options.
- Do not number the questions, and do not put option letters (A., B.) inside the option text.
- If the notes contain code, ask at least one question about what the code does or how to fix it.
- If the notes contain formulas, ask at least one question that requires using one. Write any maths in plain text, not LaTeX.
- Give a one or two sentence explanation of why the correct answer is right.
- "topic" is the short section or concept from the notes the question comes from.
{avoid_block}
Return ONLY a JSON array, with no prose before or after it, in exactly this shape:

[
  {{
    "question": "...",
    "options": ["...", "...", "...", "..."],
    "correct_index": 0,
    "explanation": "...",
    "topic": "..."
  }}
]"""

        try:
            # A high temperature is deliberate: this is the one call in the app
            # where variety between runs is the point.
            raw = self.ai.complete(prompt, temperature=0.9)
        except GroqAIError as e:
            raise QuizGenerationError(f"The quiz could not be generated: {e}") from e

        parsed = _extract_json(raw)

        questions: list[dict] = []
        seen_questions: set[str] = set()
        for item in parsed:
            question = _normalize_question(item, rng)
            if not question:
                continue
            # The model occasionally repeats itself inside one response.
            key = question["question"].casefold()
            if key in seen_questions:
                continue
            seen_questions.add(key)
            questions.append(question)

        if len(questions) < 3:
            raise QuizGenerationError(
                "The AI did not return enough usable questions. Please try again."
            )

        rng.shuffle(questions)
        questions = questions[:question_count]

        logger.info(
            "quiz_generated",
            title=title,
            requested=question_count,
            produced=len(questions),
            difficulty=difficulty,
            angle=angle,
        )
        return GeneratedQuiz(questions=questions, model_used=self.ai.model)
