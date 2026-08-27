"""Tests for get_or_generate_today / streak / serialize_mission.

app/services/mission/service.py is READ-ONLY against the roadmap: it selects
incomplete roadmap tasks (status != done) ordered by
(milestone.due_on asc nulls last, phase.order, milestone.order, task.order),
carrying forward any `snoozed` tasks from the most recent prior mission first,
then filling to `mission_settings.mission_size` (default 3).
"""

import uuid
from datetime import date, timedelta

from app.db.models.enums import MissionStatus, MissionTaskStatus, RoadmapStatus
from app.db.models.mission import MissionTask
from app.services.mission import service as mission_service
from app.services.mission.service import get_or_generate_today, serialize_mission, streak
from tests.factories import (
    create_milestone,
    create_mission,
    create_mission_settings,
    create_mission_task,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def test_generates_from_roadmap_tasks(db):
    s = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m1 = create_milestone(db, ph, due_on=date.today() + timedelta(days=2))
    for i in range(5):
        create_task(db, m1, title=f"T{i}", status=RoadmapStatus.todo, order=i)
    db.flush()

    mission = get_or_generate_today(db, s)

    assert mission is not None
    # default mission_size = 3
    got = db.query(MissionTask).filter_by(mission_id=mission.id).all()
    assert len(got) == 3
    assert all(t.roadmap_task_id is not None for t in got)
    assert [t.title for t in sorted(got, key=lambda t: t.order)] == ["T0", "T1", "T2"]


def test_no_roadmap_returns_none(db):
    s = create_startup(db, owner=create_user(db))
    assert get_or_generate_today(db, s) is None


def test_generation_is_idempotent(db):
    s = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    create_task(db, create_milestone(db, ph), status=RoadmapStatus.todo)
    db.flush()

    a = get_or_generate_today(db, s)
    db.flush()
    b = get_or_generate_today(db, s)

    assert a.id == b.id
    assert db.query(MissionTask).filter_by(mission_id=a.id).count() == 1


def test_respects_mission_settings_size(db):
    s = create_startup(db, owner=create_user(db))
    create_mission_settings(db, s, mission_size=1)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m = create_milestone(db, ph)
    for i in range(3):
        create_task(db, m, title=f"T{i}", order=i)
    db.flush()

    mission = get_or_generate_today(db, s)

    assert db.query(MissionTask).filter_by(mission_id=mission.id).count() == 1


def test_orders_by_milestone_due_date_then_phase_then_milestone_then_task_order(db):
    s = create_startup(db, owner=create_user(db))
    create_mission_settings(db, s, mission_size=2)
    r = create_roadmap(db, s)
    # Phase order alone would put NoDue first (order=0); due_on must win as the
    # primary sort key regardless of phase order.
    ph_low_order = create_phase(db, r, order=0)
    ph_high_order = create_phase(db, r, order=1)
    m_no_due = create_milestone(db, ph_low_order, title="NoDue", due_on=None, order=0)
    m_due = create_milestone(
        db, ph_high_order, title="Due", due_on=date.today() + timedelta(days=1), order=0
    )
    create_task(db, m_no_due, title="LateTask")
    create_task(db, m_due, title="EarlyTask")
    db.flush()

    mission = get_or_generate_today(db, s)
    tasks = sorted(
        db.query(MissionTask).filter_by(mission_id=mission.id).all(), key=lambda t: t.order
    )

    assert [t.title for t in tasks] == ["EarlyTask", "LateTask"]


def test_excludes_done_roadmap_tasks(db):
    s = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m = create_milestone(db, ph)
    create_task(db, m, title="Done", status=RoadmapStatus.done, order=0)
    create_task(db, m, title="Todo", status=RoadmapStatus.todo, order=1)
    db.flush()

    mission = get_or_generate_today(db, s)
    tasks = db.query(MissionTask).filter_by(mission_id=mission.id).all()

    assert [t.title for t in tasks] == ["Todo"]


def test_carries_forward_snoozed_tasks_first(db):
    s = create_startup(db, owner=create_user(db))
    create_mission_settings(db, s, mission_size=2)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    m = create_milestone(db, ph)
    create_task(db, m, title="Fresh1", order=0)
    create_task(db, m, title="Fresh2", order=1)
    db.flush()

    yesterday = create_mission(db, s, mission_date=date.today() - timedelta(days=1))
    snoozed = create_mission_task(
        db,
        yesterday,
        title="Snoozed",
        status=MissionTaskStatus.snoozed,
        roadmap_task_id=uuid.uuid4(),
    )
    db.flush()

    mission = get_or_generate_today(db, s)
    tasks = sorted(
        db.query(MissionTask).filter_by(mission_id=mission.id).all(), key=lambda t: t.order
    )

    assert len(tasks) == 2
    assert tasks[0].title == "Snoozed"
    assert tasks[0].roadmap_task_id == snoozed.roadmap_task_id
    assert tasks[0].order == 0


def test_weekend_off_generates_empty_mission(monkeypatch, db):
    s = create_startup(db, owner=create_user(db))
    create_mission_settings(db, s, weekend_missions=False)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    create_task(db, create_milestone(db, ph))
    db.flush()

    saturday = date(2026, 8, 29)
    assert saturday.weekday() == 5
    monkeypatch.setattr(mission_service, "_today", lambda: saturday)

    mission = get_or_generate_today(db, s)

    assert mission is not None
    assert mission.mission_date == saturday
    assert db.query(MissionTask).filter_by(mission_id=mission.id).count() == 0


def test_weekend_on_still_generates_tasks(monkeypatch, db):
    s = create_startup(db, owner=create_user(db))
    create_mission_settings(db, s, weekend_missions=True)
    r = create_roadmap(db, s)
    ph = create_phase(db, r)
    create_task(db, create_milestone(db, ph))
    db.flush()

    saturday = date(2026, 8, 29)
    monkeypatch.setattr(mission_service, "_today", lambda: saturday)

    mission = get_or_generate_today(db, s)

    assert db.query(MissionTask).filter_by(mission_id=mission.id).count() == 1


def test_streak_counts_consecutive_completed(db):
    s = create_startup(db, owner=create_user(db))
    for d in range(1, 4):  # yesterday, 2 days ago, 3 days ago all complete
        create_mission(
            db, s, mission_date=date.today() - timedelta(days=d), status=MissionStatus.complete
        )
    db.flush()
    assert streak(db, s) == 3


def test_streak_broken_by_gap(db):
    s = create_startup(db, owner=create_user(db))
    create_mission(
        db, s, mission_date=date.today() - timedelta(days=1), status=MissionStatus.complete
    )
    create_mission(
        db, s, mission_date=date.today() - timedelta(days=3), status=MissionStatus.complete
    )
    db.flush()
    assert streak(db, s) == 1


def test_streak_includes_today_when_complete(db):
    s = create_startup(db, owner=create_user(db))
    create_mission(db, s, mission_date=date.today(), status=MissionStatus.complete)
    create_mission(
        db, s, mission_date=date.today() - timedelta(days=1), status=MissionStatus.complete
    )
    db.flush()
    assert streak(db, s) == 2


def test_streak_starts_from_yesterday_when_today_incomplete(db):
    s = create_startup(db, owner=create_user(db))
    create_mission(db, s, mission_date=date.today(), status=MissionStatus.pending)
    for d in range(1, 3):
        create_mission(
            db, s, mission_date=date.today() - timedelta(days=d), status=MissionStatus.complete
        )
    db.flush()
    assert streak(db, s) == 2


def test_streak_zero_when_no_missions(db):
    s = create_startup(db, owner=create_user(db))
    assert streak(db, s) == 0


def test_serialize_mission_shape(db):
    s = create_startup(db, owner=create_user(db))
    m = create_mission(db, s)
    create_mission_task(db, m, title="Do the thing", order=0)
    db.flush()

    out = serialize_mission(db, m, 5)

    assert out["mission_date"] == m.mission_date.isoformat()
    assert out["status"] == "pending"
    assert out["streak"] == 5
    assert len(out["tasks"]) == 1
    t = out["tasks"][0]
    assert t["title"] == "Do the thing"
    assert t["status"] == "todo"
    assert t["effort"] == "medium"
    assert t["completed_at"] is None
    assert t["order"] == 0
