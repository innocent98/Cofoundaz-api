from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"

NON_MARKETING_ROLES = [
    MembershipRole.mentor,
    MembershipRole.investor,
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def test_copy_generate_is_202_and_pollable(client, db):
    _u, _s, h = _member(db)
    r = client.post(
        f"{BASE}/copy/generate",
        json={"asset_type": "ad", "tone": "bold", "key_message": "Ship it"},
        headers=h,
    )
    assert r.status_code == 202, r.text
    gid = r.json()["data"]["id"]

    got = client.get(f"{BASE}/copy/generations/{gid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["kind"] == "copy"

    hist = client.get(f"{BASE}/copy/generations", headers=h)
    assert hist.status_code == 200, hist.text
    assert any(g["id"] == gid for g in hist.json()["data"]["generations"])


def test_plan_week_is_202_and_pollable(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/calendar/plan-week", headers=h)
    assert r.status_code == 202, r.text
    gid = r.json()["data"]["id"]

    got = client.get(f"{BASE}/calendar/plan-week/{gid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["kind"] == "plan_week"


def test_kind_mismatch_poll_404(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/calendar/plan-week", headers=h)
    assert r.status_code == 202, r.text
    gid = r.json()["data"]["id"]

    wrong = client.get(f"{BASE}/copy/generations/{gid}", headers=h)  # plan_week id via copy route
    assert wrong.status_code == 404, wrong.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_rbac_forbidden(client, db, role):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)

    posted = client.post(
        f"{BASE}/copy/generate",
        json={"asset_type": "ad", "tone": "bold", "key_message": "x"},
        headers=h,
    )
    assert posted.status_code == 403, posted.text

    listed = client.get(f"{BASE}/copy/generations", headers=h)
    assert listed.status_code == 403, listed.text

    planned = client.post(f"{BASE}/calendar/plan-week", headers=h)
    assert planned.status_code == 403, planned.text
