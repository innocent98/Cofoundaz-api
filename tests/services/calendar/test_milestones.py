from datetime import date

import pytest

from app.db.models.enums import RoadmapStatus
from app.services.calendar.milestones import calendar_status, list_milestones
from tests.factories import (
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_user,
)

TODAY = date(2026, 7, 15)
PAST = date(2026, 7, 1)
FUTURE = date(2026, 8, 1)


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _phase(db, startup):
    roadmap = create_roadmap(db, startup)
    return create_phase(db, roadmap, name="Validation")


# --- the derivation, as a pure function -------------------------------------------------------


@pytest.mark.parametrize(
    "status,due_on,expected",
    [
        # Finished beats everything: a completed milestone is never "at risk", however old.
        (RoadmapStatus.done, PAST, "complete"),
        (RoadmapStatus.done, FUTURE, "complete"),
        (RoadmapStatus.done, None, "complete"),
        # Overdue beats in-progress.
        (RoadmapStatus.in_progress, PAST, "at_risk"),
        (RoadmapStatus.todo, PAST, "at_risk"),
        # Not overdue: the raw status decides.
        (RoadmapStatus.in_progress, FUTURE, "on_track"),
        (RoadmapStatus.todo, FUTURE, "not_started"),
        # No due date can never be at risk.
        (RoadmapStatus.in_progress, None, "on_track"),
        (RoadmapStatus.todo, None, "not_started"),
        # Due today is not yet overdue.
        (RoadmapStatus.todo, TODAY, "not_started"),
    ],
)
def test_calendar_status_covers_every_branch(status, due_on, expected):
    assert calendar_status(status, due_on, TODAY) == expected


# --- the projection ---------------------------------------------------------------------------


def test_only_this_workspaces_milestones_are_returned(db):
    _u, mine = _ctx(db)
    _other_u, theirs = _ctx(db)
    create_milestone(db, _phase(db, mine), title="Mine", due_on=FUTURE)
    create_milestone(db, _phase(db, theirs), title="Theirs", due_on=FUTURE)
    db.flush()
    # RoadmapMilestone has no startup_id: the three-table join IS the tenancy boundary.
    rows = list_milestones(db, startup_id=mine.id, today=TODAY)
    assert [r.title for r in rows] == ["Mine"]


def test_a_workspace_with_no_roadmap_returns_an_empty_list(db):
    _u, s = _ctx(db)
    assert list_milestones(db, startup_id=s.id, today=TODAY) == []


def test_dated_milestones_come_first_undated_last(db):
    _u, s = _ctx(db)
    phase = _phase(db, s)
    create_milestone(db, phase, title="No date", due_on=None)
    create_milestone(db, phase, title="Later", due_on=FUTURE)
    create_milestone(db, phase, title="Sooner", due_on=PAST)
    db.flush()
    rows = list_milestones(db, startup_id=s.id, today=TODAY)
    assert [r.title for r in rows] == ["Sooner", "Later", "No date"]


def test_completed_milestones_are_included(db):
    _u, s = _ctx(db)
    phase = _phase(db, s)
    create_milestone(
        db, phase, title="Incorporated", status=RoadmapStatus.done, progress=100, due_on=PAST
    )
    db.flush()
    rows = list_milestones(db, startup_id=s.id, today=TODAY)
    assert [r.title for r in rows] == ["Incorporated"]
    assert rows[0].calendar_status == "complete"


def test_both_the_raw_status_and_the_derived_one_are_returned(db):
    _u, s = _ctx(db)
    phase = _phase(db, s)
    create_milestone(
        db,
        phase,
        title="Pricing test",
        status=RoadmapStatus.in_progress,
        progress=35,
        due_on=PAST,
    )
    db.flush()
    row = list_milestones(db, startup_id=s.id, today=TODAY)[0]
    assert row.status == RoadmapStatus.in_progress  # raw, straight from Roadmap
    assert row.calendar_status == "at_risk"  # derived for the FE badge
    assert row.progress == 35
    assert row.due_on == PAST


def test_today_defaults_to_the_utc_date_when_not_passed(db):
    _u, s = _ctx(db)
    phase = _phase(db, s)
    create_milestone(db, phase, title="Long past", status=RoadmapStatus.todo, due_on=PAST)
    db.flush()
    # PAST is 2026-07-01, comfortably behind any real clock this code will run on.
    row = list_milestones(db, startup_id=s.id)[0]
    assert row.calendar_status == "at_risk"
