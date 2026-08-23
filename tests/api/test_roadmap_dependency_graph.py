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
    ms = data["phases"][0]["milestones"][0]["id"]
    a = client.post(
        "/api/v1/roadmap/tasks", headers=headers, json={"milestone_id": ms, "title": "A"}
    ).json()["data"]["id"]
    b = client.post(
        "/api/v1/roadmap/tasks", headers=headers, json={"milestone_id": ms, "title": "B"}
    ).json()["data"]["id"]
    return ms, a, b


def test_graph_and_tree_reflect_edge(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    ms, a, b = _two_tasks(client, h)
    client.post(
        f"/api/v1/roadmap/tasks/{a}/dependencies", headers=h, json={"depends_on_task_id": b}
    )

    g = client.get("/api/v1/roadmap/dependencies", headers=h)
    assert g.status_code == 200, g.text
    gd = g.json()["data"]
    assert {"task_id": a, "depends_on_task_id": b} in gd["edges"]
    assert any(n["task_id"] == a for n in gd["nodes"])

    tree = client.get("/api/v1/roadmap", headers=h).json()["data"]
    task_a = next(
        t for p in tree["phases"] for m in p["milestones"] for t in m["tasks"] if t["id"] == a
    )
    assert b in task_a["depends_on"]
    m_obj = next(m for p in tree["phases"] for m in p["milestones"] if m["id"] == ms)
    assert m_obj["dependency_count"] >= 1


def test_graph_requires_auth(client):
    assert client.get("/api/v1/roadmap/dependencies").status_code == 401
