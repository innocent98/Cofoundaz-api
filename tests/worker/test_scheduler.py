from datetime import UTC, datetime, timedelta

from app.db.models.enums import AssessmentStatus, AssessmentType, RoadmapStatus
from app.db.models.scheduled_run import ScheduledRun
from app.worker import scheduler
from app.worker.scheduler import _claim
from tests.factories import (
    create_assessment,
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)


def test_claim_is_once_per_period(db):
    assert _claim(db, "mission.generate", "ws1", "2026-09-18") is True
    # same triple → already claimed
    assert _claim(db, "mission.generate", "ws1", "2026-09-18") is False
    # different period → claimable
    assert _claim(db, "mission.generate", "ws1", "2026-09-19") is True
    assert db.query(ScheduledRun).filter_by(task_key="mission.generate", scope_key="ws1").count() == 2


def _ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    return u, s


def test_due_missions_gated_by_hour(db):
    _u, s = _ws(db)
    before = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)  # 05:00 UTC < MISSION_GEN_HOUR 6
    after = datetime(2026, 9, 18, 6, 30, tzinfo=UTC)
    assert scheduler._due_missions(db, before) == []
    due = scheduler._due_missions(db, after)
    assert [d.scope_key for d in due] == [str(s.id)]
    assert due[0].period_key == "2026-09-18" and due[0].task_key == "mission.generate"


def test_due_overdue_milestones(db):
    _u, s = _ws(db)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    overdue = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.todo)
    create_milestone(db, phase=p, due_on=datetime(2026, 12, 1).date(), status=RoadmapStatus.todo)  # future
    create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.done)  # done
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    due = scheduler._due_overdue_milestones(db, now)
    assert [d.scope_key for d in due] == [str(overdue.id)]
    assert due[0].period_key == "once" and due[0].payload["milestone_id"] == str(overdue.id)


def test_due_quarterly(db):
    _u, s = _ws(db)
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    a = create_assessment(
        db, startup=s, type=AssessmentType.initial, status=AssessmentStatus.completed
    )
    a.completed_at = now - timedelta(days=100)
    db.flush()
    due = scheduler._due_quarterly(db, now)
    assert [d.scope_key for d in due] == [str(s.id)]
    assert due[0].period_key == "2026-Q3"

    # a fresh assessment (30 days ago) is NOT due
    _u2, s2 = _ws(db)
    a2 = create_assessment(
        db, startup=s2, type=AssessmentType.initial, status=AssessmentStatus.completed
    )
    a2.completed_at = now - timedelta(days=30)
    db.flush()
    assert str(s2.id) not in {d.scope_key for d in scheduler._due_quarterly(db, now)}
