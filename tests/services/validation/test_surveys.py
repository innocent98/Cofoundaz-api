import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import NotFound
from app.db.models.enums import SurveyStatus
from app.db.models.validation import SurveyResponse
from app.services.auth.sessions import hash_token
from app.services.validation.service import (
    create_survey,
    get_survey,
    list_surveys,
    response_counts,
    serialize_survey,
    survey_analytics,
    update_survey,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _questions():
    return [
        {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
        {"type": "nps", "prompt": "Recommend us?"},
        {"type": "open", "prompt": "Anything else?"},
    ]


def _answer(db, survey, answers):
    db.add(
        SurveyResponse(
            survey_id=survey.id,
            startup_id=survey.startup_id,
            answers=answers,
            submitted_at=datetime.now(UTC),
        )
    )
    db.flush()


def test_new_surveys_are_drafts_with_no_link(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    assert survey.status == SurveyStatus.draft and survey.token_hash is None
    assert [q["id"] for q in survey.questions]  # ids assigned by the validator


def test_opening_returns_the_raw_token_once_and_stores_only_its_hash(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    survey, raw = update_survey(db, survey, status=SurveyStatus.open)
    assert raw and len(raw) >= 32
    assert survey.token_hash == hash_token(raw)
    # Re-opening does not rotate the token, and never hands it out again.
    survey, again = update_survey(db, survey, status=SurveyStatus.closed)
    assert again is None
    survey, again = update_survey(db, survey, status=SurveyStatus.open)
    assert again is None and survey.token_hash == hash_token(raw)


def test_cross_workspace_is_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = create_survey(db, s.id, title="Mine")
    with pytest.raises(NotFound):
        get_survey(db, other.id, mine.id)
    with pytest.raises(NotFound):
        get_survey(db, s.id, uuid.uuid4())


def test_list_and_response_counts(db):
    _u, s = _ctx(db)
    first = create_survey(db, s.id, title="First", questions=_questions())
    second = create_survey(db, s.id, title="Second")
    _answer(db, first, {first.questions[0]["id"]: "A"})
    counts = response_counts(db, s.id, list_surveys(db, s.id))
    assert counts[str(first.id)] == 1 and counts[str(second.id)] == 0
    assert response_counts(db, s.id, []) == {}


def test_analytics_counts_every_option_and_the_completion_rate(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    choice, nps, open_q = (q["id"] for q in survey.questions)
    _answer(db, survey, {choice: "A", nps: 9, open_q: "loved it"})
    _answer(db, survey, {choice: "A", nps: 7})
    _answer(db, survey, {nps: 5})  # misses the required choice question
    out = survey_analytics(db, s.id, survey.id)
    assert out["responses"] == 3
    assert out["completion_rate"] == 67  # 2 of 3 answered every required question
    by_id = {q["id"]: q for q in out["questions"]}
    assert by_id[choice]["counts"] == {"A": 2, "B": 0}
    assert by_id[choice]["answered"] == 2
    assert by_id[nps]["counts"] == {"5": 1, "7": 1, "9": 1}
    assert by_id[nps]["average"] == 7.0
    assert by_id[open_q]["answered"] == 1
    assert "counts" not in by_id[open_q]  # open answers are counted, never listed


def test_analytics_on_an_empty_survey(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    out = survey_analytics(db, s.id, survey.id)
    assert out["responses"] == 0 and out["completion_rate"] == 0
    assert out["questions"][1]["average"] == 0.0


def test_serialize_shape_never_exposes_the_token(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    survey, _raw = update_survey(db, survey, status=SurveyStatus.open)
    out = serialize_survey(survey, 4)
    assert out["id"] == str(survey.id) and out["title"] == "Pricing"
    assert out["status"] == "open" and out["response_count"] == 4
    assert out["has_link"] is True
    assert "token" not in out and "token_hash" not in out
