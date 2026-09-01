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


def _outsider(db):
    # A verified user with no membership on any workspace -- but we still need
    # a workspace id to send, so create one they're not a member of.
    _owner, s, _h = _member(db)
    u = create_user(db, email="outsider@x.com", email_verified_at=datetime.now(UTC))
    db.flush()
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def test_overview_lists_all_types_as_start(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/business-builder/overview", headers=h)
    assert r.status_code == 200
    rows = r.json()["data"]
    assert {row["type"] for row in rows} == {
        "business_model",
        "lean",
        "value_prop",
        "mission_vision",
        "swot",
    }
    assert all(row["status"] == "start" for row in rows)


def test_get_canvas_lazy_creates_and_returns_defs(client, db):
    _u, _s, h = _member(db)
    r = client.get("/api/v1/business-builder/canvases/swot", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["type"] == "swot" and data["version"] == 1
    assert [b["key"] for b in data["block_defs"]] == [
        "strengths",
        "weaknesses",
        "opportunities",
        "threats",
    ]
    assert data["blocks"] == {
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    }


def test_get_unknown_type_404(client, db):
    _u, _s, h = _member(db)
    assert client.get("/api/v1/business-builder/canvases/not_a_type", headers=h).status_code == 404


def test_overview_requires_membership_403(client, db):
    outsider_headers = _outsider(db)
    assert (
        client.get("/api/v1/business-builder/overview", headers=outsider_headers).status_code == 403
    )


def test_put_saves_and_bumps_version(client, db):
    _u, _s, h = _member(db)
    client.get("/api/v1/business-builder/canvases/business_model", headers=h)  # lazy-create v1
    r = client.put(
        "/api/v1/business-builder/canvases/business_model",
        json={"blocks": {"key_partners": ["Stripe"]}, "version": 1},
        headers=h,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["version"] == 2
    assert data["blocks"]["key_partners"] == ["Stripe"]
    assert data["completion"]["status"] == "continue"


def test_put_stale_version_409(client, db):
    _u, _s, h = _member(db)
    client.get("/api/v1/business-builder/canvases/swot", headers=h)  # v1
    client.put(
        "/api/v1/business-builder/canvases/swot",
        json={"blocks": {"strengths": ["fast"]}, "version": 1},
        headers=h,
    )  # -> v2
    r = client.put(
        "/api/v1/business-builder/canvases/swot",
        json={"blocks": {"weaknesses": ["slow"]}, "version": 1},
        headers=h,
    )  # stale
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CANVAS_VERSION_CONFLICT"


def test_put_bad_block_422(client, db):
    _u, _s, h = _member(db)
    client.get("/api/v1/business-builder/canvases/swot", headers=h)
    r = client.put(
        "/api/v1/business-builder/canvases/swot",
        json={"blocks": {"strengths": "not-a-list"}, "version": 1},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_mentor_forbidden_403(client, db):
    _u, _s, founder_h = _member(db)
    r = client.put(
        "/api/v1/business-builder/canvases/swot",
        json={"blocks": {"strengths": ["x"]}, "version": 1},
        headers=founder_h,
    )
    assert r.status_code == 200  # sanity: founder can edit

    mentor_u, mentor_s, mentor_h = _member(db, role=MembershipRole.mentor)
    r = client.put(
        "/api/v1/business-builder/canvases/swot",
        json={"blocks": {"strengths": ["x"]}, "version": 1},
        headers=mentor_h,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
