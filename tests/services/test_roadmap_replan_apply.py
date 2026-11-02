import uuid
from datetime import date, timedelta

from app.db.models.enums import RoadmapStatus
from app.db.models.job import Job
from app.db.models.roadmap import RoadmapReplan
from app.services.roadmap.replan import apply_replan, compute_replan
from tests.factories import (
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)


def _slipped(db):
    user = create_user(db)
    startup = create_startup(db, owner=user)
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    m = create_milestone(
        db, phase, due_on=date.today() - timedelta(days=10), status=RoadmapStatus.todo
    )
    db.flush()
    return roadmap, user, m


def test_apply_commits_marker_history_event(db, monkeypatch):
    events = []
    from app.platform import events as ev

    monkeypatch.setattr(ev.event_bus, "publish", lambda db, e, p: events.append((e, p)))

    roadmap, user, m = _slipped(db)
    changes = compute_replan(db, roadmap)
    result = apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()

    assert result["applied"] == [str(m.id)] and result["skipped"] == []
    db.refresh(m)
    assert m.due_on == date.today() + timedelta(days=7)
    assert m.last_replanned_at is not None and m.last_replan_reason
    row = db.query(RoadmapReplan).filter_by(roadmap_id=roadmap.id).one()
    assert row.change_count == 1 and row.summary == "Re-planned 1 milestone"
    assert row.changes[0]["milestone_id"] == str(m.id)
    assert any(e == "roadmap.replanned" for e, _ in events)


def test_apply_skips_stale_change_id(db):
    import uuid

    roadmap, user, _m = _slipped(db)
    result = apply_replan(db, roadmap, user, [uuid.uuid4()])  # not in the proposal
    db.flush()
    assert result["applied"] == [] and len(result["skipped"]) == 1
    assert result["replan_id"] is None
    assert db.query(RoadmapReplan).count() == 0  # no row, no event


def test_reapply_is_idempotent(db):
    roadmap, user, m = _slipped(db)
    changes = compute_replan(db, roadmap)
    apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()
    # after apply the milestone is no longer overdue -> nothing to apply
    again = apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()
    assert again["applied"] == []
    assert db.query(RoadmapReplan).count() == 1


def test_apply_writes_rationale_and_enqueues(db):
    roadmap, user, m = _slipped(db)
    changes = compute_replan(db, roadmap)
    result = apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()

    assert result["applied"] == [str(m.id)]
    assert result["rationale"]  # non-null templated fallback in the response
    replan = db.query(RoadmapReplan).filter_by(roadmap_id=roadmap.id).one()
    assert replan.rationale  # persisted, non-null
    assert db.query(Job).filter(Job.type == "ai.roadmap.rationale").count() == 1


def test_apply_empty_enqueues_nothing(db):
    roadmap, user, _m = _slipped(db)
    result = apply_replan(db, roadmap, user, [uuid.uuid4()])  # not in the proposal
    db.flush()
    assert result["applied"] == []
    assert result["replan_id"] is None
    assert db.query(Job).filter(Job.type == "ai.roadmap.rationale").count() == 0
