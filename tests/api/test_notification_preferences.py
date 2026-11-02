from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user


def _member(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_get_returns_defaults(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/notifications/preferences", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["master_email"] is True and data["categories"]["documents"] is True


def test_put_upserts_and_roundtrips(client, db):
    _u, _s, h = _member(db)
    r = client.put(
        "/api/v1/notifications/preferences", headers=h, json={"categories": {"documents": False}}
    )
    assert r.status_code == 200 and r.json()["data"]["categories"]["documents"] is False
    got = client.get("/api/v1/notifications/preferences", headers=h).json()["data"]
    assert got["categories"]["documents"] is False and got["categories"]["team"] is True


def test_put_unknown_category_422(client, db):
    _u, _s, h = _member(db)
    r = client.put(
        "/api/v1/notifications/preferences",
        headers=h,
        json={"categories": {"not_a_category": False}},
    )
    assert r.status_code == 422


def test_defaults_include_digest_and_quiet_hours(client, db):
    _u, _s, h = _member(db)
    d = client.get("/api/v1/notifications/preferences", headers=h).json()["data"]
    assert d["digest_mode"] == "realtime" and d["digest_time"] == "08:00" and d["digest_day"] == 0
    assert (
        d["quiet_enabled"] is False and d["quiet_from"] == "20:00" and d["quiet_until"] == "06:00"
    )
    assert d["timezone"] == "Africa/Lagos"


def test_put_saves_digest_and_quiet_hours_and_keeps_the_rest(client, db):
    _u, _s, h = _member(db)
    client.put("/api/v1/notifications/preferences", headers=h, json={"master_email": False})
    r = client.put(
        "/api/v1/notifications/preferences",
        headers=h,
        json={
            "digest_mode": "weekly",
            "digest_time": "07:30",
            "digest_day": 4,
            "quiet_enabled": True,
            "quiet_from": "21:15",
            "quiet_until": "05:45",
            "timezone": "Europe/London",
        },
    )
    assert r.status_code == 200, r.text
    d = client.get("/api/v1/notifications/preferences", headers=h).json()["data"]
    assert d["digest_mode"] == "weekly" and d["digest_time"] == "07:30" and d["digest_day"] == 4
    assert d["quiet_enabled"] is True and d["quiet_from"] == "21:15" and d["quiet_until"] == "05:45"
    assert d["timezone"] == "Europe/London" and d["master_email"] is False


def test_put_rejects_bad_schedule_values(client, db):
    _u, _s, h = _member(db)
    for body in (
        {"digest_mode": "hourly"},
        {"digest_day": 7},
        {"timezone": "Mars/Base"},
        {"quiet_from": "25:00"},
    ):
        r = client.put("/api/v1/notifications/preferences", headers=h, json=body)
        assert r.status_code == 422, body
