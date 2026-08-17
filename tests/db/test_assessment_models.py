import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.assessment import AssessmentAnswer, AssessmentResult
from app.db.models.enums import AssessmentStatus, AssessmentType
from tests.factories import create_assessment, create_startup, create_user


def test_assessment_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u)
    assert a.status == AssessmentStatus.in_progress and a.type == AssessmentType.initial
    assert a.bank_version


def test_one_in_progress_per_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_assessment(db, s, creator=u)
    db.flush()
    with pytest.raises(IntegrityError):
        create_assessment(db, s, creator=u)


def test_answer_upsert_unique(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u)
    db.add(AssessmentAnswer(assessment_id=a.id, question_key="q1", value_json="yes"))
    db.flush()
    db.add(AssessmentAnswer(assessment_id=a.id, question_key="q1", value_json="no"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_result_roundtrip(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u)
    db.add(
        AssessmentResult(
            assessment_id=a.id,
            dimension_scores={"product": 60, "market": 40, "money": 50, "legal": 70, "team": 55},
            overall_provisional=55,
            narrative="Strongest: legal.",
        )
    )
    db.flush()
    r = db.query(AssessmentResult).filter_by(assessment_id=a.id).one()
    assert r.dimension_scores["legal"] == 70 and r.overall_provisional == 55
