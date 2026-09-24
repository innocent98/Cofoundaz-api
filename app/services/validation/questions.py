"""Survey question and answer validation (Module 09).

Questions live as JSONB on ``surveys`` and answers as JSONB on ``survey_responses``; both are
checked here rather than modelled as tables (spec D7), the same way ``validate_sections`` checks
document sections in Module 18.

Every message raised here is safe to show a member of the public: it describes the respondent's
own answer and never anything about the survey's workspace (spec section 4).
"""

import uuid
from typing import Any

from app.core.errors import AppError

MAX_QUESTIONS = 50
MAX_OPTIONS = 20
MAX_OPEN_ANSWER = 4000
QUESTION_TYPES = ("choice", "scale", "nps", "open")
SCALE_MIN, SCALE_MAX = 1, 5
NPS_MIN, NPS_MAX = 0, 10


def _invalid(message: str, field: str | None = None) -> AppError:
    field_errors = [{"field": field, "message": message}] if field else None
    return AppError("VALIDATION_ERROR", message, 422, field_errors=field_errors)


def validate_questions(questions: Any) -> list[dict[str, Any]]:
    """Return the cleaned question list, giving every question a stable id.

    An id already present is kept, so editing a survey does not orphan the answers already
    collected against its questions.
    """
    if not isinstance(questions, list):
        raise _invalid("Questions must be a list.")
    if len(questions) > MAX_QUESTIONS:
        raise _invalid(f"A survey may have at most {MAX_QUESTIONS} questions.")
    return [_validate_question(question, index) for index, question in enumerate(questions)]


def _validate_question(question: Any, index: int) -> dict[str, Any]:
    field = f"questions.{index}"
    if not isinstance(question, dict):
        raise _invalid("Each question must be an object.", field)
    qtype = question.get("type")
    if qtype not in QUESTION_TYPES:
        raise _invalid(f"Question type must be one of: {', '.join(QUESTION_TYPES)}.", field)
    prompt = question.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _invalid("Each question needs a prompt.", field)
    clean: dict[str, Any] = {
        "id": str(question.get("id") or uuid.uuid4()),
        "type": qtype,
        "prompt": prompt.strip(),
        "required": bool(question.get("required", False)),
    }
    if qtype == "choice":
        clean["options"] = _validate_options(question.get("options"), field)
    return clean


def _validate_options(options: Any, field: str) -> list[str]:
    if not isinstance(options, list) or not options:
        raise _invalid("A choice question needs at least one option.", field)
    if len(options) > MAX_OPTIONS:
        raise _invalid(f"A choice question may have at most {MAX_OPTIONS} options.", field)
    if any(not isinstance(option, str) or not option.strip() for option in options):
        raise _invalid("Choice options must be non-empty text.", field)
    return [option.strip() for option in options]


def validate_answers(questions: list[dict[str, Any]], answers: Any) -> dict[str, Any]:
    """Check a respondent's answers against the survey's own questions."""
    if not isinstance(answers, dict):
        raise _invalid("Answers must be an object keyed by question id.")
    by_id = {question["id"]: question for question in questions}
    unknown = sorted(set(answers) - set(by_id))
    if unknown:
        raise _invalid("This question is not part of the survey.", f"answers.{unknown[0]}")
    clean: dict[str, Any] = {}
    for question_id, question in by_id.items():
        if question_id not in answers:
            if question["required"]:
                raise _invalid("This question is required.", f"answers.{question_id}")
            continue
        clean[question_id] = _validate_answer(question, answers[question_id])
    return clean


def _validate_answer(question: dict[str, Any], value: Any) -> Any:
    field = f"answers.{question['id']}"
    qtype = question["type"]
    if qtype == "choice":
        if value not in question.get("options", []):
            raise _invalid("Pick one of the offered options.", field)
        return value
    if qtype == "scale":
        return _whole_number(value, SCALE_MIN, SCALE_MAX, field)
    if qtype == "nps":
        return _whole_number(value, NPS_MIN, NPS_MAX, field)
    if not isinstance(value, str):
        raise _invalid("This answer must be text.", field)
    text = value.strip()
    if len(text) > MAX_OPEN_ANSWER:
        raise _invalid(f"Keep this answer under {MAX_OPEN_ANSWER} characters.", field)
    return text


def _whole_number(value: Any, low: int, high: int, field: str) -> int:
    # ``bool`` is a subclass of ``int`` in Python, so True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _invalid(f"This answer must be a whole number from {low} to {high}.", field)
    return value
