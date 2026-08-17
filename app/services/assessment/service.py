from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.assessment import Assessment, AssessmentAnswer
from app.db.models.enums import AssessmentStatus, AssessmentType
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.assessment.bank import ASSESSMENT_BANK, Question
from app.services.assessment.engine import next_question, validate_answer


def answered_map(db: Session, assessment: Assessment) -> dict[str, Any]:
    rows = db.query(AssessmentAnswer).filter(AssessmentAnswer.assessment_id == assessment.id).all()
    return {r.question_key: r.value_json for r in rows}


def serialize_question(q: Question | None) -> dict | None:
    if q is None:
        return None
    return {
        "key": q.key,
        "dimension": q.dimension.value,
        "section": q.section,
        "qtype": q.qtype,
        "options": q.options,
    }


def start_or_resume(db: Session, startup: Startup, user: User) -> Assessment:
    existing = (
        db.query(Assessment)
        .filter(
            Assessment.startup_id == startup.id,
            Assessment.status == AssessmentStatus.in_progress,
        )
        .first()
    )
    if existing is not None:
        return existing
    has_completed = (
        db.query(Assessment)
        .filter(
            Assessment.startup_id == startup.id,
            Assessment.status == AssessmentStatus.completed,
        )
        .first()
        is not None
    )
    a = Assessment(
        startup_id=startup.id,
        created_by=user.id,
        type=AssessmentType.quarterly if has_completed else AssessmentType.initial,
        status=AssessmentStatus.in_progress,
        bank_version=ASSESSMENT_BANK.version,
    )
    db.add(a)
    db.flush()
    return a


def submit_answer(
    db: Session, assessment: Assessment, startup: Startup, question_key: str, value: Any
) -> Question | None:
    if assessment.status != AssessmentStatus.in_progress:
        raise AppError("INVALID_ANSWER", "This assessment is not in progress.", 422)
    answers = answered_map(db, assessment)
    current = next_question(ASSESSMENT_BANK, answers, startup)
    if current is None or current.key != question_key:
        raise AppError("INVALID_ANSWER", "That isn't the current question.", 422)
    validate_answer(current, value)
    row = (
        db.query(AssessmentAnswer)
        .filter(
            AssessmentAnswer.assessment_id == assessment.id,
            AssessmentAnswer.question_key == question_key,
        )
        .first()
    )
    if row is None:
        db.add(
            AssessmentAnswer(
                assessment_id=assessment.id, question_key=question_key, value_json=value
            )
        )
    else:
        row.value_json = value
    db.flush()
    return next_question(ASSESSMENT_BANK, answered_map(db, assessment), startup)
