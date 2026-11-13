"""A confirmed consultation lands on the founder workspace's Calendar (gap 29), with the expert as
an external attendee, using the Calendar module's own meeting and invite machinery: the invite
(with its .ics) goes out through the `calendar.invites` job, and a cancellation sends the
matching CANCEL.

Two rules. The meeting link only ever reaches the expert's inbox AFTER they accept (the event is
created by `accept`, never earlier). And a Calendar failure must never undo an accept: the expert
has committed their time, so a failure here is logged loudly and the booking carries on."""

import uuid
from datetime import UTC, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.logger import log
from app.db.models.calendar import CalendarEvent
from app.db.models.marketplace import MarketplaceBooking, MarketplaceProvider
from app.db.models.user import User
from app.schemas.calendar import AttendeeIn, MeetingCreate
from app.services.calendar import meetings
from app.services.calendar import service as calendar_service
from app.services.calendar.ics import build_ics


def source_ref(booking_id: uuid.UUID) -> str:
    return f"marketplace:{booking_id}"


def _meeting_payload(b: MarketplaceBooking, provider: MarketplaceProvider) -> MeetingCreate:
    assert b.starts_at is not None and b.ends_at is not None  # nosec B101 - consultations only
    zone = ZoneInfo(provider.timezone)
    start, end = b.starts_at.astimezone(zone), b.ends_at.astimezone(zone)
    # A slot that runs past local midnight is clipped to 23:59 (the Calendar module needs
    # end >= start on the same wall clock); real slots are inside one working day.
    end_time = end.time().replace(tzinfo=None) if end.date() == start.date() else time(23, 59)
    name = provider.display_name or "the expert"
    return MeetingCreate(
        title=f"{b.title_snap} with {name}"[:200],
        event_date=start.date(),
        start_time=start.time().replace(tzinfo=None),
        end_time=end_time,
        timezone=provider.timezone,
        video_url=b.meeting_url_snap,
        description="Booked on the Cofoundaz Marketplace.",
        attendees=[AttendeeIn(email=provider.email, name=provider.display_name)],
        assistant_enabled=False,  # no AI note-taker on a call with an outside expert
        visibility="team",
    )


def create_for_booking(db: Session, b: MarketplaceBooking, provider: MarketplaceProvider) -> None:
    """Idempotent: a booking that already has its event gets no second one."""
    if b.calendar_event_id is not None or b.starts_at is None or b.ends_at is None:
        return
    booker = db.get(User, b.booked_by)
    if booker is None:
        return
    try:
        with db.begin_nested():
            ev = meetings.create_meeting(
                db,
                startup_id=b.startup_id,
                user=booker,
                data=_meeting_payload(b, provider),
            )
            ev.source_ref = source_ref(b.id)
            b.calendar_event_id = ev.id
    except Exception as exc:  # noqa: BLE001 - never undo an accept; shout instead
        log.error(f"marketplace: calendar event for booking {b.id} failed: {exc}")


def cancel(db: Session, b: MarketplaceBooking) -> bool:
    """Remove the meeting and cancel the expert's invite (ICS CANCEL). True if one was removed."""
    event_id = b.calendar_event_id
    if event_id is None:
        return False
    booker = db.get(User, b.booked_by)
    b.calendar_event_id = None
    try:
        with db.begin_nested():
            calendar_service.delete_event(
                db, startup_id=b.startup_id, event_id=event_id, viewer=booker
            )
    except NotFound:
        return False  # the founder already deleted it from their Calendar
    except Exception as exc:  # noqa: BLE001 - the booking's own change must still commit
        log.error(f"marketplace: calendar cancel for booking {b.id} failed: {exc}")
        return False
    return True


def ics_for(
    db: Session, b: MarketplaceBooking, *, viewer_email: str, viewer_name: str | None
) -> bytes:
    """The .ics behind "Add to calendar": the same event, addressed to whoever is asking."""
    if b.calendar_event_id is None:
        raise NotFound()
    ev = db.query(CalendarEvent).filter_by(id=b.calendar_event_id, startup_id=b.startup_id).first()
    if ev is None:
        raise NotFound()
    booker = db.get(User, b.booked_by)
    org_email = str(booker.email) if booker else "no-reply@cofoundaz.com"
    org_name = (booker.profile.full_name if booker and booker.profile else None) or org_email
    return build_ics(
        SimpleNamespace(**{c: getattr(ev, c) for c in _EVENT_FIELDS}),
        method="REQUEST",
        sequence=0,
        organizer_email=org_email,
        organizer_name=org_name,
        attendee_email=viewer_email,
        attendee_name=viewer_name,
        now=datetime.now(UTC),
    )


_EVENT_FIELDS = (
    "id",
    "title",
    "event_date",
    "end_date",
    "start_time",
    "end_time",
    "timezone",
    "location",
    "video_url",
    "description",
)
