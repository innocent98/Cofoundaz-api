import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus
from app.db.models.marketing import ContentCalendarEntry, MarketingChannel
from app.platform.events import event_bus
from app.schemas.marketing import (
    CalendarEntryCreate,
    CalendarEntryResponse,
    CalendarEntryUpdate,
    ChannelUpdate,
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


def list_channels(db: Session, *, startup_id: uuid.UUID) -> list[MarketingChannel]:
    """Return all 8 ChannelKey rows for the startup, lazy-seeding any that are missing.

    An unguarded check-then-INSERT would let two concurrent callers both insert, and the
    loser's flush would raise IntegrityError against uq_marketing_channel_startup_key. Mirrors
    get_or_create_enrollment: insert inside a SAVEPOINT, and on IntegrityError re-read the
    winner's now-committed rows.
    """
    existing = {c.key: c for c in db.query(MarketingChannel).filter_by(startup_id=startup_id).all()}
    missing = [k for k in ChannelKey if k not in existing]
    if missing:
        try:
            with db.begin_nested():
                for k in missing:
                    db.add(
                        MarketingChannel(
                            startup_id=startup_id, key=k, status=ChannelStatus.not_started
                        )
                    )
                db.flush()
        except IntegrityError:
            pass  # a concurrent first-read seeded them; re-read below
    rows = db.query(MarketingChannel).filter_by(startup_id=startup_id).all()
    order = {k: i for i, k in enumerate(ChannelKey)}
    return sorted(rows, key=lambda c: order[c.key])


def update_channel(
    db: Session, *, startup_id: uuid.UUID, key: ChannelKey, data: ChannelUpdate
) -> MarketingChannel:
    row = db.query(MarketingChannel).filter_by(startup_id=startup_id, key=key).one_or_none()
    if row is None:
        raise NotFound()
    fields = data.model_dump(exclude_unset=True)
    for name, value in fields.items():
        setattr(row, name, value)
    db.flush()
    return row


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def overview(db: Session, *, startup_id: uuid.UUID) -> dict:
    start, end = _week_bounds(datetime.now(UTC))
    scheduled_this_week = (
        db.query(ContentCalendarEntry)
        .filter(
            ContentCalendarEntry.startup_id == startup_id,
            ContentCalendarEntry.status == ContentStatus.scheduled,
            ContentCalendarEntry.scheduled_at >= start,
            ContentCalendarEntry.scheduled_at < end,
        )
        .count()
    )
    active_channels = (
        db.query(MarketingChannel)
        .filter(
            MarketingChannel.startup_id == startup_id,
            MarketingChannel.status == ChannelStatus.active,
        )
        .count()
    )
    return {
        "scheduled_this_week": scheduled_this_week,
        "active_channels": active_channels,
        "active_campaigns": None,  # Slice 2
        "top_channel_by_conversions": None,  # Slice 5
        "ai_content_ideas": None,  # Slice 3
    }
