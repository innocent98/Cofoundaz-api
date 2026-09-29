from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/finance"
FORBIDDEN_ROLES = [MembershipRole.mentor, MembershipRole.investor]


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


def test_transaction_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-10",
            "description": "AWS",
            "amount_minor": 12050000,
            "currency": "NGN",
            "direction": "out",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    tid = created.json()["data"]["id"]
    assert created.json()["data"]["source"] == "manual"  # body source ignored/forced

    listed = client.get(f"{BASE}/transactions", headers=h)
    assert listed.status_code == 200
    assert any(t["id"] == tid for t in listed.json()["data"]["transactions"])

    uncat_before = client.get(f"{BASE}/transactions?uncategorized=true", headers=h)
    assert any(t["id"] == tid for t in uncat_before.json()["data"]["transactions"])

    patched = client.patch(
        f"{BASE}/transactions/{tid}", json={"category": "Infrastructure"}, headers=h
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["category"] == "Infrastructure"

    uncat = client.get(f"{BASE}/transactions?uncategorized=true", headers=h)
    assert uncat.status_code == 200
    assert all(t["id"] != tid for t in uncat.json()["data"]["transactions"])  # now categorized

    deleted = client.delete(f"{BASE}/transactions/{tid}", headers=h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"deleted": True}


def test_client_cannot_set_source(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-10",
            "description": "x",
            "amount_minor": 1,
            "direction": "in",
            "source": "import",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["source"] == "manual"


def test_negative_amount_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(
        f"{BASE}/transactions",
        json={"date": "2026-03-10", "description": "x", "amount_minor": -5, "direction": "out"},
        headers=h,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    posted = client.post(
        f"{BASE}/transactions",
        json={"date": "2026-03-10", "description": "x", "amount_minor": 1, "direction": "out"},
        headers=h,
    )
    assert posted.status_code == 403, posted.text


def test_accountant_allowed(client, db):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=MembershipRole.accountant, startup=startup)
    listed = client.get(f"{BASE}/transactions", headers=h)
    assert listed.status_code == 200, listed.text
