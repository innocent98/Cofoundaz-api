import uuid

import pytest

from app.core.redis import get_redis
from app.db.models.enums import LeadStatus, MembershipRole
from app.db.models.sales import SalesLead
from tests.api.sales.test_people_api import OTHER_ROLES, _member

BASE = "/api/v1/sales"
_ID = str(uuid.uuid4())
ROUTES = [
    ("GET", "/leads", None),
    ("POST", "/leads", {"name": "X"}),
    ("GET", f"/leads/{_ID}", None),
    ("PATCH", f"/leads/{_ID}", {"name": "Y"}),
    ("DELETE", f"/leads/{_ID}", None),
    ("POST", "/leads/import", {"upload_id": "a" * 32, "mapping": {"name": "Name"}}),
]


@pytest.fixture(autouse=True)
def _clean_redis():
    yield
    r = get_redis()
    for k in r.scan_iter("sales:csv:*"):
        r.delete(k)


@pytest.mark.parametrize("role", OTHER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_other_roles_forbidden(client, db, role, method, path, body):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = client.request(method, BASE + path, json=body, headers=h)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_other_roles_forbidden_preview(client, db, role):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    files = {"file": ("l.csv", b"Name\na\n", "text/csv")}
    r = client.post(f"{BASE}/leads/import/preview", files=files, headers=h)
    assert r.status_code == 403, r.text


def test_requires_auth(client):
    assert client.get(f"{BASE}/leads").status_code in (401, 403)


def test_lead_crud_flow(client, db):
    u, s, h = _member(db)
    r = client.post(
        f"{BASE}/leads",
        json={"name": "Jane", "company": "Acme", "email": "jane@acme.co", "owner_id": str(u.id)},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    lead = r.json()["data"]
    assert lead["status"] == "new" and lead["source"] == "manual"
    lid = lead["id"]

    d = client.get(f"{BASE}/leads", headers=h).json()["data"]
    assert d["total"] == 1 and d["limit"] == 50 and d["offset"] == 0 and d["items"][0]["id"] == lid
    d = client.get(f"{BASE}/leads?status=working", headers=h).json()["data"]
    assert d["total"] == 0
    d = client.get(f"{BASE}/leads?q=acme", headers=h).json()["data"]
    assert d["total"] == 1

    r = client.patch(f"{BASE}/leads/{lid}", json={"status": "working"}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "working"
    assert client.get(f"{BASE}/leads/{lid}", headers=h).json()["data"]["id"] == lid
    assert client.delete(f"{BASE}/leads/{lid}", headers=h).json()["data"] == {"deleted": True}
    assert client.get(f"{BASE}/leads/{lid}", headers=h).status_code == 404


def test_lead_validation_errors(client, db):
    _u, _s, h = _member(db)
    assert client.post(f"{BASE}/leads", json={"email": "a@b.co"}, headers=h).status_code == 422
    assert (
        client.post(f"{BASE}/leads", json={"name": "A", "email": "nope"}, headers=h).status_code
        == 422
    )
    r = client.post(f"{BASE}/leads", json={"name": "A", "status": "converted"}, headers=h)
    assert r.status_code == 422
    r = client.post(f"{BASE}/leads", json={"name": "A", "owner_id": _ID}, headers=h)
    assert r.status_code == 422


def test_converted_lead_locked(client, db):
    _u, s, h = _member(db)
    row = SalesLead(startup_id=s.id, name="A", status=LeadStatus.converted)
    db.add(row)
    db.flush()
    r = client.patch(f"{BASE}/leads/{row.id}", json={"status": "new"}, headers=h)
    assert r.status_code == 422 and "already converted" in r.text
    assert client.delete(f"{BASE}/leads/{row.id}", headers=h).status_code == 422


def test_cross_tenant_lead_404(client, db):
    _u, s, _h = _member(db)
    _u2, _s2, h2 = _member(db)
    row = SalesLead(startup_id=s.id, name="A")
    db.add(row)
    db.flush()
    assert client.get(f"{BASE}/leads/{row.id}", headers=h2).status_code == 404
    assert client.patch(f"{BASE}/leads/{row.id}", json={"name": "B"}, headers=h2).status_code == 404
    assert client.delete(f"{BASE}/leads/{row.id}", headers=h2).status_code == 404


def test_team_member_can_use_leads(client, db):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.team_member, startup=startup)
    assert client.get(f"{BASE}/leads", headers=h).status_code == 200


def _upload(client, h, content: bytes, name="l.csv"):
    return client.post(
        f"{BASE}/leads/import/preview", files={"file": (name, content, "text/csv")}, headers=h
    )


def test_import_flow(client, db):
    _u, _s, h = _member(db)
    r = _upload(client, h, b"Name,Company,Email\nJane,Acme,jane@acme.co\nBob,,bad\n")
    assert r.status_code == 200, r.text
    p = r.json()["data"]
    assert p["row_count"] == 2 and p["suggested_mapping"]["name"] == "Name"
    r = client.post(
        f"{BASE}/leads/import",
        json={"upload_id": p["upload_id"], "mapping": p["suggested_mapping"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["added"] == 1 and d["invalid_total"] == 1 and d["invalid_rows"][0]["row"] == 2
    assert client.get(f"{BASE}/leads", headers=h).json()["data"]["total"] == 1
    # same upload twice
    r = client.post(
        f"{BASE}/leads/import",
        json={"upload_id": p["upload_id"], "mapping": p["suggested_mapping"]},
        headers=h,
    )
    assert r.status_code == 404


def test_import_route_not_shadowed_by_lead_id(client, db):
    _u, _s, h = _member(db)
    r = client.post(
        f"{BASE}/leads/import", json={"upload_id": "z" * 32, "mapping": {"name": "N"}}, headers=h
    )
    assert r.status_code == 404  # not 422 from a uuid path parse


def test_import_foreign_workspace_404(client, db):
    _u, _s, h = _member(db)
    _u2, _s2, h2 = _member(db)
    p = _upload(client, h, b"Name\na\n").json()["data"]
    r = client.post(
        f"{BASE}/leads/import",
        json={"upload_id": p["upload_id"], "mapping": {"name": "Name"}},
        headers=h2,
    )
    assert r.status_code == 404


def test_preview_too_large_413_and_empty_422(client, db):
    _u, _s, h = _member(db)
    assert _upload(client, h, b"a" * (2 * 1024 * 1024 + 10)).status_code == 413
    assert _upload(client, h, b"").status_code == 422


def test_preview_missing_file_422(client, db):
    _u, _s, h = _member(db)
    assert client.post(f"{BASE}/leads/import/preview", headers=h).status_code == 422


def test_import_mapping_missing_required_422(client, db):
    _u, _s, h = _member(db)
    p = _upload(client, h, b"Name,Email\na,a@b.co\n").json()["data"]
    r = client.post(
        f"{BASE}/leads/import",
        json={"upload_id": p["upload_id"], "mapping": {"email": "Email"}},
        headers=h,
    )
    assert r.status_code == 422
