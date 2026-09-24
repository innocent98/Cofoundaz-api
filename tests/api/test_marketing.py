from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"

NON_MARKETING_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

ROUTES = [
    ("GET", "", None),
    ("POST", "/calendar-entries", {"title": "Launch", "channel": "email", "status": "draft"}),
    ("GET", "/calendar-entries", None),
    ("GET", "/channels", None),
    ("PATCH", "/channels/email", {"status": "active"}),
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


def _call(client, method, path, body, headers=None):
    return client.request(method, BASE + path, json=body, headers=headers)


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_roles_other_than_founder_and_team_member_are_forbidden(
    client, db, role, method, path, body
):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_founders_and_team_members_are_allowed(client, db, role, method, path, body):
    _u, _s, h = _member(db, role=role)
    client.get(f"{BASE}/channels", headers=h)  # lazy-seed so PATCH /channels/email finds a row
    r = _call(client, method, path, body, h)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unauthenticated_is_rejected(client, method, path, body):
    r = _call(client, method, path, body)
    assert r.status_code == 401, r.text


def test_founder_can_crud_calendar_entry(client, db):
    _u, _s, h = _member(db)
    r = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "Launch", "channel": "email", "status": "draft"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    eid = r.json()["data"]["id"]

    get_r = client.get(f"{BASE}/calendar-entries/{eid}", headers=h)
    assert get_r.status_code == 200, get_r.text
    assert get_r.json()["data"]["id"] == eid

    patch_r = client.patch(
        f"{BASE}/calendar-entries/{eid}", json={"status": "published"}, headers=h
    )
    assert patch_r.status_code == 200, patch_r.text
    assert patch_r.json()["data"]["published_at"] is not None

    list_r = client.get(f"{BASE}/calendar-entries", headers=h)
    assert list_r.status_code == 200
    assert list_r.json()["data"]["entries"]

    delete_r = client.delete(f"{BASE}/calendar-entries/{eid}", headers=h)
    assert delete_r.status_code == 200, delete_r.text
    assert delete_r.json()["data"] == {"deleted": True}

    assert client.get(f"{BASE}/calendar-entries/{eid}", headers=h).status_code == 404


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_calendar_entry_by_id_routes_are_forbidden_for_non_marketing_roles(client, db, role):
    _u, startup, h = _member(db)
    r = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "Launch", "channel": "email", "status": "draft"},
        headers=h,
    )
    eid = r.json()["data"]["id"]

    _other, _s, other_h = _member(db, role=role, startup=startup)
    assert client.get(f"{BASE}/calendar-entries/{eid}", headers=other_h).status_code == 403
    assert (
        client.patch(
            f"{BASE}/calendar-entries/{eid}", json={"title": "X"}, headers=other_h
        ).status_code
        == 403
    )
    assert client.delete(f"{BASE}/calendar-entries/{eid}", headers=other_h).status_code == 403


def test_scheduled_without_time_is_422(client, db):
    _u, _s, h = _member(db)
    r = client.post(
        f"{BASE}/calendar-entries",
        json={"title": "X", "channel": "email", "status": "scheduled"},
        headers=h,
    )
    assert r.status_code == 422, r.text


def test_channels_seed_and_update(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/channels", headers=h)
    assert r.status_code == 200
    assert len(r.json()["data"]) == 8

    r2 = client.patch(f"{BASE}/channels/email", json={"status": "active", "notes": "go"}, headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["status"] == "active"
    assert r2.json()["data"]["notes"] == "go"


def test_overview_shape(client, db):
    _u, _s, h = _member(db)
    r = client.get(BASE, headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data) == {
        "scheduled_this_week",
        "active_channels",
        "active_campaigns",
        "top_channel_by_conversions",
        "ai_content_ideas",
    }
