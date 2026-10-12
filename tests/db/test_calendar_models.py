from datetime import date, time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.calendar import CalendarEvent
from app.db.models.enums import CalendarEventType
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _event(db, startup, **kw):
    kw.setdefault("title", "Partner call")
    kw.setdefault("type", CalendarEventType.meeting)
    kw.setdefault("event_date", date(2026, 7, 6))
    row = CalendarEvent(startup_id=startup.id, **kw)
    db.add(row)
    db.flush()
    return row


def test_event_type_values():
    assert {t.value for t in CalendarEventType} == {
        "milestone",
        "meeting",
        "deadline",
        "task",
    }


def test_defaults_all_day_false_and_times_null(db):
    _u, s = _ctx(db)
    row = _event(db, s)
    db.refresh(row)
    # An event with no time is *untimed*, which is not the same as all-day (spec D12).
    assert row.all_day is False
    assert row.start_time is None and row.end_time is None
    assert row.description is None
    assert row.created_by is None


def test_naive_times_round_trip_and_the_enum_stores_its_value(db):
    u, s = _ctx(db)
    row = _event(
        db,
        s,
        type=CalendarEventType.deadline,
        start_time=time(14, 0),
        end_time=time(15, 30),
        description="Bring the deck",
        created_by=u.id,
        all_day=False,
    )
    db.refresh(row)
    # Stored exactly as given: nothing converts, because there is no timezone to convert to (D6).
    assert row.start_time == time(14, 0) and row.end_time == time(15, 30)
    assert row.created_by == u.id
    stored = db.execute(
        text("SELECT type FROM calendar_events WHERE id = :i"), {"i": str(row.id)}
    ).scalar_one()
    assert stored == "deadline"  # the VALUE, not the member name


def test_all_day_event_needs_no_times(db):
    _u, s = _ctx(db)
    row = _event(db, s, type=CalendarEventType.deadline, all_day=True)
    db.refresh(row)
    assert row.all_day is True
    assert row.start_time is None and row.end_time is None


def test_event_dies_with_its_startup(db):
    _u, s = _ctx(db)
    _event(db, s)
    db.delete(s)
    db.flush()
    # Queried, not db.get(): get() answers from the session's identity map and would hand back
    # the cached object even though the row is gone.
    assert db.query(CalendarEvent).filter_by(startup_id=s.id).count() == 0


def test_title_is_required(db):
    _u, s = _ctx(db)
    db.add(
        CalendarEvent(
            startup_id=s.id,
            type=CalendarEventType.task,
            event_date=date(2026, 7, 6),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
