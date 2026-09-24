from datetime import UTC, datetime

from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus
from app.db.models.marketing import ContentCalendarEntry, MarketingChannel
from tests.factories import create_startup, create_user


def test_content_calendar_entry_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    e = ContentCalendarEntry(
        startup_id=s.id,
        created_by=u.id,
        title="Launch tweet",
        channel=ChannelKey.organic_social,
        status=ContentStatus.scheduled,
        body="Big news",
        scheduled_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )
    db.add(e)
    db.flush()
    got = db.query(ContentCalendarEntry).filter_by(startup_id=s.id).one()
    assert got.status == ContentStatus.scheduled
    assert got.channel == ChannelKey.organic_social
    assert got.published_at is None


def test_marketing_channel_unique_per_startup_key(db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    s = create_startup(db, owner=create_user(db))
    db.add(MarketingChannel(startup_id=s.id, key=ChannelKey.email, status=ChannelStatus.active))
    db.flush()
    db.add(MarketingChannel(startup_id=s.id, key=ChannelKey.email, status=ChannelStatus.testing))
    with pytest.raises(IntegrityError):
        db.flush()
