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
