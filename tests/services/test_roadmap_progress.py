from app.db.models.enums import RoadmapStatus
from app.services.roadmap.service import recompute_milestone_progress
from tests.factories import (
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def test_progress_is_done_ratio(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    m = create_milestone(db, phase)
    create_task(db, m, status=RoadmapStatus.done)
    create_task(db, m, status=RoadmapStatus.done)
    create_task(db, m, status=RoadmapStatus.todo)

    recompute_milestone_progress(db, m)
    assert m.progress == 67  # round(100 * 2/3)


def test_progress_zero_with_no_tasks(db):
    startup = create_startup(db, owner=create_user(db))
    m = create_milestone(db, create_phase(db, create_roadmap(db, startup)))
    recompute_milestone_progress(db, m)
    assert m.progress == 0


def test_progress_100_when_milestone_done_and_no_tasks(db):
    startup = create_startup(db, owner=create_user(db))
    m = create_milestone(
        db, create_phase(db, create_roadmap(db, startup)), status=RoadmapStatus.done
    )
    recompute_milestone_progress(db, m)
    assert m.progress == 100
