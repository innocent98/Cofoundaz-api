from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.business import BusinessPlan
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.job import Job
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_generate_enqueues_and_creates_plan(client, db):
    _u, s, h = _member(db)
    r = client.post("/api/v1/business-builder/plan/generate", headers=h)
    assert r.status_code == 202, r.text
    body = r.json()["data"]
    assert body["status"] == "generating" and body["plan_id"]
    assert db.query(BusinessPlan).filter_by(startup_id=s.id).count() == 1
    job = db.query(Job).filter(Job.type == "business.plan.generate").one()
    assert job.payload["startup_id"] == str(s.id)
    assert job.payload["plan_id"] == body["plan_id"]


def test_get_plan_returns_latest_or_404(client, db):
    _u, _s, h = _member(db)
    r0 = client.get("/api/v1/business-builder/plan", headers=h)
    assert r0.status_code == 404
    client.post("/api/v1/business-builder/plan/generate", headers=h)
    r1 = client.get("/api/v1/business-builder/plan", headers=h)
    assert r1.status_code == 200
    data = r1.json()["data"]
    assert data["status"] in ("generating", "complete")
    assert data["document_id"] is None
    assert data["created_at"]


def test_generate_mentor_forbidden_403(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor)
    r = client.post("/api/v1/business-builder/plan/generate", headers=h)
    assert r.status_code == 403
