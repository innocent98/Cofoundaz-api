import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models.activity import ActivityLog
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
    ("GET", "/accounts", None),
    ("POST", "/accounts", {"name": "X"}),
    ("GET", f"/accounts/{_ID}", None),
    ("PATCH", f"/accounts/{_ID}", {"name": "Y"}),
    ("DELETE", f"/accounts/{_ID}", None),
    ("GET", "/contacts", None),
    ("POST", "/contacts", {"first_name": "X"}),
    ("GET", f"/contacts/{_ID}", None),
    ("PATCH", f"/contacts/{_ID}", {"first_name": "Y"}),
    ("DELETE", f"/contacts/{_ID}", None),
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


def test_account_crud_flow(client, db):
    u, s, h = _member(db)
    r = client.post(f"{BASE}/accounts", json={"name": "Acme", "owner_id": str(u.id)}, headers=h)
    assert r.status_code in (200, 201), r.text
    acc = r.json()["data"]
    assert acc["name"] == "Acme" and acc["owner_id"] == str(u.id)
    aid = acc["id"]

    r = client.get(f"{BASE}/accounts", headers=h)
    data = r.json()["data"]
    assert data["total"] == 1 and data["limit"] == 50 and data["offset"] == 0
    assert data["items"][0]["id"] == aid

    r = client.patch(f"{BASE}/accounts/{aid}", json={"industry": "Fintech"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["industry"] == "Fintech"

    r = client.get(f"{BASE}/accounts/{aid}", headers=h)
    d = r.json()["data"]
    assert set(d) == {"account", "contacts", "open_deals", "lifetime_won_minor"}
    assert d["open_deals"] == [] and d["lifetime_won_minor"] == 0

    r = client.delete(f"{BASE}/accounts/{aid}", headers=h)
    assert r.status_code == 200
    assert client.get(f"{BASE}/accounts/{aid}", headers=h).status_code == 404
    actions = set(db.scalars(select(ActivityLog.action).where(ActivityLog.startup_id == s.id)))
    assert {"sales.account.created", "sales.account.updated", "sales.account.deleted"} <= actions


def test_duplicate_account_name_422_then_read_works(client, db):
    _u, _s, h = _member(db)
    assert client.post(f"{BASE}/accounts", json={"name": "Acme"}, headers=h).status_code < 300
    r = client.post(f"{BASE}/accounts", json={"name": "ACME"}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["field_errors"][0]["field"] == "name"
    assert client.get(f"{BASE}/accounts", headers=h).json()["data"]["total"] == 1


def test_foreign_owner_422(client, db):
    _u, _s, h = _member(db)
    foreign, _s2, _h2 = _member(db)
    r = client.post(f"{BASE}/accounts", json={"name": "A", "owner_id": str(foreign.id)}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["field_errors"][0]["field"] == "owner_id"


def test_cross_workspace_account_404(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    aid = client.post(f"{BASE}/accounts", json={"name": "Theirs"}, headers=h2).json()["data"]["id"]
    assert client.get(f"{BASE}/accounts/{aid}", headers=h).status_code == 404
    assert client.patch(f"{BASE}/accounts/{aid}", json={"name": "z"}, headers=h).status_code == 404
    assert client.delete(f"{BASE}/accounts/{aid}", headers=h).status_code == 404


def test_account_list_filters_and_pagination(client, db):
    _u, _s, h = _member(db)
    for n in ["Alpha", "Beta", "Alphabet"]:
        client.post(f"{BASE}/accounts", json={"name": n}, headers=h)
    d = client.get(f"{BASE}/accounts", params={"q": "alph"}, headers=h).json()["data"]
    assert d["total"] == 2
    d = client.get(f"{BASE}/accounts", params={"limit": 1, "offset": 1}, headers=h).json()["data"]
    assert len(d["items"]) == 1 and d["total"] == 3
    assert client.get(f"{BASE}/accounts", params={"limit": 201}, headers=h).status_code == 422


def test_contact_flow_and_cross_tenant_account(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    theirs = client.post(f"{BASE}/accounts", json={"name": "T"}, headers=h2).json()["data"]["id"]
    r = client.post(f"{BASE}/contacts", json={"first_name": "Ada", "account_id": theirs}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["field_errors"][0]["field"] == "account_id"

    mine = client.post(f"{BASE}/accounts", json={"name": "M"}, headers=h).json()["data"]["id"]
    r = client.post(
        f"{BASE}/contacts",
        json={"first_name": "Ada", "email": "ada@acmecorp.com", "account_id": mine},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    cid = r.json()["data"]["id"]
    assert (
        client.post(
            f"{BASE}/contacts", json={"first_name": "B", "email": "not-an-email"}, headers=h
        ).status_code
        == 422
    )

    d = client.get(f"{BASE}/contacts", params={"account_id": mine}, headers=h).json()["data"]
    assert d["total"] == 1
    d = client.get(f"{BASE}/contacts", params={"q": "ACMEcorp.com"}, headers=h).json()["data"]
    assert d["total"] == 1
    r = client.patch(f"{BASE}/contacts/{cid}", json={"title": "CTO"}, headers=h)
    assert r.json()["data"]["title"] == "CTO"
    assert client.get(f"{BASE}/contacts/{cid}", headers=h2).status_code == 404
    assert client.delete(f"{BASE}/contacts/{cid}", headers=h).status_code == 200
    assert client.get(f"{BASE}/contacts/{cid}", headers=h).status_code == 404


def test_account_website_must_be_http_or_https(client, db):
    _u, _s, h = _member(db)
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.com"):
        r = client.post(f"{BASE}/accounts", json={"name": "Acme X", "website": bad}, headers=h)
        assert r.status_code == 422, bad
    ok = client.post(f"{BASE}/accounts", json={"name": "Acme Y", "website": "acme.com"}, headers=h)
    assert ok.status_code < 300
    assert ok.json()["data"]["website"] == "https://acme.com"
