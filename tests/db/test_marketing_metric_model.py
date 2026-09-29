from datetime import date

from app.db.models.enums import ChannelKey, MarketingMetricName
from app.db.models.marketing import MarketingMetric
from tests.factories import create_startup, create_user


def test_metric_name_values():
    assert {m.value for m in MarketingMetricName} == {
        "visits",
        "impressions",
        "clicks",
        "conversions",
        "spend",
    }


def test_marketing_metric_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = MarketingMetric(
        startup_id=s.id,
        ts=date(2026, 9, 1),
        channel=ChannelKey.email,
        metric=MarketingMetricName.visits,
        value=1200,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.value == 1200 and row.channel == ChannelKey.email
    assert row.campaign_id is None  # nullable
