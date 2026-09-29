from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"
NON_MARKETING_ROLES = [MembershipRole.mentor, MembershipRole.investor]


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


def test_keyword_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/keywords",
        json={
            "keyword": "automated daily savings",
            "volume": "2.4K",
            "difficulty": 45,
            "current_rank": 12,
            "target_page": "/x",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    kid = created.json()["data"]["id"]
    assert created.json()["data"]["volume"] == "2.4K"

    listed = client.get(f"{BASE}/keywords", headers=h)
    assert listed.status_code == 200
    assert any(k["id"] == kid for k in listed.json()["data"]["keywords"])

    patched = client.patch(f"{BASE}/keywords/{kid}", json={"current_rank": 4}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["current_rank"] == 4

    deleted = client.delete(f"{BASE}/keywords/{kid}", headers=h)
    assert deleted.status_code == 200, deleted.text


def test_keyword_difficulty_out_of_range_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/keywords", json={"keyword": "x", "difficulty": 150}, headers=h)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_keyword_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    posted = client.post(f"{BASE}/keywords", json={"keyword": "x"}, headers=h)
    assert posted.status_code == 403, posted.text
    listed = client.get(f"{BASE}/keywords", headers=h)
    assert listed.status_code == 403, listed.text
