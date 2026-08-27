from datetime import UTC, date, datetime, timedelta

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


def test_tree_exposes_drift_and_replanned(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    data = client.get("/api/v1/roadmap", headers=h).json()["data"]
    assert data["roadmap"]["drift"]["slipped_count"] == 0
    mid = data["phases"][0]["milestones"][0]["id"]
    # every milestone starts with replanned == null
    m0 = next(m for p in data["phases"] for m in p["milestones"] if m["id"] == mid)
    assert m0["replanned"] is None

    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=h, json={"due_on": past})
    d2 = client.get("/api/v1/roadmap", headers=h).json()["data"]
    assert d2["roadmap"]["drift"]["slipped_count"] >= 1

    cid = client.post("/api/v1/roadmap/replan/preview", headers=h).json()["data"]["changes"][0][
        "change_id"
    ]
    client.post("/api/v1/roadmap/replan/apply", headers=h, json={"change_ids": [cid]})
    d3 = client.get("/api/v1/roadmap", headers=h).json()["data"]
    m3 = next(m for p in d3["phases"] for m in p["milestones"] if m["id"] == mid)
    assert m3["replanned"] is not None and m3["replanned"]["reason"]
