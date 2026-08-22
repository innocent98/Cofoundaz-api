import uuid
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


def _two_tasks(client, headers):
    data = client.get("/api/v1/roadmap", headers=headers).json()["data"]
    mid = data["phases"][0]["milestones"][0]["id"]
    a = client.post(
        "/api/v1/roadmap/tasks", headers=headers, json={"milestone_id": mid, "title": "A"}
    ).json()["data"]["id"]
    b = client.post(
        "/api/v1/roadmap/tasks", headers=headers, json={"milestone_id": mid, "title": "B"}
    ).json()["data"]["id"]
    return a, b


def test_create_dependency_then_cycle_rejected(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    a, b = _two_tasks(client, h)

    # A depends on B -> 201
    r = client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies", headers=h, json={"depends_on_task_id": b}
    )
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["task_id"] == a
    assert body["depends_on_task_id"] == b

    # duplicate -> idempotent 200
    r2 = client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies", headers=h, json={"depends_on_task_id": b}
    )
    assert r2.status_code == 200, r2.text

    # B depends on A -> cycle 409
    r3 = client.post(
        f"/api/v1/roadmap/tasks/{b}/dependencies", headers=h, json={"depends_on_task_id": a}
    )
    assert r3.status_code == 409, r3.text
    assert r3.json()["error"]["code"] == "DEPENDENCY_CYCLE"


def test_self_dependency_422(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    a, _b = _two_tasks(client, h)

    r = client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies", headers=h, json={"depends_on_task_id": a}
    )
    assert r.status_code == 422, r.text


def test_dependency_foreign_task_404(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    a, _b = _two_tasks(client, h)

    r = client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies",
        headers=h,
        json={"depends_on_task_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404, r.text


def test_delete_dependency(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    a, b = _two_tasks(client, h)

    client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies", headers=h, json={"depends_on_task_id": b}
    )

    r = client.delete(f"/api/v1/roadmap/tasks/{a}/dependencies/{b}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["deleted"] is True

    # deleting a missing edge -> 404
    assert (
        client.delete(f"/api/v1/roadmap/tasks/{a}/dependencies/{b}", headers=h).status_code == 404
    )


def test_dependency_write_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.idea)
    db.commit()

    # mentor cannot create tasks either; use a bogus id — the 403 must come from the role gate
    r = client.post(
        f"/api/v1/roadmap/tasks/{uuid.uuid4()}/dependencies",
        headers=h,
        json={"depends_on_task_id": str(uuid.uuid4())},
    )
    assert r.status_code == 403


def test_dependency_cross_workspace_404(client, db):
    _ua, _sa, ha = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    _ub, _sb, hb = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    a, b = _two_tasks(client, ha)

    r = client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies", headers=hb, json={"depends_on_task_id": b}
    )
    assert r.status_code == 404
