from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.roadmap import RoadmapMilestone, RoadmapTask
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


def test_get_roadmap_owner_and_assignee_shape(client, db):
    u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    # First GET lazy-generates the tree.
    r = client.get("/api/v1/roadmap", headers=h)
    assert r.status_code == 200, r.text

    # Assign the founder as owner of the first milestone and assignee of its first task,
    # directly via the ORM -- proves serialize_tree's non-null person_ref object shape.
    milestone = db.query(RoadmapMilestone).order_by(RoadmapMilestone.order).first()
    task = (
        db.query(RoadmapTask)
        .filter_by(milestone_id=milestone.id)
        .order_by(RoadmapTask.order)
        .first()
    )
    milestone.owner_id = u.id
    task.assignee_id = u.id
    db.commit()

    r2 = client.get("/api/v1/roadmap", headers=h)
    assert r2.status_code == 200, r2.text
    data = r2.json()["data"]
    expected = {"id": str(u.id), "name": u.profile.full_name}

    found_milestone = next(
        m for ph in data["phases"] for m in ph["milestones"] if m["id"] == str(milestone.id)
    )
    assert found_milestone["owner"] == expected

    found_task = next(
        t
        for ph in data["phases"]
        for m in ph["milestones"]
        for t in m["tasks"]
        if t["id"] == str(task.id)
    )
    assert found_task["assignee"] == expected
