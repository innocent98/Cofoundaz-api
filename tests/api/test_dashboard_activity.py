from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_activity, create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation, full_name=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if full_name is not None:
        u.profile.full_name = full_name
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_activity_newest_first_and_paginates(client, db):
    u, s, h = _member(db, full_name="Ada Lovelace")
    for i in range(3):
        create_activity(
            db,
            startup=s,
            actor=u,
            action="mission.task.completed",
            summary=f"did thing {i}",
        )
    db.commit()

    r1 = client.get("/api/v1/dashboard/activity?limit=2", headers=h)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()["data"]
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None
    assert body1["items"][0]["actor"]["id"] == str(u.id)
    assert body1["items"][0]["actor"]["name"] == "Ada Lovelace"

    r2 = client.get(f"/api/v1/dashboard/activity?cursor={body1['next_cursor']}", headers=h)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()["data"]
    ids1 = {i["id"] for i in body1["items"]}
    ids2 = {i["id"] for i in body2["items"]}
    assert ids1.isdisjoint(ids2)  # pages don't overlap
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None


def test_activity_empty_workspace(client, db):
    _u, _s, h = _member(db)
    db.commit()

    r = client.get("/api/v1/dashboard/activity", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"items": [], "next_cursor": None}


def test_activity_never_leaks_across_tenants(client, db):
    _u, s, h = _member(db)
    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    other_startup = create_startup(db, owner=other_owner)
    create_activity(db, startup=other_startup, action="x", summary="theirs")
    db.commit()

    r = client.get("/api/v1/dashboard/activity", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["items"] == []


def test_activity_system_row_has_null_actor(client, db):
    _u, s, h = _member(db)
    create_activity(db, startup=s, actor=None, action="system.sync", summary="ran a sync")
    db.commit()

    r = client.get("/api/v1/dashboard/activity", headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["actor"] is None
