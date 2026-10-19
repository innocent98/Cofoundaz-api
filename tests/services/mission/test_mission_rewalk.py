"""Today's Mission fixes from the 2026-10-19 click-through re-walk: mission work flows back to
the roadmap, rejected tasks are calibrated out of future missions (D-MIS-7), and a snoozed
task no longer blocks "Mission complete." or the streak."""

from datetime import date, timedelta

import pytest

from app.db.models.enums import MembershipRole, MissionStatus, MissionTaskStatus, RoadmapStatus
from app.db.models.mission import MissionTask
from app.services.mission import planning
from app.services.mission import service as svc
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)

MON = date(2026, 10, 12)  # a Monday
TUE = MON + timedelta(days=1)


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


def _roadmap_with(db, s, titles):
    r = create_roadmap(db, startup=s)
    m = create_milestone(db, phase=create_phase(db, roadmap=r), title="Validate")
    tasks = [create_task(db, milestone=m, title=t, order=i) for i, t in enumerate(titles)]
    db.flush()
    return m, tasks


def _today_task(db, mission, title) -> MissionTask:
    return db.query(MissionTask).filter_by(mission_id=mission.id, title=title).one()


def _titles(db, mission) -> list[str]:
    rows = db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order)
    return [t.title for t in rows]


# --- mission work flows back to the roadmap ---------------------------------------------


def test_completing_a_mission_task_completes_its_roadmap_task(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    milestone, tasks = _roadmap_with(db, s, ["A", "B", "C", "D"])
    mission = svc.get_or_generate_today(db, s)
    svc.complete_task(db, s, _today_task(db, mission, "A"))
    db.refresh(tasks[0])
    db.refresh(milestone)
    assert tasks[0].status == RoadmapStatus.done
    assert milestone.progress == 25

    _pin(monkeypatch, TUE)
    assert "A" not in _titles(db, svc.get_or_generate_today(db, s))


def test_custom_task_completion_touches_no_roadmap_task(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _m, tasks = _roadmap_with(db, s, ["A"])
    mission = svc.get_or_generate_today(db, s)
    own = svc.add_custom_task(db, mission, "Call the bank", None)
    svc.complete_task(db, s, own)
    db.refresh(tasks[0])
    assert tasks[0].status == RoadmapStatus.todo


def test_reject_already_done_completes_the_roadmap_task(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _m, tasks = _roadmap_with(db, s, ["A", "B", "C", "D"])
    mission = svc.get_or_generate_today(db, s)
    svc.reject_task(db, _today_task(db, mission, "A"), "Already done")
    db.refresh(tasks[0])
    assert tasks[0].status == RoadmapStatus.done


def test_reject_doesnt_apply_is_never_offered_again(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _m, tasks = _roadmap_with(db, s, ["A", "B", "C", "D", "E"])
    mission = svc.get_or_generate_today(db, s)
    svc.reject_task(db, _today_task(db, mission, "A"), "Doesn't apply")
    db.refresh(tasks[0])
    assert tasks[0].status == RoadmapStatus.todo  # still on the roadmap
    projected = [t["title"] for d in planning.upcoming(db, s)["days"] for t in d["tasks"]]
    assert "A" not in projected

    _pin(monkeypatch, MON + timedelta(days=21))
    assert "A" not in _titles(db, svc.get_or_generate_today(db, s))


def test_reject_wrong_priority_rests_for_a_week(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _m, _tasks = _roadmap_with(db, s, ["A", "B", "C", "D", "E", "F", "G"])
    mission = svc.get_or_generate_today(db, s)
    svc.reject_task(db, _today_task(db, mission, "A"), "Wrong priority")

    _pin(monkeypatch, TUE)
    assert "A" not in _titles(db, svc.get_or_generate_today(db, s))

    _pin(monkeypatch, MON + timedelta(days=7))
    assert "A" in _titles(db, svc.get_or_generate_today(db, s))


# --- a snoozed task is resolved for today -----------------------------------------------


def test_snoozed_task_does_not_block_mission_complete(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _roadmap_with(db, s, ["A", "B", "C"])
    mission = svc.get_or_generate_today(db, s)
    svc.snooze_task(db, _today_task(db, mission, "C"))
    svc.reject_task(db, _today_task(db, mission, "B"), "Wrong priority")
    svc.complete_task(db, s, _today_task(db, mission, "A"))
    db.refresh(mission)
    assert mission.status == MissionStatus.complete
    assert svc.streak(db, s) == 1


def test_snoozing_the_last_open_task_completes_the_mission(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _roadmap_with(db, s, ["A", "B"])
    mission = svc.get_or_generate_today(db, s)
    svc.complete_task(db, s, _today_task(db, mission, "A"))
    svc.snooze_task(db, _today_task(db, mission, "B"))
    db.refresh(mission)
    assert mission.status == MissionStatus.complete


def test_snoozing_everything_never_completes_the_mission(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _roadmap_with(db, s, ["A"])
    mission = svc.get_or_generate_today(db, s)
    svc.snooze_task(db, _today_task(db, mission, "A"))
    db.refresh(mission)
    assert mission.status == MissionStatus.pending


def test_history_total_leaves_out_snoozed_tasks(db, ws, monkeypatch):
    _u, s = ws
    _pin(monkeypatch, MON)
    _roadmap_with(db, s, ["A", "B"])
    mission = svc.get_or_generate_today(db, s)
    svc.complete_task(db, s, _today_task(db, mission, "A"))
    svc.snooze_task(db, _today_task(db, mission, "B"))
    assert svc._history_counts(db, s)[mission.id] == (1, 1)
    assert _today_task(db, mission, "B").status == MissionTaskStatus.snoozed
