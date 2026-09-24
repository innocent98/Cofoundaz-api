from datetime import UTC, datetime

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import ChannelKey, ContentStatus
from app.platform import events as events_mod
from app.schemas.marketing import CalendarEntryCreate, CalendarEntryUpdate
from app.services.marketing import service as svc
from tests.factories import create_startup, create_user

WHEN = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _mk(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    return u, s


def test_create_and_get_entry(db):
    u, s = _mk(db)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(
            title="Post", channel=ChannelKey.email, status=ContentStatus.draft
        ),
    )
    got = svc.get_entry(db, startup_id=s.id, entry_id=e.id)
    assert got.title == "Post" and got.status == ContentStatus.draft


def test_get_entry_other_tenant_not_found(db):
    u, s = _mk(db)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.draft),
    )
    _, other = _mk(db)
    with pytest.raises(NotFound):
        svc.get_entry(db, startup_id=other.id, entry_id=e.id)


def test_scheduled_requires_scheduled_at(db):
    u, s = _mk(db)
    with pytest.raises(AppError) as ei:
        svc.create_entry(
            db,
            startup_id=s.id,
            created_by=u.id,
            data=CalendarEntryCreate(
                title="P", channel=ChannelKey.email, status=ContentStatus.scheduled
            ),
        )
    assert ei.value.http_status == 422


def test_update_to_scheduled_without_time_is_422(db):
    u, s = _mk(db)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.draft),
    )
    with pytest.raises(AppError) as ei:
        svc.update_entry(
            db,
            startup_id=s.id,
            entry_id=e.id,
            actor_id=u.id,
            data=CalendarEntryUpdate(status=ContentStatus.scheduled),
        )
    assert ei.value.http_status == 422


def test_update_to_scheduled_with_time_succeeds(db):
    u, s = _mk(db)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.draft),
    )
    updated = svc.update_entry(
        db,
        startup_id=s.id,
        entry_id=e.id,
        actor_id=u.id,
        data=CalendarEntryUpdate(status=ContentStatus.scheduled, scheduled_at=WHEN),
    )
    assert updated.status == ContentStatus.scheduled and updated.scheduled_at == WHEN


def test_list_filters_by_date_range_and_channel(db):
    u, s = _mk(db)
    svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(
            title="A", channel=ChannelKey.email, status=ContentStatus.scheduled, scheduled_at=WHEN
        ),
    )
    svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(
            title="B",
            channel=ChannelKey.search,
            status=ContentStatus.scheduled,
            scheduled_at=datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        ),
    )
    got = svc.list_entries(
        db,
        startup_id=s.id,
        date_from=datetime(2026, 10, 1, tzinfo=UTC),
        date_to=datetime(2026, 10, 31, tzinfo=UTC),
    )
    assert [e.title for e in got] == ["A"]
    only_search = svc.list_entries(db, startup_id=s.id, channel=ChannelKey.search)
    assert [e.title for e in only_search] == ["B"]


def test_publish_sets_published_at_and_emits_event_once(db, monkeypatch):
    u, s = _mk(db)
    published: list = []
    real_publish = events_mod.event_bus.publish

    def spy(dbx, event, payload):
        if event == "marketing.post.published":
            published.append(payload)
        return real_publish(dbx, event, payload)

    monkeypatch.setattr(events_mod.event_bus, "publish", spy)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(
            title="Launch",
            channel=ChannelKey.email,
            status=ContentStatus.scheduled,
            scheduled_at=WHEN,
        ),
    )
    svc.update_entry(
        db,
        startup_id=s.id,
        entry_id=e.id,
        actor_id=u.id,
        data=CalendarEntryUpdate(status=ContentStatus.published),
    )
    assert e.published_at is not None and len(published) == 1
    assert published[0]["title"] == "Launch" and published[0]["actor_id"] == str(u.id)
    first_published_at = e.published_at
    # re-publish is a no-op: no second event, published_at unchanged
    svc.update_entry(
        db,
        startup_id=s.id,
        entry_id=e.id,
        actor_id=u.id,
        data=CalendarEntryUpdate(status=ContentStatus.published),
    )
    assert len(published) == 1 and e.published_at == first_published_at


def test_delete_entry(db):
    u, s = _mk(db)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.draft),
    )
    svc.delete_entry(db, startup_id=s.id, entry_id=e.id)
    with pytest.raises(NotFound):
        svc.get_entry(db, startup_id=s.id, entry_id=e.id)
