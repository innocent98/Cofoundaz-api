from datetime import UTC, datetime

from app.db.models.enums import ChannelKey, ChannelStatus, MarketingGenerationKind
from app.db.models.marketing import MarketingChannel
from tests.factories import create_startup, create_user


def test_channel_fit_note_columns_persist(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = MarketingChannel(
        startup_id=s.id,
        key=ChannelKey.search,
        status=ChannelStatus.not_started,
        ai_fit_note="High fit: your buyers research heavily.",
        fit_note_generated_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.ai_fit_note == "High fit: your buyers research heavily."
    assert row.fit_note_generated_at is not None


def test_new_generation_kinds_exist():
    assert MarketingGenerationKind.channel_plan.value == "channel_plan"
    assert MarketingGenerationKind.channel_fit.value == "channel_fit"
