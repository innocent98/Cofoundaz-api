from app.db.models.enums import AssessmentStatus, AssessmentType
from app.services.assessment.service import start_or_resume
from tests.factories import create_assessment, create_startup, create_user


def test_start_or_resume_creates_quarterly_after_prior_completion(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_assessment(db, s, creator=u, status=AssessmentStatus.completed)
    db.flush()

    a = start_or_resume(db, s, u)

    assert a.type == AssessmentType.quarterly
    assert a.status == AssessmentStatus.in_progress
