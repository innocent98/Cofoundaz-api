from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_get_roadmap_lazy_generates(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    r = client.get("/api/v1/roadmap", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["current_stage"] == "validation"
    assert data["roadmap"]["template_key"] == "stage.validation"
    assert len(data["phases"]) >= 1
    assert data["phases"][0]["milestones"][0]["tasks"][0]["depends_on"] == []


def test_get_roadmap_requires_auth(client):
    assert client.get("/api/v1/roadmap").status_code == 401
