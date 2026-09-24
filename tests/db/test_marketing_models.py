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


def test_campaign_and_segment_persist(db):
    from app.db.models.enums import CampaignObjective, CampaignStatus
    from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
    from tests.factories import create_startup, create_user

    s = create_startup(db, owner=create_user(db))
    seg = AudienceSegment(
        startup_id=s.id, name="SMB founders", definition={"rules": []}, est_size=1200
    )
    db.add(seg)
    camp = Campaign(
        startup_id=s.id,
        name="Q4 launch",
        objective=CampaignObjective.launch,
        budget=50000,
        channel_mix={"email": 60, "search": 40},
        status=CampaignStatus.draft,
    )
    db.add(camp)
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    db.flush()
    got = db.query(Campaign).filter_by(startup_id=s.id).one()
    assert got.objective == CampaignObjective.launch
    assert got.budget == 50000 and got.channel_mix == {"email": 60, "search": 40}
    assert got.metrics == {} and got.status == CampaignStatus.draft


def test_campaign_segment_unique(db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.db.models.enums import CampaignObjective
    from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
    from tests.factories import create_startup, create_user

    s = create_startup(db, owner=create_user(db))
    seg = AudienceSegment(startup_id=s.id, name="A", definition={})
    camp = Campaign(startup_id=s.id, name="C", objective=CampaignObjective.leads)
    db.add_all([seg, camp])
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    with pytest.raises(IntegrityError):
        db.flush()
