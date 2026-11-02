import uuid
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/sales"
OTHER_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]
_ID = str(uuid.uuid4())
ROUTES = [
    ("POST", f"/leads/{_ID}/convert", None),
    ("GET", f"/activities?deal_id={_ID}", None),
    ("POST", "/activities", {"kind": "note", "body": "x", "deal_id": _ID}),
    ("PATCH", f"/activities/{_ID}", {"body": "y"}),
    ("DELETE", f"/activities/{_ID}", None),
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


@pytest.mark.parametrize("role", OTHER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_other_roles_forbidden(client, db, role, method, path, body):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = client.request(method, BASE + path, json=body, headers=h)
    assert r.status_code == 403, r.text


def test_convert_flow(client, db):
    _u, _s, h = _member(db)
    lead = client.post(
        f"{BASE}/leads",
        json={"name": "Ada Obi", "company": "Acme", "email": "a@acme.co"},
        headers=h,
    ).json()["data"]
    r = client.post(f"{BASE}/leads/{lead['id']}/convert", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["deal"]["name"] == "Acme" and d["deal"]["account_name"] == "Acme"
    assert d["account"]["name"] == "Acme" and d["contact"]["first_name"] == "Ada"
    got = client.get(f"{BASE}/leads/{lead['id']}", headers=h).json()["data"]
    assert got["status"] == "converted" and got["converted_deal_id"] == d["deal"]["id"]
    hist = client.get(f"{BASE}/deals/{d['deal']['id']}", headers=h).json()["data"]
    assert len(hist["stage_history"]) == 1

    r = client.post(f"{BASE}/leads/{lead['id']}/convert", headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "This lead was already converted."


def test_convert_no_company(client, db):
    _u, _s, h = _member(db)
    lead = client.post(f"{BASE}/leads", json={"name": "Solo"}, headers=h).json()["data"]
    d = client.post(f"{BASE}/leads/{lead['id']}/convert", headers=h).json()["data"]
    assert d["account"] is None and d["deal"]["account_id"] is None


def test_convert_cross_tenant_404(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    lead = client.post(f"{BASE}/leads", json={"name": "A"}, headers=h2).json()["data"]
    assert client.post(f"{BASE}/leads/{lead['id']}/convert", headers=h).status_code == 404


def test_activities_flow(client, db):
    _u, startup, h = _member(db)
    _u2, _s2, h2 = _member(db, role=MembershipRole.team_member, startup=startup)
    deal = client.post(f"{BASE}/deals", json={"name": "D"}, headers=h).json()["data"]

    r = client.post(f"{BASE}/activities", json={"kind": "note", "body": "x"}, headers=h)
    assert r.status_code == 422
    r = client.post(
        f"{BASE}/activities",
        json={"kind": "note", "body": "x", "deal_id": str(uuid.uuid4())},
        headers=h,
    )
    assert r.status_code == 422

    r = client.post(
        f"{BASE}/activities",
        json={"kind": "call", "body": "Called them", "deal_id": deal["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    a = r.json()["data"]
    assert a["kind"] == "call" and a["deal_id"] == deal["id"]

    assert client.get(f"{BASE}/activities", headers=h).status_code == 422
    lst = client.get(f"{BASE}/activities?deal_id={deal['id']}", headers=h).json()["data"]
    assert lst["total"] == 1 and lst["items"][0]["id"] == a["id"]

    assert (
        client.patch(f"{BASE}/activities/{a['id']}", json={"body": "z"}, headers=h2).status_code
        == 403
    )
    assert client.delete(f"{BASE}/activities/{a['id']}", headers=h2).status_code == 403
    r = client.patch(f"{BASE}/activities/{a['id']}", json={"body": "edited"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["body"] == "edited"
    assert client.delete(f"{BASE}/activities/{a['id']}", headers=h).status_code == 200
    lst = client.get(f"{BASE}/activities?deal_id={deal['id']}", headers=h).json()["data"]
    assert lst["total"] == 0
