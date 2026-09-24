from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import NotFound
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus
from app.schemas.marketing import CalendarEntryCreate, ChannelUpdate
from app.services.marketing import service as svc
from tests.factories import create_startup, create_user


def test_list_channels_seeds_all_eight_idempotently(db):
    s = create_startup(db, owner=create_user(db))
    first = svc.list_channels(db, startup_id=s.id)
    assert len(first) == 8
    assert {c.key for c in first} == set(ChannelKey)
    again = svc.list_channels(db, startup_id=s.id)  # second call: no duplicates
    assert len(again) == 8


def test_update_channel(db):
    s = create_startup(db, owner=create_user(db))
    svc.list_channels(db, startup_id=s.id)  # seed
    row = svc.update_channel(
        db,
        startup_id=s.id,
        key=ChannelKey.email,
        data=ChannelUpdate(status=ChannelStatus.active, notes="warming up"),
    )
    assert row.status == ChannelStatus.active and row.notes == "warming up"


def test_update_missing_channel_not_found(db):
    s = create_startup(db, owner=create_user(db))  # not seeded yet
    with pytest.raises(NotFound):
        svc.update_channel(
            db,
            startup_id=s.id,
            key=ChannelKey.email,
            data=ChannelUpdate(status=ChannelStatus.active),
        )


def test_overview_counts(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    svc.list_channels(db, startup_id=s.id)
    svc.update_channel(
        db, startup_id=s.id, key=ChannelKey.email, data=ChannelUpdate(status=ChannelStatus.active)
    )
    svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(
            title="This week",
            channel=ChannelKey.email,
            status=ContentStatus.scheduled,
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        ),
    )
    ov = svc.overview(db, startup_id=s.id)
    assert ov["active_channels"] == 1
    assert ov["scheduled_this_week"] >= 1
    assert ov["active_campaigns"] is None
    assert ov["top_channel_by_conversions"] is None
    assert ov["ai_content_ideas"] is None
