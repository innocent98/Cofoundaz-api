import uuid
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_next_question_for_resume(client, db):
    u, s, h = _founder(db)
    db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    r = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["next_question"]["key"] == "product_stage"


def test_next_question_foreign_assessment_404(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.get(f"/api/v1/assessments/{uuid.uuid4()}/next-question", headers=h)
    assert r.status_code == 404
