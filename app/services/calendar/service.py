import uuid
from datetime import date

from sqlalchemy import nullsfirst, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import NotFound
from app.db.models.calendar import (
    CalendarAttendee,
    CalendarConnection,
    CalendarEvent,
    CalendarEventLink,
    CalendarMeeting,
)
from app.db.models.enums import CalendarEventType
from app.db.models.user import User
from app.platform.jobs import job_dispatcher
from app.schemas.calendar import EventCreate, EventResponse, EventUpdate, _check_times
from app.services.calendar import meetings
from app.services.calendar.errors import _validation


def serialize_event(row: CalendarEvent) -> EventResponse:
    return EventResponse.model_validate(row, from_attributes=True)


def _ensure_meeting_row(db: Session, row: CalendarEvent) -> None:
    if row.type == CalendarEventType.meeting and (
        db.query(CalendarMeeting.id).filter_by(event_id=row.id).first() is None
    ):
        db.add(CalendarMeeting(event_id=row.id))


def create_event(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: EventCreate
) -> CalendarEvent:
    row = CalendarEvent(startup_id=startup_id, created_by=created_by, **data.model_dump())
    db.add(row)
    db.flush()
    _ensure_meeting_row(db, row)
    db.flush()
    return row


def _visible(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """A private event is visible to its creator and its invitees only."""
    if viewer_id is None:
        return CalendarEvent.visibility == "team"
    invited = select(CalendarAttendee.event_id).where(CalendarAttendee.user_id == viewer_id)
    return or_(
        CalendarEvent.visibility == "team",
        CalendarEvent.created_by == viewer_id,
        CalendarEvent.id.in_(invited),
    )


def get_event(
    db: Session, *, startup_id: uuid.UUID, event_id: uuid.UUID, viewer_id: uuid.UUID | None = None
) -> CalendarEvent:
    q = db.query(CalendarEvent).filter_by(id=event_id, startup_id=startup_id)
    if viewer_id is not None:
        q = q.filter(_visible(viewer_id))
    row = q.one_or_none()
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
    viewer_id: uuid.UUID | None = None,
) -> list[CalendarEvent]:
    """The one list that powers Month, Week and Agenda — they differ only in the range asked for.

    `date_from` and `date_to` are independent and optional, mirroring the Finance transaction
    list. A backwards range simply matches nothing.
    """
    q = db.query(CalendarEvent).filter_by(startup_id=startup_id)
    if viewer_id is not None:
        q = q.filter(_visible(viewer_id))
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
    db: Session,
    *,
    startup_id: uuid.UUID,
    event_id: uuid.UUID,
    data: EventUpdate,
    viewer_id: uuid.UUID | None = None,
) -> CalendarEvent:
    row = get_event(db, startup_id=startup_id, event_id=event_id, viewer_id=viewer_id)
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
    end_date = changes.get("end_date", row.end_date)
    if end_date is not None and end_date < changes.get("event_date", row.event_date):
        raise _validation("end_date", "end_date may not be earlier than event_date")
    for name, value in changes.items():
        setattr(row, name, value)
    _ensure_meeting_row(db, row)
    db.flush()
    return row


def delete_event(
    db: Session, *, startup_id: uuid.UUID, event_id: uuid.UUID, viewer: User | None = None
) -> None:
    row = get_event(
        db, startup_id=startup_id, event_id=event_id, viewer_id=viewer.id if viewer else None
    )
    # The Google copies must be removed too, and the link rows die with the event: hand the
    # external ids to a job first.
    by_conn: dict[uuid.UUID, list[str]] = {}
    for link in db.query(CalendarEventLink).filter_by(event_id=row.id):
        by_conn.setdefault(link.connection_id, []).append(link.external_id)
    for conn_id, ids in by_conn.items():
        job_dispatcher.enqueue(
            db, JOB_PUSH_DELETE, {"connection_id": str(conn_id), "external_ids": ids}, startup_id
        )
    if row.type == CalendarEventType.meeting and viewer is not None:
        meetings.delete_meeting_snapshot(db, row, viewer)
    db.delete(row)
    db.flush()


JOB_PUSH_DELETE = "calendar.sync.push_delete"


def queue_pushes(db: Session, startup_id: uuid.UUID) -> None:
    """After any event write: ask every active push-capable connection of the workspace to sync."""
    for conn_id in (
        db.query(CalendarConnection.id)
        .filter(
            CalendarConnection.startup_id == startup_id,
            CalendarConnection.status == "active",
            CalendarConnection.direction != "pull_only",
        )
        .all()
    ):
        job_dispatcher.enqueue(db, "calendar.sync", {"connection_id": str(conn_id[0])}, startup_id)
