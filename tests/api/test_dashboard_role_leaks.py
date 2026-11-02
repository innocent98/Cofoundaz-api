"""D-MIS-8 follow-through on the Dashboard: Today's Mission is founder + team member only.

Covers `GET /dashboard/summary` (`mission`), `GET /search` (mission tasks, the Mission and
Assessment pages), for every MembershipRole.
"""

from datetime import UTC, date, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, MissionTaskStatus, StartupStage
from app.db.models.mission import Mission, MissionTask
from tests.factories import (
    create_membership,
    create_milestone,
    create_mission_settings,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)

MISSION_ROLES = (MembershipRole.founder, MembershipRole.team_member)
BLOCKED = tuple(r for r in MembershipRole if r not in MISSION_ROLES)
SECRET_TITLE = "Zebra pitch deck rewrite"


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _workspace(db):
    """A founder-owned workspace with today's mission already holding one secret-titled task."""
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner, stage=StartupStage.validation)
    create_membership(db, owner, s, role=MembershipRole.founder)
    m = Mission(startup_id=s.id, mission_date=date.today())
    db.add(m)
    db.flush()
    db.add(
        MissionTask(
            mission_id=m.id,
            title=SECRET_TITLE,
            reason="Because zebras",
            status=MissionTaskStatus.todo,
            order=0,
        )
    )
    db.flush()
    return owner, s


def _as(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return _headers(u, startup)


@pytest.mark.parametrize("role", MISSION_ROLES)
def test_summary_mission_is_returned_to_founder_and_team_member(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()

    data = client.get("/api/v1/dashboard/summary", headers=h).json()["data"]
    assert data["mission_status"] == "ok"
    assert [t["title"] for t in data["mission"]["tasks"]] == [SECRET_TITLE]


@pytest.mark.parametrize("role", BLOCKED)
def test_summary_mission_is_null_and_restricted_for_every_other_role(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()

    r = client.get("/api/v1/dashboard/summary", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["mission"] is None
    assert data["mission_status"] == "restricted"
    # nothing of the mission leaks anywhere else in the payload
    assert SECRET_TITLE not in r.text
    assert "Because zebras" not in r.text


@pytest.mark.parametrize("role", BLOCKED)
def test_summary_does_not_lazily_generate_a_mission_for_a_blocked_role(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner, stage=StartupStage.validation)
    create_membership(db, owner, s, role=MembershipRole.founder)
    create_mission_settings(db, s, weekend_missions=True)
    r_ = create_roadmap(db, s)
    m = create_milestone(db, create_phase(db, r_), title="Validate demand")
    create_task(db, m, title="T0")
    h = _as(db, s, role)
    db.commit()

    assert client.get("/api/v1/dashboard/summary", headers=h).status_code == 200
    assert db.query(Mission).filter_by(startup_id=s.id).count() == 0


@pytest.mark.parametrize("role", BLOCKED)
def test_missions_today_stays_403_for_blocked_roles(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()
    assert client.get("/api/v1/missions/today", headers=h).status_code == 403


def _search(client, h, q):
    r = client.get("/api/v1/search", params={"q": q}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]["results"]


@pytest.mark.parametrize("role", MISSION_ROLES)
def test_search_finds_mission_tasks_and_mission_page_for_core_roles(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()

    assert [r["type"] for r in _search(client, h, "zebra")] == ["mission_task"]
    titles = {r["title"] for r in _search(client, h, "mission")}
    assert "Today's Mission" in titles


@pytest.mark.parametrize("role", BLOCKED)
def test_search_hides_mission_tasks_and_mission_page_from_other_roles(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()

    assert _search(client, h, "zebra") == []
    assert "Today's Mission" not in {r["title"] for r in _search(client, h, "mission")}


@pytest.mark.parametrize("role", MISSION_ROLES)
def test_search_offers_the_assessment_page_to_founder_and_team_member(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()
    hits = [r for r in _search(client, h, "assessment") if r["type"] == "page"]
    assert [(r["title"], r["href"]) for r in hits] == [("Assessment", "/assessment")]


@pytest.mark.parametrize("role", BLOCKED)
def test_search_hides_the_assessment_page_from_other_roles(client, db, role):
    _owner, s = _workspace(db)
    h = _as(db, s, role)
    db.commit()
    assert _search(client, h, "assessment") == []
