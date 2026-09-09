from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    return (
        u,
        s,
        {
            "Authorization": f"Bearer {create_access_token(str(u.id))}",
            "X-Workspace-Id": str(s.id),
        },
    )


def test_get_positioning_map_defaults(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/business-builder/positioning-map", headers=h)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["axes"]["x"]["label"] == "Price" and body["competitors"] == []


def test_put_axes_then_coords_via_competitor(client, db):
    _u, _s, h = _member(db)
    put = client.put(
        "/api/v1/business-builder/positioning-map",
        json={
            "axes": {
                "x": {"label": "Reach", "low": "Niche", "high": "Mass"},
                "y": {"label": "Trust", "low": "New", "high": "Proven"},
            }
        },
        headers=h,
    )
    assert put.status_code == 200 and put.json()["data"]["axes"]["x"]["label"] == "Reach"

    rid = client.post(
        "/api/v1/business-builder/competitors",
        json={"data": {"name": "Acme", "map_x": 0.3, "map_y": 0.7}},
        headers=h,
    ).json()["data"]["id"]

    got = client.get("/api/v1/business-builder/positioning-map", headers=h).json()["data"]
    acme = next(c for c in got["competitors"] if c["id"] == rid)
    assert acme["x"] == 0.3 and acme["y"] == 0.7


def test_put_axes_editor_only_403(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor)
    assert (
        client.put(
            "/api/v1/business-builder/positioning-map", json={"axes": {}}, headers=h
        ).status_code
        == 403
    )


def test_positioning_map_not_shadowed_by_kind(client, db):
    _u, _s, h = _member(db)
    assert client.get("/api/v1/business-builder/positioning-map", headers=h).status_code == 200
