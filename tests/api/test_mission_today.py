from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, RoadmapStatus, StartupStage
from tests.factories import (
    create_membership,
    create_milestone,
    create_mission_settings,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _seed_roadmap(db, s):
    # weekend_missions=True keeps generation deterministic regardless of what day
    # the suite happens to run on.
    create_mission_settings(db, s, weekend_missions=True)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m = create_milestone(db, ph, title="Validate demand")
    for i in range(3):
        create_task(db, m, title=f"T{i}", status=RoadmapStatus.todo, order=i)


def test_get_today_returns_tasks_and_streak(client, db):
    _u, s, h = _member(db)
    _seed_roadmap(db, s)
    db.commit()

    r = client.get("/api/v1/missions/today", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert 1 <= len(data["tasks"]) <= 3
    assert data["streak"] == 0
    assert data["status"] == "pending"
    assert data["tasks"][0]["title"] == "T0"
    assert data["tasks"][0]["reason"] == "From your 'Validate demand' milestone."


def test_get_today_no_roadmap_returns_empty_state(client, db):
    _u, _s, h = _member(db)
    db.commit()

    r = client.get("/api/v1/missions/today", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"status": "no_roadmap"}


def test_get_today_requires_auth(client):
    assert client.get("/api/v1/missions/today").status_code == 401


def test_get_settings_returns_defaults(client, db):
    _u, _s, h = _member(db)
    db.commit()

    r = client.get("/api/v1/missions/settings", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data == {"mission_size": 3, "delivery_time": "06:00:00", "weekend_missions": False}


def test_patch_settings_persists_mission_size(client, db):
    _u, _s, h = _member(db)
    db.commit()

    r = client.patch("/api/v1/missions/settings", json={"mission_size": 2}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["mission_size"] == 2

    r2 = client.get("/api/v1/missions/settings", headers=h)
    assert r2.json()["data"]["mission_size"] == 2


def test_patch_settings_rejects_out_of_range_mission_size(client, db):
    _u, _s, h = _member(db)
    db.commit()

    r = client.patch("/api/v1/missions/settings", json={"mission_size": 5}, headers=h)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["field_errors"][0]["field"] == "mission_size"


def test_patch_settings_requires_editor_role(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor)
    db.commit()

    r = client.patch("/api/v1/missions/settings", json={"mission_size": 2}, headers=h)
    assert r.status_code == 403, r.text
