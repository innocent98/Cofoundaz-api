from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)
from app.db.models.validation import Assumption, Experiment, Interview, Survey, SurveyResponse
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def test_assumption_defaults_to_untested(db):
    _u, s = _ctx(db)
    row = Assumption(startup_id=s.id, statement="Founders will pay", risk=RiskLevel.high)
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.status == AssumptionStatus.untested


def test_experiment_json_columns_default_empty(db):
    _u, s = _ctx(db)
    row = Experiment(startup_id=s.id, name="Landing page", type=ExperimentType.smoke_test)
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.config == {} and row.metrics == {} and row.assumption_ids == []
    assert row.status == ExperimentStatus.draft


def test_interview_stores_quotes_and_assumption_links(db):
    _u, s = _ctx(db)
    assumption = Assumption(startup_id=s.id, statement="x", risk=RiskLevel.low)
    db.add(assumption)
    db.flush()
    row = Interview(
        startup_id=s.id,
        interviewee="Ada",
        segment="fintech",
        held_on=date(2026, 9, 20),
        notes="Said yes without prompting.",
        key_quotes=["I would pay for this today"],
        verdict=InterviewVerdict.supports,
        assumption_ids=[str(assumption.id)],
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.key_quotes == ["I would pay for this today"]
    assert row.assumption_ids == [str(assumption.id)]


def test_survey_defaults_and_token_hash_is_unique(db):
    _u, s = _ctx(db)
    first = Survey(startup_id=s.id, title="Pricing", token_hash="a" * 64)
    db.add(first)
    db.flush()
    db.refresh(first)
    assert first.status == SurveyStatus.draft and first.questions == []
    db.add(Survey(startup_id=s.id, title="Second", token_hash="a" * 64))
    with pytest.raises(IntegrityError):
        db.flush()


def test_two_draft_surveys_may_both_have_no_token(db):
    _u, s = _ctx(db)
    db.add(Survey(startup_id=s.id, title="One"))
    db.add(Survey(startup_id=s.id, title="Two"))
    db.flush()  # NULL token_hash is not caught by the unique constraint


def test_response_is_anonymous_and_dies_with_its_survey(db):
    _u, s = _ctx(db)
    survey = Survey(startup_id=s.id, title="Pricing")
    db.add(survey)
    db.flush()
    db.add(
        SurveyResponse(
            survey_id=survey.id,
            startup_id=s.id,
            answers={"q1": 9},
            submitted_at=datetime.now(UTC),
        )
    )
    db.flush()
    assert "user_id" not in SurveyResponse.__table__.columns
    db.delete(survey)
    db.flush()
    assert db.query(SurveyResponse).filter_by(survey_id=survey.id).count() == 0
