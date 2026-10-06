from datetime import UTC, date, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)

BASE = "/api/v1/calendar"
# Unlike Finance, accountant has no calendar grant (spec D4): a calendar is not finance-scoped.
FORBIDDEN_ROLES = [
    MembershipRole.mentor,
    MembershipRole.investor,
    MembershipRole.accountant,
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


def _create(client, h, **kw):
    body = {"title": "Partner call", "type": "meeting", "event_date": "2026-07-06"}
    body.update(kw)
    return client.post(f"{BASE}/events", json=body, headers=h)


def test_event_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = _create(client, h, start_time="14:00", end_time="15:30", description="Deck")
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    eid = data["id"]
    # D6: HH:MM on the wire, never HH:MM:SS.
    assert data["start_time"] == "14:00" and data["end_time"] == "15:30"
    assert data["all_day"] is False and data["type"] == "meeting"

    listed = client.get(f"{BASE}/events", headers=h)
    assert listed.status_code == 200
    assert any(e["id"] == eid for e in listed.json()["data"]["events"])

    one = client.get(f"{BASE}/events/{eid}", headers=h)
    assert one.status_code == 200 and one.json()["data"]["title"] == "Partner call"

    patched = client.patch(f"{BASE}/events/{eid}", json={"title": "Renamed"}, headers=h)
    assert patched.status_code == 200
    assert patched.json()["data"]["title"] == "Renamed"

    cleared = client.patch(
        f"{BASE}/events/{eid}", json={"start_time": None, "end_time": None}, headers=h
    )
    assert cleared.json()["data"]["start_time"] is None

    deleted = client.delete(f"{BASE}/events/{eid}", headers=h)
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True}

    gone = client.get(f"{BASE}/events/{eid}", headers=h)
    assert gone.status_code == 404


def test_create_with_only_the_three_fe_fields(client, db):
    _u, _s, h = _member(db)
    r = _create(client, h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # all_day is declared, never inferred from the absence of a time (D12).
    assert data["all_day"] is False
    assert data["start_time"] is None and data["end_time"] is None


def test_empty_calendar_lists_nothing(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/events", headers=h)
    assert r.status_code == 200 and r.json()["data"]["events"] == []


def test_range_and_type_filters(client, db):
    _u, _s, h = _member(db)
    _create(client, h, title="June", event_date="2026-06-30")
    _create(client, h, title="July 1", event_date="2026-07-01")
    _create(client, h, title="July 31", event_date="2026-07-31")
    _create(client, h, title="A deadline", event_date="2026-07-10", type="deadline")

    ranged = client.get(f"{BASE}/events?date_from=2026-07-01&date_to=2026-07-31", headers=h)
    titles = [e["title"] for e in ranged.json()["data"]["events"]]
    assert titles == ["July 1", "A deadline", "July 31"]  # boundaries included

    filtered = client.get(f"{BASE}/events?type=deadline", headers=h)
    assert [e["title"] for e in filtered.json()["data"]["events"]] == ["A deadline"]


@pytest.mark.parametrize(
    "body",
    [
        {"all_day": True, "start_time": "09:00"},  # D9
        {"end_time": "09:00"},  # end with no start
        {"start_time": "10:00", "end_time": "09:00"},  # end before start
        {"title": ""},  # empty title
        {"type": "birthday"},  # not one of the four
    ],
)
def test_bad_create_bodies_are_422_with_field_errors(client, db, body):
    _u, _s, h = _member(db)
    r = _create(client, h, **body)
    assert r.status_code == 422, r.text
    payload = r.json()
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["field_errors"]


def test_patch_explicit_null_on_a_required_field_is_422(client, db):
    _u, _s, h = _member(db)
    eid = _create(client, h).json()["data"]["id"]
    r = client.patch(f"{BASE}/events/{eid}", json={"title": None}, headers=h)
    assert r.status_code == 422


def test_patch_is_validated_against_the_merged_row(client, db):
    _u, _s, h = _member(db)
    eid = _create(client, h, start_time="09:00", end_time="10:00").json()["data"]["id"]
    # Harmless on its own; nonsense against the stored end_time.
    r = client.patch(f"{BASE}/events/{eid}", json={"start_time": "11:00"}, headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_cross_tenant_ids_are_404_not_403(client, db):
    _u, _s, h = _member(db)
    _other_u, _other_s, other_h = _member(db)
    theirs = _create(client, other_h).json()["data"]["id"]

    assert client.get(f"{BASE}/events/{theirs}", headers=h).status_code == 404
    assert (
        client.patch(f"{BASE}/events/{theirs}", json={"title": "X"}, headers=h).status_code == 404
    )
    assert client.delete(f"{BASE}/events/{theirs}", headers=h).status_code == 404


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_touch_the_calendar(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=role, startup=startup)

    assert client.get(f"{BASE}/events", headers=h).status_code == 403
    assert _create(client, h).status_code == 403
    assert client.get(f"{BASE}/milestones", headers=h).status_code == 403


def test_milestones_projects_the_roadmap_and_is_scoped(client, db):
    _u, s, h = _member(db)
    _other_u, other_s, _other_h = _member(db)
    phase = create_phase(db, create_roadmap(db, s), name="Validation")
    create_milestone(db, phase, title="Validate demand", progress=85, due_on=date(2026, 12, 1))
    create_milestone(db, create_phase(db, create_roadmap(db, other_s)), title="Theirs")
    db.flush()

    r = client.get(f"{BASE}/milestones", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["milestones"]
    assert [m["title"] for m in rows] == ["Validate demand"]  # never another workspace's
    assert rows[0]["progress"] == 85
    assert rows[0]["due_on"] == "2026-12-01"  # ISO, not the FE's "Due Friday, Jul 31"
    assert rows[0]["status"] == "todo"  # raw
    assert rows[0]["calendar_status"] == "not_started"  # derived
    assert "tasksInfo" not in rows[0] and "tasks_info" not in rows[0]  # not backed


def test_unauthenticated_is_rejected(client, db):
    _u, s, _h = _member(db)
    r = client.get(f"{BASE}/events", headers={"X-Workspace-Id": str(s.id)})
    assert r.status_code in (401, 403)
