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
