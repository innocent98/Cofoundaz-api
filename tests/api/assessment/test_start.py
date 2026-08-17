from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.assessment import Assessment
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user


def _founder(db, *, verified: bool = True):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_start_creates_initial_with_next_question(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.post("/api/v1/assessments", headers=h)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["type"] == "initial" and data["status"] == "in_progress"
    assert data["next_question"]["key"] == "product_stage"


def test_start_resumes_existing(client, db):
    u, s, h = _founder(db)
    db.commit()
    first = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    again = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    assert first == again  # resumed, not a 2nd assessment
    assert db.query(Assessment).filter(Assessment.startup_id == s.id).count() == 1


def test_start_requires_founder(client, db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.team_member)
    db.commit()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    assert client.post("/api/v1/assessments", headers=h).status_code == 403


def test_start_requires_verified_email(client, db):
    u, s, h = _founder(db, verified=False)
    db.commit()
    r = client.post("/api/v1/assessments", headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
