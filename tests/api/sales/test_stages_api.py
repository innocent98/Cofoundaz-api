from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.sales import Deal
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/sales"

OTHER_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

ROUTES = [
    ("GET", "/stages", None),
    ("PUT", "/stages", {"stages": []}),
    ("GET", "/settings", None),
    ("PATCH", "/settings", {"stall_days": 20}),
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _body(stages):
    return {
        "stages": [
            {
                "id": s["id"],
                "name": s["name"],
                "kind": s["kind"],
                "win_probability": s["win_probability"],
            }
            for s in stages
        ]
    }


@pytest.mark.parametrize("role", OTHER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_other_roles_forbidden(client, db, role, method, path, body):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = client.request(method, BASE + path, json=body, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
def test_core_roles_get_stages(client, db, role):
    _u, _s, h = _member(db, role=role)
    r = client.get(f"{BASE}/stages", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [s["name"] for s in data][:2] == ["New", "Contacted"]
    assert set(data[0]) == {"id", "name", "position", "kind", "win_probability"}


def test_unauthenticated_rejected(client):
    assert client.get(f"{BASE}/stages").status_code in (401, 403)


def test_put_stages_rename_add_and_failed_omit(client, db):
    _u, startup, h = _member(db)
    stages = client.get(f"{BASE}/stages", headers=h).json()["data"]
    body = _body(stages)
    body["stages"][0]["name"] = "Fresh"
    body["stages"].insert(1, {"id": None, "name": "Demo", "kind": "open", "win_probability": 25})
    r = client.put(f"{BASE}/stages", json=body, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()["data"]
    assert out[0]["name"] == "Fresh" and out[0]["id"] == stages[0]["id"]
    assert out[1]["name"] == "Demo" and len(out) == 8

    from uuid import UUID

    db.add(Deal(startup_id=startup.id, name="D", stage_id=UUID(out[2]["id"])))
    db.flush()
    omit = _body([s for i, s in enumerate(out) if i != 2])
    r = client.put(f"{BASE}/stages", json=omit, headers=h)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["field_errors"][0]["field"] == "stages"
    assert "Move the deals out of Contacted first." in err["message"]


def test_put_stages_two_won_422(client, db):
    _u, _s, h = _member(db)
    stages = client.get(f"{BASE}/stages", headers=h).json()["data"]
    body = _body(stages)
    body["stages"].append({"id": None, "name": "W2", "kind": "won", "win_probability": 100})
    assert client.put(f"{BASE}/stages", json=body, headers=h).status_code == 422


def test_stages_isolated_between_workspaces(client, db):
    _a, _sa, ha = _member(db)
    _b, _sb, hb = _member(db)
    ia = {s["id"] for s in client.get(f"{BASE}/stages", headers=ha).json()["data"]}
    ib = {s["id"] for s in client.get(f"{BASE}/stages", headers=hb).json()["data"]}
    assert not ia & ib


def test_settings_get_and_patch(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/settings", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"stall_days": 14, "outreach_postal_address": None}
    r = client.patch(
        f"{BASE}/settings",
        json={"stall_days": 21, "outreach_postal_address": "1 Broad St"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"stall_days": 21, "outreach_postal_address": "1 Broad St"}
    assert client.get(f"{BASE}/settings", headers=h).json()["data"]["stall_days"] == 21


@pytest.mark.parametrize("bad", [2, 91])
def test_settings_stall_days_bounds_422(client, db, bad):
    _u, _s, h = _member(db)
    r = client.patch(f"{BASE}/settings", json={"stall_days": bad}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
