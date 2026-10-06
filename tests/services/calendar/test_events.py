from datetime import date, time

import pytest
from pydantic import ValidationError

from app.core.errors import AppError, NotFound
from app.db.models.enums import CalendarEventType
from app.schemas.calendar import EventCreate, EventUpdate, _check_times
from app.services.calendar.service import (
    create_event,
    delete_event,
    get_event,
    list_events,
    serialize_event,
    update_event,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, u, s, **kw):
    kw.setdefault("title", "Partner call")
    kw.setdefault("type", CalendarEventType.meeting)
    kw.setdefault("event_date", date(2026, 7, 6))
    return create_event(db, startup_id=s.id, created_by=u.id, data=EventCreate(**kw))


# --- the time rules, as a pure function -------------------------------------------------------


def test_check_times_accepts_the_valid_shapes():
    _check_times(False, None, None)  # untimed
    _check_times(True, None, None)  # all-day
    _check_times(False, time(9, 0), None)  # start only
    _check_times(False, time(9, 0), time(10, 0))  # a span
    _check_times(False, time(9, 0), time(9, 0))  # zero-length marker is allowed


@pytest.mark.parametrize(
    "all_day,start,end",
    [
        (True, time(9, 0), None),  # all-day may not carry a time (D9)
        (True, None, time(9, 0)),
        (False, None, time(9, 0)),  # an end with no beginning
        (False, time(10, 0), time(9, 0)),  # end before start
    ],
)
def test_check_times_rejects_the_contradictions(all_day, start, end):
    with pytest.raises(ValueError):
        _check_times(all_day, start, end)


def test_create_schema_enforces_the_time_rules():
    with pytest.raises(ValidationError):
        EventCreate(
            title="All day with a time",
            type=CalendarEventType.deadline,
            event_date=date(2026, 7, 6),
            all_day=True,
            start_time=time(9, 0),
        )


# --- create -----------------------------------------------------------------------------------


def test_create_with_only_the_three_fe_fields(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    # The create modal collects title, type and date only — nothing else may be required.
    assert row.all_day is False
    assert row.start_time is None and row.end_time is None
    assert row.description is None
    assert row.created_by == u.id
    assert row.startup_id == s.id


def test_create_keeps_times_exactly_as_sent(db):
    u, s = _ctx(db)
    row = _new(db, u, s, start_time=time(14, 0), end_time=time(15, 30))
    assert row.start_time == time(14, 0) and row.end_time == time(15, 30)


def test_serialize_shape(db):
    u, s = _ctx(db)
    row = _new(db, u, s, description="Bring the deck")
    out = serialize_event(row)
    assert out.id == row.id and out.title == "Partner call"
    assert out.type == CalendarEventType.meeting
    assert out.event_date == date(2026, 7, 6)
    assert out.all_day is False and out.description == "Bring the deck"


# --- list -------------------------------------------------------------------------------------


def test_range_filter_includes_both_boundaries(db):
    u, s = _ctx(db)
    _new(db, u, s, title="Before", event_date=date(2026, 6, 30))
    _new(db, u, s, title="On from", event_date=date(2026, 7, 1))
    _new(db, u, s, title="Inside", event_date=date(2026, 7, 15))
    _new(db, u, s, title="On to", event_date=date(2026, 7, 31))
    _new(db, u, s, title="After", event_date=date(2026, 8, 1))
    rows = list_events(db, startup_id=s.id, date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
    assert [r.title for r in rows] == ["On from", "Inside", "On to"]


def test_untimed_and_all_day_sort_before_timed_within_a_day(db):
    u, s = _ctx(db)
    day = date(2026, 7, 6)
    _new(db, u, s, title="Afternoon", event_date=day, start_time=time(14, 0))
    _new(db, u, s, title="Morning", event_date=day, start_time=time(9, 0))
    _new(db, u, s, title="Whole day", event_date=day, all_day=True)
    rows = list_events(db, startup_id=s.id)
    # NULLS FIRST is deliberate: Postgres would otherwise put the untimed event last.
    assert [r.title for r in rows] == ["Whole day", "Morning", "Afternoon"]


def test_type_filter_and_no_filter(db):
    u, s = _ctx(db)
    _new(db, u, s, title="A meeting")
    _new(db, u, s, title="A deadline", type=CalendarEventType.deadline)
    _new(db, u, s, title="A task", type=CalendarEventType.task)
    assert len(list_events(db, startup_id=s.id)) == 3
    only = list_events(db, startup_id=s.id, type=CalendarEventType.deadline)
    assert [r.title for r in only] == ["A deadline"]


def test_backwards_range_returns_nothing_rather_than_erroring(db):
    u, s = _ctx(db)
    _new(db, u, s)
    rows = list_events(db, startup_id=s.id, date_from=date(2026, 8, 1), date_to=date(2026, 7, 1))
    assert rows == []


def test_list_only_sees_its_own_workspace(db):
    u, s = _ctx(db)
    other_u, other_s = _ctx(db)
    _new(db, u, s, title="Mine")
    _new(db, other_u, other_s, title="Theirs")
    assert [r.title for r in list_events(db, startup_id=s.id)] == ["Mine"]


# --- get / update / delete --------------------------------------------------------------------


def test_get_another_workspaces_event_is_a_404(db):
    u, s = _ctx(db)
    other_u, other_s = _ctx(db)
    theirs = _new(db, other_u, other_s, title="Theirs")
    with pytest.raises(NotFound):
        get_event(db, startup_id=s.id, event_id=theirs.id)


def test_update_changes_only_what_was_sent(db):
    u, s = _ctx(db)
    row = _new(db, u, s, start_time=time(9, 0), end_time=time(10, 0))
    updated = update_event(db, startup_id=s.id, event_id=row.id, data=EventUpdate(title="Renamed"))
    assert updated.title == "Renamed"
    assert updated.start_time == time(9, 0) and updated.end_time == time(10, 0)


def test_update_clears_a_time_with_an_explicit_null(db):
    u, s = _ctx(db)
    row = _new(db, u, s, start_time=time(9, 0), end_time=time(10, 0))
    updated = update_event(
        db,
        startup_id=s.id,
        event_id=row.id,
        data=EventUpdate(start_time=None, end_time=None),
    )
    assert updated.start_time is None and updated.end_time is None


def test_update_validates_the_merged_row_not_just_the_payload(db):
    u, s = _ctx(db)
    row = _new(db, u, s, start_time=time(9, 0), end_time=time(10, 0))
    # On its own this payload is unremarkable; against the stored end_time it is nonsense.
    with pytest.raises(AppError) as exc:
        update_event(db, startup_id=s.id, event_id=row.id, data=EventUpdate(start_time=time(11, 0)))
    assert exc.value.http_status == 422


def test_update_rejects_an_explicit_null_on_a_required_field():
    with pytest.raises(ValidationError):
        EventUpdate(title=None)


def test_delete_then_delete_again(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    delete_event(db, startup_id=s.id, event_id=row.id)
    assert list_events(db, startup_id=s.id) == []
    with pytest.raises(NotFound):
        delete_event(db, startup_id=s.id, event_id=row.id)
