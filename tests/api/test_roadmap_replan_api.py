import uuid
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


def _force_slip(client, headers, db):
    """GET the tree (lazy-generates), then PATCH the first milestone's due_on into the past."""
    data = client.get("/api/v1/roadmap", headers=headers).json()["data"]
    mid = data["phases"][0]["milestones"][0]["id"]
    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=headers, json={"due_on": past})
    return mid


def test_preview_then_apply(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()
    _force_slip(client, h, db)

    pv = client.post("/api/v1/roadmap/replan/preview", headers=h)
    assert pv.status_code == 200, pv.text
    body = pv.json()["data"]
    assert body["drift_count"] >= 1 and len(body["changes"]) >= 1
    cid = body["changes"][0]["change_id"]

    ap = client.post("/api/v1/roadmap/replan/apply", headers=h, json={"change_ids": [cid]})
    assert ap.status_code == 200, ap.text
    assert cid in ap.json()["data"]["applied"]
    assert ap.json()["data"]["replan_id"]


def test_apply_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.validation)
    db.commit()

    r = client.post(
        "/api/v1/roadmap/replan/apply",
        headers=h,
        json={"change_ids": [str(uuid.uuid4())]},
    )
    assert r.status_code == 403


def test_preview_no_drift_empty(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()
    client.get("/api/v1/roadmap", headers=h)  # generate, no slip

    pv = client.post("/api/v1/roadmap/replan/preview", headers=h)
    assert pv.status_code == 200
    assert pv.json()["data"]["changes"] == []
