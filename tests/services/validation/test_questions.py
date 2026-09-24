import pytest

from app.core.errors import AppError
from app.services.validation.questions import (
    MAX_OPEN_ANSWER,
    MAX_OPTIONS,
    MAX_QUESTIONS,
    validate_answers,
    validate_questions,
)


def _questions():
    return validate_questions(
        [
            {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
            {"type": "scale", "prompt": "How useful?"},
            {"type": "nps", "prompt": "Would you recommend us?"},
            {"type": "open", "prompt": "Anything else?"},
        ]
    )


def test_each_question_gets_a_stable_id_and_clean_shape():
    questions = _questions()
    assert [q["type"] for q in questions] == ["choice", "scale", "nps", "open"]
    assert all(q["id"] for q in questions)
    assert len({q["id"] for q in questions}) == 4
    assert questions[0]["required"] is True and questions[1]["required"] is False
    assert questions[0]["options"] == ["A", "B"]


def test_an_existing_id_is_kept_so_answers_keep_pointing_at_it():
    first = validate_questions([{"id": "q-1", "type": "open", "prompt": "Why?"}])
    again = validate_questions(first)
    assert again[0]["id"] == "q-1"


def test_bad_question_shapes_are_rejected():
    for bad in (
        "not a list",
        [{"type": "sketch", "prompt": "?"}],
        [{"type": "open", "prompt": "   "}],
        [{"type": "choice", "prompt": "Pick", "options": []}],
        [{"type": "choice", "prompt": "Pick", "options": ["ok", 7]}],
    ):
        with pytest.raises(AppError) as exc:
            validate_questions(bad)
        assert exc.value.http_status == 422


def test_the_caps_are_enforced():
    too_many = [{"type": "open", "prompt": f"Q{i}"} for i in range(MAX_QUESTIONS + 1)]
    with pytest.raises(AppError):
        validate_questions(too_many)
    too_wide = [
        {"type": "choice", "prompt": "Pick", "options": [f"o{i}" for i in range(MAX_OPTIONS + 1)]}
    ]
    with pytest.raises(AppError):
        validate_questions(too_wide)


def test_answers_are_returned_cleaned_and_optional_ones_may_be_missing():
    questions = _questions()
    ids = [q["id"] for q in questions]
    clean = validate_answers(
        questions,
        {ids[0]: "A", ids[1]: 4, ids[2]: 9, ids[3]: "  loved it  "},
    )
    assert clean == {ids[0]: "A", ids[1]: 4, ids[2]: 9, ids[3]: "loved it"}
    assert validate_answers(questions, {ids[0]: "B"}) == {ids[0]: "B"}


def test_a_required_question_must_be_answered():
    questions = _questions()
    with pytest.raises(AppError) as exc:
        validate_answers(questions, {questions[1]["id"]: 3})
    assert exc.value.http_status == 422


def test_an_unknown_question_id_is_rejected():
    questions = _questions()
    with pytest.raises(AppError):
        validate_answers(questions, {questions[0]["id"]: "A", "not-a-question": "hi"})


def test_answer_values_must_match_their_question_type():
    questions = _questions()
    choice, scale, nps, open_q = (q["id"] for q in questions)
    for answers in (
        {choice: "C"},
        {choice: "A", scale: 0},
        {choice: "A", scale: 6},
        {choice: "A", scale: "4"},
        {choice: "A", nps: 11},
        {choice: "A", nps: True},
        {choice: "A", open_q: 42},
        {choice: "A", open_q: "x" * (MAX_OPEN_ANSWER + 1)},
    ):
        with pytest.raises(AppError) as exc:
            validate_answers(questions, answers)
        assert exc.value.http_status == 422
