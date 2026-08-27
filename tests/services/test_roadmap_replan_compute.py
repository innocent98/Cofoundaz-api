from datetime import date, timedelta

from app.db.models.enums import RoadmapStatus
from app.services.roadmap.replan import REPLAN_BUFFER_DAYS, compute_replan, detect_drift
from tests.factories import (
    create_dependency,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def _rm(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    return roadmap, phase


def test_slipped_milestone_targets_today_plus_buffer(db):
    roadmap, phase = _rm(db)
    past = date.today() - timedelta(days=10)
    m = create_milestone(db, phase, due_on=past, status=RoadmapStatus.todo)
    db.flush()
    changes = compute_replan(db, roadmap)
    assert len(changes) == 1
    assert changes[0].milestone_id == m.id
    assert changes[0].new_due == date.today() + timedelta(days=REPLAN_BUFFER_DAYS)
    assert "overdue" in changes[0].reason


def test_downstream_dependency_shifts(db):
    roadmap, phase = _rm(db)
    past = date.today() - timedelta(days=5)
    up = create_milestone(db, phase, title="Up", due_on=past, status=RoadmapStatus.todo)
    down = create_milestone(
        db, phase, title="Down", due_on=date.today() + timedelta(days=30), status=RoadmapStatus.todo
    )
    tu = create_task(db, up)
    td = create_task(db, down)
    create_dependency(db, td, tu)  # td depends on tu  => up precedes down
    db.flush()
    changes = {c.milestone_id: c for c in compute_replan(db, roadmap)}
    assert up.id in changes and down.id in changes
    shift = (changes[up.id].new_due - up.due_on).days
    assert changes[down.id].new_due == down.due_on + timedelta(days=shift)  # same delta
    assert "dependency" in changes[down.id].reason


def test_diamond_shifts_by_max_not_sum(db):
    roadmap, phase = _rm(db)
    b = create_milestone(
        db, phase, title="B", due_on=date.today() - timedelta(days=3), status=RoadmapStatus.todo
    )  # small slip
    c = create_milestone(
        db, phase, title="C", due_on=date.today() - timedelta(days=20), status=RoadmapStatus.todo
    )  # big slip
    d = create_milestone(
        db, phase, title="D", due_on=date.today() + timedelta(days=60), status=RoadmapStatus.todo
    )
    tb, tc, td = create_task(db, b), create_task(db, c), create_task(db, d)
    create_dependency(db, td, tb)  # D depends on B
    create_dependency(db, td, tc)  # D depends on C
    db.flush()
    ch = {x.milestone_id: x for x in compute_replan(db, roadmap)}
    sb = (ch[b.id].new_due - b.due_on).days
    sc = (ch[c.id].new_due - c.due_on).days
    sd = (ch[d.id].new_due - d.due_on).days
    assert sd == max(sb, sc)  # max, never sb + sc


def test_no_drift_is_empty(db):
    roadmap, phase = _rm(db)
    create_milestone(db, phase, due_on=date.today() + timedelta(days=10), status=RoadmapStatus.todo)
    db.flush()
    assert compute_replan(db, roadmap) == []


def test_done_milestone_is_not_drift(db):
    roadmap, phase = _rm(db)
    create_milestone(db, phase, due_on=date.today() - timedelta(days=10), status=RoadmapStatus.done)
    db.flush()
    assert detect_drift(db, roadmap) == []
    assert compute_replan(db, roadmap) == []


def test_reason_names_dependency_when_cascade_dominates_own_slip(db):
    # M is only 1 day overdue (a small self-slip), but it also depends on an
    # upstream milestone U with a much larger slip. The applied new_due must
    # reflect U's bigger shift, and the reason must say so — not just report
    # M's own tiny overdue count as if that were what moved it.
    roadmap, phase = _rm(db)
    u = create_milestone(
        db,
        phase,
        title="Upstream",
        due_on=date.today() - timedelta(days=20),
        status=RoadmapStatus.todo,
    )
    m = create_milestone(
        db,
        phase,
        title="Downstream",
        due_on=date.today() - timedelta(days=1),
        status=RoadmapStatus.todo,
    )
    tu = create_task(db, u)
    tm = create_task(db, m)
    create_dependency(db, tm, tu)  # tm depends on tu => u precedes m
    db.flush()
    changes = {c.milestone_id: c for c in compute_replan(db, roadmap)}
    u_shift = (changes[u.id].new_due - u.due_on).days
    m_shift = (changes[m.id].new_due - m.due_on).days
    assert m_shift == u_shift  # M's shift is driven entirely by U's larger cascade
    assert m_shift > 1  # strictly more than M's own 1-day slip would produce
    assert "Upstream" in changes[m.id].reason
    assert "dependency" in changes[m.id].reason
