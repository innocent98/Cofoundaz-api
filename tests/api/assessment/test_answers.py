from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.assessment import Assessment, AssessmentAnswer
from app.db.models.enums import AssessmentStatus, MembershipRole
from tests.factories import create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_answer_advances_and_upserts(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    r = client.post(
        f"/api/v1/assessments/{aid}/answers",
        headers=h,
        json={"question_key": "product_stage", "value": "mvp"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["next_question"]["key"] != "product_stage"
    # re-answer the same (current-if-resubmitted) question upserts, not duplicates
    n = db.query(AssessmentAnswer).filter(AssessmentAnswer.question_key == "product_stage").count()
    assert n == 1


def test_answer_wrong_question_422(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    # market_clarity is not the current next question (product_stage is)
    r = client.post(
        f"/api/v1/assessments/{aid}/answers",
        headers=h,
        json={"question_key": "market_clarity", "value": 3},
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_ANSWER"


def test_answer_bad_value_422(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    r = client.post(
        f"/api/v1/assessments/{aid}/answers",
        headers=h,
        json={"question_key": "product_stage", "value": "bogus"},
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_ANSWER"


def test_answer_not_in_progress_422(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]

    a = db.query(Assessment).filter(Assessment.id == aid).first()
    a.status = AssessmentStatus.completed
    db.commit()

    r = client.post(
        f"/api/v1/assessments/{aid}/answers",
        headers=h,
        json={"question_key": "product_stage", "value": "mvp"},
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_ANSWER"
