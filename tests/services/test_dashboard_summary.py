import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db.models.enums import MissionTaskStatus, RoadmapStatus, TaskEffort
from app.db.models.mission import MissionTask
from app.services.dashboard.service import UPCOMING_WINDOW_DAYS, get_summary
from tests.factories import (
    create_milestone,
    create_mission,
    create_mission_task,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)


def test_summary_has_all_sections_and_greeting(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    out = get_summary(db, startup, owner)
    assert set(out) == {
        "greeting",
        "health",
        "mission",
        "upcoming",
        "kpis",
        "calibration",
        "briefing",
        "risks",
        "opportunities",
    }
    assert out["greeting"]["startup_name"] == startup.name
    assert out["briefing"]["status"] == "empty"
    assert out["calibration"]["assessment_complete"] is False


def test_upcoming_window_and_done_exclusion(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    roadmap = create_roadmap(db, startup=startup)
    phase = create_phase(db, roadmap=roadmap)
    today = date.today()
    create_milestone(db, phase=phase, title="Inside", due_on=today + timedelta(days=3))
    create_milestone(db, phase=phase, title="TooFar", due_on=today + timedelta(days=30))
    create_milestone(db, phase=phase, title="Past", due_on=today - timedelta(days=1))
    create_milestone(
        db,
        phase=phase,
        title="DoneInWindow",
        due_on=today + timedelta(days=2),
        status=RoadmapStatus.done,
    )
    titles = [u["title"] for u in get_summary(db, startup, owner)["upcoming"]]
    assert titles == ["Inside"]
    assert UPCOMING_WINDOW_DAYS == 7


def test_tasks_done_this_week_counts_only_recent_done(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    now = datetime.now(UTC)

    # Mission dated well over a week ago, but the task was completed today — must still
    # count. This is the case a `mission_date`-keyed implementation gets wrong (excludes
    # it, since the *mission* is stale even though the *completion* is recent) — verified
    # by re-running this exact test against that implementation before this fix landed.
    old_mission = create_mission(
        db, startup=startup, mission_date=date.today() - timedelta(days=10)
    )
    create_mission_task(db, mission=old_mission, status="done", completed_at=now)

    # Same-day mission, same-day completion — must count (baseline agreement case).
    today_mission = create_mission(db, startup=startup, mission_date=date.today())
    create_mission_task(db, mission=today_mission, status="done", completed_at=now)
    create_mission_task(db, mission=today_mission, status="todo")  # not done — must not count

    # Completed 8 days ago, on an equally stale mission — outside the 7-day completion
    # window, must not count. (Mission is also stale here so this case doesn't itself
    # discriminate between the two implementations — `old_mission` above is what does that;
    # this case only guards against a regression to "count everything done".)
    stale_mission = create_mission(
        db, startup=startup, mission_date=date.today() - timedelta(days=9)
    )
    create_mission_task(
        db, mission=stale_mission, status="done", completed_at=now - timedelta(days=8)
    )

    assert get_summary(db, startup, owner)["kpis"]["tasks_done_this_week"] == 2


def test_a_failing_section_becomes_error_marker_not_a_raise(db, monkeypatch):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)

    def boom(*a, **k):
        raise RuntimeError("health exploded")

    monkeypatch.setattr("app.services.dashboard.service.get_overview", boom)
    out = get_summary(db, startup, owner)
    assert out["health"] == {"error": True}
    assert out["greeting"]["startup_name"] == startup.name  # rest still populated


def test_a_db_error_in_one_section_is_isolated_not_a_cascade(db, monkeypatch):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)

    def bad_flush(db_, startup_id):
        # A real IntegrityError, not a Python-level exception: mission_tasks.mission_id
        # is a NOT NULL FK to missions.id, so a random UUID that matches no row fails
        # the constraint on flush -- this is what marks the whole shared Session
        # rollback-only if the section isn't savepoint-isolated.
        db_.add(
            MissionTask(
                mission_id=uuid.uuid4(),
                title="boom",
                effort=TaskEffort.medium,
                status=MissionTaskStatus.todo,
                order=0,
            )
        )
        db_.flush()
        return None  # pragma: no cover - flush always raises before this line

    monkeypatch.setattr("app.services.dashboard.service.latest_completed_result", bad_flush)
    out = get_summary(db, startup, owner)

    assert out["calibration"] == {"error": True}
    # Every other section still populated -- the DB error didn't cascade.
    assert out["greeting"]["startup_name"] == startup.name
    assert out["upcoming"] == []
    assert out["kpis"]["tasks_done_this_week"] == 0

    # The outer transaction must still be usable: no PendingRollbackError from the
    # poisoned flush leaking past its savepoint. A read and a trailing write both work.
    assert db.execute(select(1)).scalar_one() == 1
    create_mission(db, startup=startup, mission_date=date.today() + timedelta(days=90))
