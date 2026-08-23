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


def test_gallery_lists_templates(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    client.get("/api/v1/roadmap", headers=h)  # ensure roadmap exists
    r = client.get("/api/v1/roadmap/templates", headers=h)
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) >= 6
    one = next(i for i in items if i["id"] == "mvp-build")
    assert one["title"] == "MVP build"
    assert one["category"] == "Fintech"
    assert one["milestone_count"] >= 1 and one["task_count"] >= 1
    assert one["applied"] is False


def test_template_preview(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    r = client.get("/api/v1/roadmap/templates/mvp-build", headers=h)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["id"] == "mvp-build"
    assert d["phases"] and d["phases"][0]["milestones"][0]["tasks"]


def test_template_preview_unknown_404(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    assert client.get("/api/v1/roadmap/templates/nope", headers=h).status_code == 404
