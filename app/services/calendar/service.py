import uuid
from datetime import date

from sqlalchemy import nullsfirst
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.calendar import CalendarEvent
from app.db.models.enums import CalendarEventType
from app.schemas.calendar import EventCreate, EventResponse, EventUpdate, _check_times
from app.services.calendar.errors import _validation


def serialize_event(row: CalendarEvent) -> EventResponse:
    return EventResponse.model_validate(row, from_attributes=True)


def create_event(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: EventCreate
) -> CalendarEvent:
    row = CalendarEvent(startup_id=startup_id, created_by=created_by, **data.model_dump())
    db.add(row)
    db.flush()
    return row


def get_event(db: Session, *, startup_id: uuid.UUID, event_id: uuid.UUID) -> CalendarEvent:
    row = db.query(CalendarEvent).filter_by(id=event_id, startup_id=startup_id).one_or_none()
    if row is None:
        # Uniform 404, never 403: a workspace must not be able to detect another's row ids.
        raise NotFound()
    return row


def list_events(
    db: Session,
    *,
    startup_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    type: CalendarEventType | None = None,
) -> list[CalendarEvent]:
    """The one list that powers Month, Week and Agenda — they differ only in the range asked for.

    `date_from` and `date_to` are independent and optional, mirroring the Finance transaction
    list. A backwards range simply matches nothing.
    """
    q = db.query(CalendarEvent).filter_by(startup_id=startup_id)
    if date_from is not None:
        q = q.filter(CalendarEvent.event_date >= date_from)
    if date_to is not None:
        q = q.filter(CalendarEvent.event_date <= date_to)
    if type is not None:
        q = q.filter(CalendarEvent.type == type)
    return q.order_by(
        CalendarEvent.event_date.asc(),
        # NULLS FIRST is deliberate: Postgres defaults ASC to NULLS LAST, which would push
        # untimed and all-day events to the bottom of their day instead of the top.
        nullsfirst(CalendarEvent.start_time.asc()),
        CalendarEvent.created_at.asc(),
    ).all()


def update_event(
    db: Session, *, startup_id: uuid.UUID, event_id: uuid.UUID, data: EventUpdate
) -> CalendarEvent:
    row = get_event(db, startup_id=startup_id, event_id=event_id)
    changes = data.model_dump(exclude_unset=True)
    # Validate the MERGED row, not the payload: setting only start_time to a value later than the
    # stored end_time is a contradiction the payload alone cannot reveal.
    try:
        _check_times(
            changes.get("all_day", row.all_day),
            changes.get("start_time", row.start_time),
            changes.get("end_time", row.end_time),
        )
    except ValueError as exc:
        raise _validation("start_time", str(exc)) from exc
    for name, value in changes.items():
        setattr(row, name, value)
    db.flush()
    return row


def delete_event(db: Session, *, startup_id: uuid.UUID, event_id: uuid.UUID) -> None:
    row = get_event(db, startup_id=startup_id, event_id=event_id)
    db.delete(row)
    db.flush()
