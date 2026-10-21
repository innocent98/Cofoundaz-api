import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

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
_ID = str(uuid.uuid4())
ROUTES = [
    ("GET", "/deals", None),
    ("POST", "/deals", {"name": "X"}),
    ("GET", f"/deals/{_ID}", None),
    ("PATCH", f"/deals/{_ID}", {"name": "Y"}),
    ("DELETE", f"/deals/{_ID}", None),
    ("POST", f"/deals/{_ID}/stage", {"stage_id": _ID}),
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


def _stages(client, h):
    return {s["name"]: s["id"] for s in client.get(f"{BASE}/stages", headers=h).json()["data"]}


@pytest.mark.parametrize("role", OTHER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_other_roles_forbidden(client, db, role, method, path, body):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = client.request(method, BASE + path, json=body, headers=h)
    assert r.status_code == 403, r.text


def test_deal_flow(client, db):
    u, s, h = _member(db)
    st = _stages(client, h)
    r = client.post(f"{BASE}/accounts", json={"name": "Acme"}, headers=h)
    aid = r.json()["data"]["id"]
    r = client.post(
        f"{BASE}/contacts",
        json={"first_name": "Ada", "last_name": "Obi", "account_id": aid},
        headers=h,
    )
    cid = r.json()["data"]["id"]

    r = client.post(
        f"{BASE}/deals",
        json={"name": "Big", "account_id": aid, "contact_id": cid, "value_minor": 250000},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    d = r.json()["data"]
    assert d["stage_id"] == st["New"] and d["account_name"] == "Acme"
    assert d["contact_name"] == "Ada Obi" and d["is_stalled"] is False and d["days_inactive"] == 0
    assert d["currency"] == "NGN"
    did = d["id"]

    r = client.get(f"{BASE}/deals", headers=h)
    data = r.json()["data"]
    assert data["total"] == 1 and data["items"][0]["id"] == did
    assert client.get(f"{BASE}/deals?open_only=true", headers=h).json()["data"]["total"] == 1
    assert client.get(f"{BASE}/deals?q=zzz", headers=h).json()["data"]["total"] == 0

    r = client.patch(f"{BASE}/deals/{did}", json={"stage_id": st["Qualified"]}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["message"] == "Use the stage endpoint."

    r = client.post(f"{BASE}/deals/{did}/stage", json={"stage_id": st["Closed Lost"]}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["field_errors"][0]["field"] == "closed_reason"

    r = client.post(
        f"{BASE}/deals/{did}/stage",
        json={"stage_id": st["Closed Lost"], "closed_reason": "price"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["closed_reason"] == "price"
    assert len(r.json()["data"]["stage_history"]) == 2

    r = client.post(f"{BASE}/deals/{did}/stage", json={"stage_id": st["Contacted"]}, headers=h)
    assert r.json()["data"]["closed_at"] is None and r.json()["data"]["closed_reason"] is None

    r = client.post(f"{BASE}/deals/{did}/stage", json={"stage_id": st["Closed Won"]}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["closed_at"] is not None

    r = client.get(f"{BASE}/deals/{did}", headers=h)
    hist = r.json()["data"]["stage_history"]
    assert [x["to_stage_name"] for x in hist] == ["New", "Closed Lost", "Contacted", "Closed Won"]
    assert hist[0]["from_stage_id"] is None

    r = client.get(f"{BASE}/accounts/{aid}", headers=h)
    d = r.json()["data"]
    assert d["open_deals"] == [] and d["lifetime_won_minor"] == 250000

    client.post(f"{BASE}/deals/{did}/stage", json={"stage_id": st["Proposal"]}, headers=h)
    d = client.get(f"{BASE}/accounts/{aid}", headers=h).json()["data"]
    assert [x["id"] for x in d["open_deals"]] == [did] and d["lifetime_won_minor"] == 0
    assert d["open_deals"][0]["account_name"] == "Acme"

    r = client.delete(f"{BASE}/deals/{did}", headers=h)
    assert r.status_code == 200 and r.json()["data"] == {"deleted": True}
    assert client.get(f"{BASE}/deals/{did}", headers=h).status_code == 404


def test_stalled_flag_in_list(client, db):
    _u, s, h = _member(db)
    r = client.post(f"{BASE}/deals", json={"name": "Old"}, headers=h)
    did = r.json()["data"]["id"]
    row = db.scalar(select(Deal).where(Deal.id == uuid.UUID(did)))
    row.last_activity_at = datetime.now(UTC) - timedelta(days=20)
    db.flush()
    item = client.get(f"{BASE}/deals", headers=h).json()["data"]["items"][0]
    assert item["is_stalled"] is True and item["days_inactive"] == 20


def test_cross_tenant_ids_rejected(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    acc2 = client.post(f"{BASE}/accounts", json={"name": "Theirs"}, headers=h2).json()["data"]
    st2 = _stages(client, h2)
    other = client.post(f"{BASE}/deals", json={"name": "Theirs"}, headers=h2).json()["data"]
    st = _stages(client, h)

    r = client.post(f"{BASE}/deals", json={"name": "X", "account_id": acc2["id"]}, headers=h)
    assert r.status_code == 422
    r = client.post(f"{BASE}/deals", json={"name": "X", "stage_id": st2["New"]}, headers=h)
    assert r.status_code == 422
    r = client.post(f"{BASE}/deals", json={"name": "X", "owner_id": str(_u2.id)}, headers=h)
    assert r.status_code == 422
    assert client.get(f"{BASE}/deals/{other['id']}", headers=h).status_code == 404
    assert (
        client.patch(f"{BASE}/deals/{other['id']}", json={"name": "Z"}, headers=h).status_code
        == 404
    )
    assert client.delete(f"{BASE}/deals/{other['id']}", headers=h).status_code == 404
    r = client.post(f"{BASE}/deals/{other['id']}/stage", json={"stage_id": st["New"]}, headers=h)
    assert r.status_code == 404
    assert client.get(f"{BASE}/deals", headers=h).json()["data"]["total"] == 0


def test_validation_errors(client, db):
    _u, _s, h = _member(db)
    assert client.post(f"{BASE}/deals", json={"name": ""}, headers=h).status_code == 422
    assert (
        client.post(f"{BASE}/deals", json={"name": "A", "value_minor": -1}, headers=h).status_code
        == 422
    )
