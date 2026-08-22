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


def _ensure_roadmap(client, headers):
    client.get("/api/v1/roadmap", headers=headers)  # ensure generated


def _first_milestone_id(client, headers):
    data = client.get("/api/v1/roadmap", headers=headers).json()["data"]
    return data["phases"][0]["milestones"][0]["id"]


def test_task_lifecycle_updates_milestone_progress(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    t1 = client.post(
        "/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "A"}
    ).json()["data"]["id"]
    t2 = client.post(
        "/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "B"}
    ).json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/tasks/{t1}", headers=h, json={"status": "done"})
    assert r.status_code == 200, r.text

    data = client.get("/api/v1/roadmap", headers=h).json()["data"]
    ms = next(m for p in data["phases"] for m in p["milestones"] if m["id"] == mid)
    done = sum(1 for t in ms["tasks"] if t["status"] == "done")
    total = len(ms["tasks"])
    assert ms["progress"] == round(100 * done / total)

    assert client.delete(f"/api/v1/roadmap/tasks/{t2}", headers=h).status_code == 200

    data = client.get("/api/v1/roadmap", headers=h).json()["data"]
    ms = next(m for p in data["phases"] for m in p["milestones"] if m["id"] == mid)
    done = sum(1 for t in ms["tasks"] if t["status"] == "done")
    total = len(ms["tasks"])
    assert ms["progress"] == round(100 * done / total)


def test_task_foreign_milestone_404(client, db):
    _ua, _sa, ha = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    _ub, _sb, hb = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, ha)
    a_mid = _first_milestone_id(client, ha)

    r = client.post("/api/v1/roadmap/tasks", headers=hb, json={"milestone_id": a_mid, "title": "x"})
    assert r.status_code == 404


def test_task_assignee_must_be_member(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post(
        "/api/v1/roadmap/tasks",
        headers=h,
        json={"milestone_id": mid, "title": "x", "assignee_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_task_assignee_accepts_active_member(client, db):
    u, s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    other = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, other, s, role=MembershipRole.team_member)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post(
        "/api/v1/roadmap/tasks",
        headers=h,
        json={"milestone_id": mid, "title": "x", "assignee_id": str(other.id)},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["assignee"]["id"] == str(other.id)


def test_task_create_defaults_effort_and_status(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "x"})
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["effort"] == "medium"
    assert body["status"] == "todo"
    assert body["depends_on"] == []


def test_patch_task_rejects_explicit_null_title(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/tasks/{tid}", headers=h, json={"title": None})
    assert r.status_code == 422, r.text


def test_patch_task_rejects_explicit_null_effort(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/tasks/{tid}", headers=h, json={"effort": None})
    assert r.status_code == 422, r.text


def test_patch_task_rejects_explicit_null_status(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/tasks/{tid}", headers=h, json={"status": None})
    assert r.status_code == 422, r.text


def test_patch_task_rejects_explicit_null_order(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/tasks/{tid}", headers=h, json={"order": None})
    assert r.status_code == 422, r.text


def test_patch_task_accepts_null_description_assignee_due_on(client, db):
    u, s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    other = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, other, s, role=MembershipRole.team_member)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post(
        "/api/v1/roadmap/tasks",
        headers=h,
        json={
            "milestone_id": mid,
            "title": "t",
            "description": "desc",
            "assignee_id": str(other.id),
            "due_on": "2026-01-01",
        },
    )
    tid = r.json()["data"]["id"]

    r = client.patch(
        f"/api/v1/roadmap/tasks/{tid}",
        headers=h,
        json={"description": None, "assignee_id": None, "due_on": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["description"] is None
    assert body["assignee"] is None
    assert body["due_on"] is None


def test_patch_task_assignee_must_be_member(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    r = client.patch(
        f"/api/v1/roadmap/tasks/{tid}",
        headers=h,
        json={"assignee_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_task_cross_workspace_patch_404(client, db):
    _ua, _sa, ha = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    _ub, _sb, hb = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, ha)
    mid = _first_milestone_id(client, ha)

    r = client.post("/api/v1/roadmap/tasks", headers=ha, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    assert (
        client.patch(f"/api/v1/roadmap/tasks/{tid}", headers=hb, json={"title": "z"}).status_code
        == 404
    )


def test_task_write_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.idea)
    db.commit()

    assert (
        client.post(
            "/api/v1/roadmap/tasks",
            headers=h,
            json={"milestone_id": "00000000-0000-0000-0000-000000000000", "title": "x"},
        ).status_code
        == 403
    )


def test_delete_task(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    _ensure_roadmap(client, h)
    mid = _first_milestone_id(client, h)

    r = client.post("/api/v1/roadmap/tasks", headers=h, json={"milestone_id": mid, "title": "t"})
    tid = r.json()["data"]["id"]

    assert client.delete(f"/api/v1/roadmap/tasks/{tid}", headers=h).status_code == 200
    assert (
        client.patch(f"/api/v1/roadmap/tasks/{tid}", headers=h, json={"title": "z"}).status_code
        == 404
    )
