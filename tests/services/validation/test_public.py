from datetime import UTC, datetime

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import SurveyStatus
from app.db.models.validation import SurveyResponse
from app.services.validation.public import open_survey, public_view, submit_response
from app.services.validation.service import create_survey, update_survey
from tests.factories import create_startup, create_user


def _questions():
    return [
        {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
        {"type": "open", "prompt": "Anything else?"},
    ]


def _open_survey(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    survey, raw = update_survey(db, survey, status=SurveyStatus.open)
    return s, survey, raw


def test_a_valid_token_opens_the_survey(db):
    _s, survey, raw = _open_survey(db)
    assert open_survey(db, raw).id == survey.id


def test_unknown_draft_and_closed_all_raise_the_same_404(db):
    _s, survey, raw = _open_survey(db)
    with pytest.raises(NotFound):
        open_survey(db, "not-a-real-token")
    update_survey(db, survey, status=SurveyStatus.closed)
    with pytest.raises(NotFound):
        open_survey(db, raw)
    update_survey(db, survey, status=SurveyStatus.draft)
    with pytest.raises(NotFound):
        open_survey(db, raw)


def test_the_public_view_shows_the_form_and_nothing_else(db):
    _s, survey, raw = _open_survey(db)
    view = public_view(open_survey(db, raw))
    assert set(view) == {"title", "questions"}
    assert view["title"] == "Pricing"
    assert set(view["questions"][0]) == {"id", "type", "prompt", "required", "options"}
    assert set(view["questions"][1]) == {"id", "type", "prompt", "required"}
    # Nothing about the workspace, the owner, the status or the responses.
    flat = str(view)
    assert str(survey.startup_id) not in flat
    assert str(survey.id) not in flat
    assert "response" not in flat and "status" not in flat


def test_submitting_stores_clean_answers_and_copies_the_workspace(db):
    s, survey, raw = _open_survey(db)
    choice, open_q = (q["id"] for q in survey.questions)
    before = datetime.now(UTC)
    row = submit_response(db, raw, {choice: "A", open_q: "  loved it  "})
    assert row.answers == {choice: "A", open_q: "loved it"}
    assert row.startup_id == s.id and row.survey_id == survey.id
    assert row.submitted_at >= before


def test_bad_answers_are_rejected_with_422(db):
    _s, survey, raw = _open_survey(db)
    choice, _open_q = (q["id"] for q in survey.questions)
    for answers in ({}, {choice: "C"}, {"not-a-question": "hi"}):
        with pytest.raises(AppError) as exc:
            submit_response(db, raw, answers)
        assert exc.value.http_status == 422


def test_repeat_submissions_are_allowed(db):
    _s, survey, raw = _open_survey(db)
    choice, _open_q = (q["id"] for q in survey.questions)
    submit_response(db, raw, {choice: "A"})
    submit_response(db, raw, {choice: "B"})
    assert db.query(SurveyResponse).filter_by(survey_id=survey.id).count() == 2


def test_a_closed_survey_stops_accepting_answers(db):
    _s, survey, raw = _open_survey(db)
    choice, _open_q = (q["id"] for q in survey.questions)
    update_survey(db, survey, status=SurveyStatus.closed)
    with pytest.raises(NotFound):
        submit_response(db, raw, {choice: "A"})
