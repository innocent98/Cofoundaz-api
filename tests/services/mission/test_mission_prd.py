"""Today's Mission PRD completion: streak rules, best streak, milestone chips, Upcoming projection
and order, Completed log, per-workspace delivery time, notification copy."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core.errors import AppError
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, MissionStatus, MissionTaskStatus
from app.db.models.mission import MissionSettings, MissionTask
from app.db.models.roadmap import RoadmapTask
from app.services.mission import planning
from app.services.mission import service as svc
from app.services.notifications.registry import SPECS
from tests.factories import (
    create_membership,
    create_milestone,
    create_mission,
    create_mission_task,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)

MON = date(2026, 10, 12)  # a Monday
SAT, SUN = MON + timedelta(days=5), MON + timedelta(days=6)


@pytest.fixture
def ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s


def _pin(monkeypatch, day: date) -> None:
    monkeypatch.setattr(svc, "_today", lambda: day)
    monkeypatch.setattr(planning, "_today", lambda: day)


def _done(db, s, day):
    return create_mission(db, s, mission_date=day, status=MissionStatus.complete)


def _roadmap_with(db, s, titles, *, due=None):
    r = create_roadmap(db, startup=s)
    m = create_milestone(db, phase=create_phase(db, roadmap=r), title="Validate", due_on=due)
    tasks = [create_task(db, milestone=m, title=t) for t in titles]
    db.flush()
    return m, tasks


# --- streaks ---------------------------------------------------------------------------


def test_weekends_off_rest_days_do_not_break_the_streak(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON + timedelta(days=7))  # the next Monday, mission not done yet
    for d in (MON + timedelta(days=3), MON + timedelta(days=4)):  # Thu, Fri done
        _done(db, s, d)
    create_mission(db, s, mission_date=SAT)  # empty rest-day mission
    # Sunday: no mission row at all (founder didn't open the app)
    db.flush()
    assert svc.streak(db, s) == 2


def test_a_missed_weekday_breaks_the_streak(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON + timedelta(days=3))  # Thursday
    _done(db, s, MON)
    create_mission(db, s, mission_date=MON + timedelta(days=1))  # Tue pending -> break
    _done(db, s, MON + timedelta(days=2))
    db.flush()
    assert svc.streak(db, s) == 1


def test_weekend_missions_on_makes_weekends_count(db, ws, monkeypatch):
    _u, s = ws
    db.add(MissionSettings(startup_id=s.id, weekend_missions=True))
    _pin(monkeypatch, MON + timedelta(days=7))
    _done(db, s, MON + timedelta(days=4))  # Fri
    create_mission(db, s, mission_date=SAT)  # pending Saturday now breaks it
    db.flush()
    assert svc.streak(db, s) == 0


def test_streak_with_no_missions_terminates(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, SUN)  # weekend cursor with no rows must not walk back forever
    assert svc.streak(db, s) == 0
    assert svc.best_streak(db, s) == 0


def test_best_streak_is_the_longest_run(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON + timedelta(days=14))
    for i in range(5):  # Mon..Fri complete
        _done(db, s, MON + timedelta(days=i))
    for i in (7, 8):  # next Mon, Tue complete (weekend rest in between keeps the run)
        _done(db, s, MON + timedelta(days=i))
    create_mission(db, s, mission_date=MON + timedelta(days=9))  # Wed missed
    _done(db, s, MON + timedelta(days=10))
    db.flush()
    assert svc.best_streak(db, s) == 7


# --- milestone chip ----------------------------------------------------------------------


def test_today_tasks_carry_their_milestone(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    m, _tasks = _roadmap_with(db, s, ["Interview users"], due=MON + timedelta(days=4))
    mission = svc.get_or_generate_today(db, s)
    out = svc.serialize_mission(db, mission, 0)
    assert out["tasks"][0]["milestone"] == {
        "id": str(m.id),
        "title": "Validate",
        "due_on": (MON + timedelta(days=4)).isoformat(),
    }
    custom = svc.add_custom_task(db, mission, "Own task")
    assert svc.serialize_task(custom)["milestone"] is None


# --- upcoming ----------------------------------------------------------------------------


def test_upcoming_projects_seven_days_with_size_and_rest_days(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON + timedelta(days=3))  # Thursday
    db.add(MissionSettings(startup_id=s.id, mission_size=2))
    _roadmap_with(db, s, [f"T{i}" for i in range(8)])
    svc.get_or_generate_today(db, s)  # today takes T0, T1
    out = planning.upcoming(db, s)
    assert out["has_roadmap"] is True and out["mission_size"] == 2
    days = out["days"]
    assert [d["date"] for d in days][0] == (MON + timedelta(days=4)).isoformat()
    assert len(days) == 7
    assert [t["title"] for t in days[0]["tasks"]] == ["T2", "T3"]  # Friday
    assert days[1]["rest_day"] and days[2]["rest_day"]  # Sat, Sun off
    assert [t["title"] for t in days[3]["tasks"]] == ["T4", "T5"]  # Monday
    assert days[3]["tasks"][0]["milestone"]["title"] == "Validate"


def test_upcoming_carries_todays_snoozed_task_first(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _m, tasks = _roadmap_with(db, s, ["A", "B", "C", "D", "E"])
    mission = svc.get_or_generate_today(db, s)
    first = db.query(MissionTask).filter_by(mission_id=mission.id, title="A").one()
    svc.snooze_task(db, first)
    tomorrow = planning.upcoming(db, s)["days"][0]["tasks"]
    assert tomorrow[0]["title"] == "A" and tomorrow[0]["carried_over"] is True
    assert [t["title"] for t in tomorrow] == ["A", "D", "E"]
    del tasks


def test_upcoming_without_roadmap(db, ws):
    _u, s = ws
    assert planning.upcoming(db, s) == {"has_roadmap": False, "days": []}


def test_set_order_changes_the_projection_and_real_generation(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _m, tasks = _roadmap_with(db, s, ["A", "B", "C", "D"])
    a, b, c, d = tasks
    planning.set_upcoming_order(db, s, [d.id, c.id])
    assert db.get(RoadmapTask, d.id).mission_rank == 0
    assert db.get(RoadmapTask, a.id).mission_rank is None  # unlisted tasks are unranked
    mission = svc.get_or_generate_today(db, s)
    titles = [
        t.title
        for t in db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order)
    ]
    assert titles == ["D", "C", "A"]
    del b


@pytest.mark.parametrize(
    ("ids", "code"),
    [("dup", "VALIDATION_ERROR"), ("foreign", "VALIDATION_ERROR")],
)
def test_set_order_validation(db, ws, ids, code):
    _u, s = ws
    _m, (a, _b) = _roadmap_with(db, s, ["A", "B"])
    other = create_startup(db, owner=create_user(db))
    _om, (x,) = _roadmap_with(db, other, ["X"])
    bad = [a.id, a.id] if ids == "dup" else [x.id]
    with pytest.raises(AppError) as exc:
        planning.set_upcoming_order(db, s, bad)
    assert exc.value.code == code


def test_set_order_without_roadmap_is_409(db, ws):
    _u, s = ws
    with pytest.raises(AppError) as exc:
        planning.set_upcoming_order(db, s, [create_user(db).id])
    assert (exc.value.code, exc.value.http_status) == ("NO_ROADMAP", 409)


# --- completed log -----------------------------------------------------------------------


def test_completed_log_newest_first_with_cursor(db, ws):
    _u, s = ws
    base = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    m = create_mission(db, s, mission_date=base.date())
    for i in range(3):
        create_mission_task(
            db,
            m,
            title=f"done {i}",
            status=MissionTaskStatus.done,
            completed_at=base + timedelta(hours=i),
            order=i,
        )
    create_mission_task(db, m, title="still todo", order=9)
    db.flush()
    page1 = planning.completed_log(db, s, limit=2)
    assert [i["title"] for i in page1["items"]] == ["done 2", "done 1"]
    assert page1["items"][0]["mission_date"] == base.date().isoformat()
    page2 = planning.completed_log(db, s, cursor=page1["next_cursor"], limit=2)
    assert [i["title"] for i in page2["items"]] == ["done 0"]
    assert page2["next_cursor"] is None
    with pytest.raises(AppError):
        planning.completed_log(db, s, cursor="not-a-cursor")


# --- scheduler: per-workspace delivery time ----------------------------------------------


def test_mission_is_due_at_the_workspace_delivery_time(db, ws, monkeypatch):
    from app.core.config import settings
    from app.worker import scheduler

    _u, s = ws
    monkeypatch.setattr(settings, "SCHEDULER_TIMEZONE", "UTC")
    db.add(MissionSettings(startup_id=s.id, delivery_time=time(8, 30)))
    db.flush()

    def due(hh, mm):
        now = datetime(2026, 10, 12, hh, mm, tzinfo=UTC)
        return [d for d in scheduler._due_missions(db, now) if d.scope_key == str(s.id)]

    assert due(8, 29) == []
    assert len(due(8, 30)) == 1
    other = create_startup(db, owner=create_user(db))  # no settings row -> default 06:00
    create_membership(db, create_user(db), other)
    db.flush()
    early = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
    assert any(d.scope_key == str(other.id) for d in scheduler._due_missions(db, early))


# --- notification copy -------------------------------------------------------------------


def test_prd_notification_copy():
    ready = SPECS["mission.ready"]
    assert ready.title({}) == "Your mission for today is ready"
    assert ready.body({"first_task_title": "Call 3 users"}) == "Call 3 users"
    streak = SPECS["mission.streak.milestone"]
    assert streak.title({"streak": 7}) == (
        "🔥 7 days straight. You're building a habit most founders never do."
    )
    assert streak.title({}) == "You hit a mission streak milestone"


# --- endpoints -----------------------------------------------------------------------------


def _h(u, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def test_endpoints_and_roles(client, db):
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=founder)
    create_membership(db, founder, s, role=MembershipRole.founder)
    mentor = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    _m, (a, b) = _roadmap_with(db, s, ["A", "B"])
    db.commit()

    up = client.get("/api/v1/missions/upcoming", headers=_h(founder, s))
    assert up.status_code == 200 and up.json()["data"]["has_roadmap"] is True
    body = {"roadmap_task_ids": [str(b.id), str(a.id)]}
    assert (
        client.put("/api/v1/missions/upcoming/order", json=body, headers=_h(mentor, s)).status_code
        == 403
    )
    r = client.put("/api/v1/missions/upcoming/order", json=body, headers=_h(founder, s))
    assert r.status_code == 200, r.text
    # PRD Module 04 access is founder + team member: a mentor reads nothing here either.
    for path in ("today", "upcoming", "completed", "history", "settings"):
        assert client.get(f"/api/v1/missions/{path}", headers=_h(mentor, s)).status_code == 403
    hist = client.get("/api/v1/missions/history", headers=_h(founder, s)).json()["data"]
    assert {"current_streak", "best_streak"} <= set(hist)


def test_today_lists_my_assigned_tasks_and_empty_reason(client, db, monkeypatch):
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=founder)
    create_membership(db, founder, s, role=MembershipRole.founder)
    mate = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, mate, s, role=MembershipRole.team_member)
    r = create_roadmap(db, startup=s)
    m = create_milestone(db, phase=create_phase(db, roadmap=r), title="Build")
    create_task(db, milestone=m, title="Mine", assignee=mate)
    create_task(db, milestone=m, title="Not mine")
    db.commit()
    data = client.get("/api/v1/missions/today", headers=_h(mate, s)).json()["data"]
    assert [t["title"] for t in data["assigned_to_me"]] == ["Mine"]
    assert data["assigned_to_me"][0]["milestone"]["title"] == "Build"
    assert "empty_reason" in data
    founder_view = client.get("/api/v1/missions/today", headers=_h(founder, s)).json()["data"]
    assert founder_view["assigned_to_me"] == []


def test_today_without_roadmap_still_reports_assigned(client, db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.commit()
    data = client.get("/api/v1/missions/today", headers=_h(u, s)).json()["data"]
    assert data == {"status": "no_roadmap", "assigned_to_me": []}
