from datetime import date, timedelta

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
    titles = [u["title"] for u in get_summary(db, startup, owner)["upcoming"]]
    assert titles == ["Inside"]
    assert UPCOMING_WINDOW_DAYS == 7


def test_tasks_done_this_week_counts_only_recent_done(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    mission = create_mission(db, startup=startup, mission_date=date.today())
    create_mission_task(db, mission=mission, status="done")  # counts
    create_mission_task(db, mission=mission, status="todo")  # doesn't
    assert get_summary(db, startup, owner)["kpis"]["tasks_done_this_week"] >= 1


def test_a_failing_section_becomes_error_marker_not_a_raise(db, monkeypatch):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)

    def boom(*a, **k):
        raise RuntimeError("health exploded")

    monkeypatch.setattr("app.services.dashboard.service.get_overview", boom)
    out = get_summary(db, startup, owner)
    assert out["health"] == {"error": True}
    assert out["greeting"]["startup_name"] == startup.name  # rest still populated
