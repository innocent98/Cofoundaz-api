import uuid
from datetime import datetime

from app.db.models.enums import RoadmapStatus
from app.db.models.job import (  # JobStatus from app.db.models.enums if not re-exported
    Job,
    JobStatus,
)
from app.platform import events as events_mod
from app.worker.handlers import scheduled
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)


def _job(job_type, payload):
    return Job(type=job_type, payload=payload, status=JobStatus.running)


def test_mission_generate_publishes_when_a_mission_results(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    create_milestone(db, phase=p)  # gives the roadmap content so a mission can generate
    scheduled.handle_mission_generate(
        db, _job("scheduled.mission.generate", {"startup_id": str(s.id)})
    )
    assert any(e == "mission.ready" for e, _ in published)


def test_overdue_publishes_only_if_still_overdue(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    m = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.todo)
    scheduled.handle_roadmap_overdue(
        db, _job("scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(m.id)})
    )
    assert (
        "roadmap.milestone.overdue",
        {"startup_id": str(s.id), "milestone_id": str(m.id)},
    ) in published


def test_overdue_suppresses_publish_when_milestone_already_done(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    m = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.done)
    scheduled.handle_roadmap_overdue(
        db, _job("scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(m.id)})
    )
    assert not any(e == "roadmap.milestone.overdue" for e, _ in published)


def test_overdue_suppresses_publish_when_milestone_missing(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    missing_id = uuid.uuid4()
    scheduled.handle_roadmap_overdue(
        db,
        _job(
            "scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(missing_id)}
        ),
    )
    assert published == []


def test_quarterly_publishes(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    scheduled.handle_assessment_quarterly(
        db, _job("scheduled.assessment.quarterly", {"startup_id": str(s.id)})
    )
    assert ("assessment.quarterly.due", {"startup_id": str(s.id)}) in published


def test_handlers_registered():
    # The autouse `_fresh_handler_imports` fixture evicts this module from
    # sys.modules before each test, and sibling tests clear JOB_HANDLERS, so
    # re-import here to re-trigger the register_handler(...) import-time
    # side-effect this assertion depends on (independent of collection order).
    import app.worker.handlers.scheduled  # noqa: F401
    from app.worker import runner

    assert "scheduled.mission.generate" in runner.JOB_HANDLERS
    assert "scheduled.roadmap.overdue" in runner.JOB_HANDLERS
    assert "scheduled.assessment.quarterly" in runner.JOB_HANDLERS
