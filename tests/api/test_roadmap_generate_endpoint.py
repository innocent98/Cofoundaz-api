from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.roadmap import Roadmap
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_generate_returns_succeeded_job(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    r = client.post("/api/v1/roadmap/generate", headers=h)
    assert r.status_code == 202, r.text
    data = r.json()["data"]
    assert data["status"] == "succeeded"
    assert data["job_id"]


def test_generate_is_create_once_via_endpoint(db, client):
    _u, s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    client.post("/api/v1/roadmap/generate", headers=h)
    client.post("/api/v1/roadmap/generate", headers=h)

    assert db.query(Roadmap).filter_by(startup_id=s.id).count() == 1


def test_generate_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.validation)
    db.commit()

    assert client.post("/api/v1/roadmap/generate", headers=h).status_code == 403
