import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import ChannelKey, ContentStatus
from app.db.models.marketing import ContentCalendarEntry
from app.platform.events import event_bus
from app.schemas.marketing import (
    CalendarEntryCreate,
    CalendarEntryResponse,
    CalendarEntryUpdate,
)


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR", message, 422, field_errors=[{"field": field, "message": message}]
    )


def serialize_entry(e: ContentCalendarEntry) -> CalendarEntryResponse:
    return CalendarEntryResponse.model_validate(e, from_attributes=True)


def create_entry(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID, data: CalendarEntryCreate
) -> ContentCalendarEntry:
    if data.status == ContentStatus.scheduled and data.scheduled_at is None:
        raise _validation("scheduled_at", "A scheduled entry needs a scheduled_at time.")
    entry = ContentCalendarEntry(
        startup_id=startup_id,
        created_by=created_by,
        title=data.title,
        channel=data.channel,
        status=data.status,
        body=data.body,
        media_ref=data.media_ref,
        scheduled_at=data.scheduled_at,
    )
    if data.status == ContentStatus.published:
        entry.published_at = datetime.now(UTC)
    db.add(entry)
    db.flush()
    if entry.status == ContentStatus.published:
        _emit_published(db, entry, actor_id=created_by)
    return entry


def get_entry(db: Session, *, startup_id: uuid.UUID, entry_id: uuid.UUID) -> ContentCalendarEntry:
    entry = (
        db.query(ContentCalendarEntry).filter_by(id=entry_id, startup_id=startup_id).one_or_none()
    )
    if entry is None:
        raise NotFound()
    return entry


def list_entries(
    db: Session,
    *,
    startup_id: uuid.UUID,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    channel: ChannelKey | None = None,
    status: ContentStatus | None = None,
) -> list[ContentCalendarEntry]:
    q = db.query(ContentCalendarEntry).filter(ContentCalendarEntry.startup_id == startup_id)
    if date_from is not None:
        q = q.filter(ContentCalendarEntry.scheduled_at >= date_from)
    if date_to is not None:
        q = q.filter(ContentCalendarEntry.scheduled_at <= date_to)
    if channel is not None:
        q = q.filter(ContentCalendarEntry.channel == channel)
    if status is not None:
        q = q.filter(ContentCalendarEntry.status == status)
    return q.order_by(
        ContentCalendarEntry.scheduled_at.is_(None), ContentCalendarEntry.scheduled_at
    ).all()


def update_entry(
    db: Session,
    *,
    startup_id: uuid.UUID,
    entry_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: CalendarEntryUpdate,
) -> ContentCalendarEntry:
    entry = get_entry(db, startup_id=startup_id, entry_id=entry_id)
    fields = data.model_dump(exclude_unset=True)
    new_status = fields.get("status", entry.status)
    new_scheduled = fields["scheduled_at"] if "scheduled_at" in fields else entry.scheduled_at
    if new_status == ContentStatus.scheduled and new_scheduled is None:
        raise _validation("scheduled_at", "A scheduled entry needs a scheduled_at time.")
    for name, value in fields.items():
        setattr(entry, name, value)
    just_published = new_status == ContentStatus.published and entry.published_at is None
    if just_published:
        entry.published_at = datetime.now(UTC)
    db.flush()
    if just_published:
        _emit_published(db, entry, actor_id=actor_id)
    return entry


def delete_entry(db: Session, *, startup_id: uuid.UUID, entry_id: uuid.UUID) -> None:
    entry = get_entry(db, startup_id=startup_id, entry_id=entry_id)
    db.delete(entry)
    db.flush()


def _emit_published(db: Session, entry: ContentCalendarEntry, *, actor_id: uuid.UUID) -> None:
    event_bus.publish(
        db,
        "marketing.post.published",
        {
            "startup_id": str(entry.startup_id),
            "entry_id": str(entry.id),
            "title": entry.title,
            "channel": entry.channel.value,
            "actor_id": str(actor_id),
            "created_by": str(entry.created_by) if entry.created_by else None,
        },
    )
