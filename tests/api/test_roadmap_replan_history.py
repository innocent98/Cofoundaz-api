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


def test_history_lists_applied_replan(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.validation)
    db.commit()

    data = client.get("/api/v1/roadmap", headers=h).json()["data"]
    mid = data["phases"][0]["milestones"][0]["id"]
    client.patch(
        f"/api/v1/roadmap/milestones/{mid}",
        headers=h,
        json={"due_on": (date.today() - timedelta(days=10)).isoformat()},
    )
    cid = client.post("/api/v1/roadmap/replan/preview", headers=h).json()["data"]["changes"][0][
        "change_id"
    ]
    client.post("/api/v1/roadmap/replan/apply", headers=h, json={"change_ids": [cid]})

    h_resp = client.get("/api/v1/roadmap/replan/history", headers=h)
    assert h_resp.status_code == 200, h_resp.text
    items = h_resp.json()["data"]
    assert len(items) == 1 and items[0]["change_count"] == 1
    assert items[0]["summary"] == "Re-planned 1 milestone"
    assert items[0]["changes"][0]["milestone_id"] == mid
