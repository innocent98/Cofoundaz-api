"""The weekly readout never states a number that was not computed in Python."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models.enums import (
    CampaignObjective,
    CampaignStatus,
    MarketingMetricName,
    StartupStage,
)
from app.db.models.marketing import Campaign, MarketingMetric
from app.services.marketing.readout_figures import (
    build_figures,
    fallback_output,
    format_money,
    llm_facts,
    unverified_numbers,
)
from tests.factories import create_startup, create_user

TODAY = date(2026, 10, 26)


def _campaign(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    c = Campaign(
        startup_id=s.id,
        name="Secret Name",
        objective=CampaignObjective.launch,
        budget=1000,
        channel_mix={"email": 60, "social": 40},
        status=CampaignStatus.active,
        launched_at=datetime(2026, 10, 14, tzinfo=UTC),
    )
    db.add(c)
    db.flush()
    return c


def _put(db, c, day, metric, value):
    db.add(
        MarketingMetric(
            startup_id=c.startup_id, ts=day, campaign_id=c.id, metric=metric, value=value
        )
    )


def _seed(db, c):
    this, last = TODAY - timedelta(days=2), TODAY - timedelta(days=9)
    for day, imp, clk, conv, spend in [
        (this, 12000, 300, 6, 450075),
        (last, 10000, 200, 4, 300000),
    ]:
        _put(db, c, day, MarketingMetricName.impressions, imp)
        _put(db, c, day, MarketingMetricName.clicks, clk)
        _put(db, c, day, MarketingMetricName.conversions, conv)
        _put(db, c, day, MarketingMetricName.spend, spend)
    db.flush()


def test_format_money_converts_minor_to_major_with_naira_symbol():
    assert format_money(450075, "NGN") == "₦4,500.75"
    assert format_money(0, "NGN") == "₦0.00"
    assert format_money(123456789, "NGN") == "₦1,234,567.89"
    assert format_money(5000, "USD") == "50.00 USD"


def test_figures_are_computed_deterministically(db):
    c = _campaign(db)
    _seed(db, c)
    f = build_figures(db, c, TODAY)
    tw = f["this_week"]
    assert tw["spend"] == {"value": 450075, "display": "₦4,500.75"}
    assert tw["impressions"] == {"value": 12000, "display": "12,000"}
    assert tw["clicks"]["display"] == "300"
    assert tw["ctr"] == {"value": 2.5, "display": "2.5%"}
    assert tw["conversions"]["display"] == "6"
    assert tw["cac"] == {"value": 75013, "display": "₦750.13"}  # 450075 / 6, rounded half up
    ch = f["change"]
    assert ch["spend"] == {"value": 50.0, "display": "+50.0%"}
    assert ch["clicks"]["display"] == "+50.0%"
    assert ch["ctr"] == {"value": 0.5, "display": "+0.5 pts"}  # 2.5% vs 2.0%
    assert ch["cac"]["display"] == "0.0%"  # 75013 vs 75000 -> +0.02% rounds to 0.0
    assert f["last_week"]["spend"]["display"] == "₦3,000.00"
    assert f["currency"] == "NGN" and f["days_live"] == {"value": 12, "display": "12 days"}


def test_zero_denominators_are_not_available_not_invented(db):
    c = _campaign(db)
    f = build_figures(db, c, TODAY)  # no data at all
    tw = f["this_week"]
    assert tw["ctr"] == {"value": None, "display": "n/a"}
    assert tw["cac"] == {"value": None, "display": "n/a"}
    assert f["change"]["clicks"] == {"value": None, "display": "n/a"}
    assert tw["spend"]["display"] == "₦0.00"


def test_llm_facts_carry_only_preformatted_strings_no_raw_minor_units(db):
    c = _campaign(db)
    _seed(db, c)
    facts = llm_facts(build_figures(db, c, TODAY))
    flat = repr(facts)
    assert "450075" not in flat and "₦4,500.75" in flat
    assert "Secret Name" not in flat

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            yield o

    assert list(walk(facts)) == []  # every figure is a string


@pytest.mark.parametrize(
    ("prose", "bad"),
    [
        ("Spend was ₦4,500.75 and clicks reached 300.", []),
        ("CTR is 2.5% (+0.5 pts); conversions 6 at ₦750.13 each.", []),
        ("Clicks are up +50.0%, over the last 7 days.", []),
        ("Spend was ₦4,500.00.", ["4500.00"]),  # off by 75 kobo
        ("Spend was 450075 kobo.", ["450075"]),  # raw minor units are not a provided figure
        ("We got 301 clicks.", ["301"]),
        ("Spend is ₦4,500.75 so about ₦4,501.", ["4501"]),
        ("No numbers here, clicks are up.", []),
    ],
)
def test_unverified_numbers(db, prose, bad):
    c = _campaign(db)
    _seed(db, c)
    facts = llm_facts(build_figures(db, c, TODAY))
    assert unverified_numbers({"summary": prose}, facts) == bad


def test_ratio_words_are_rejected(db):
    c = _campaign(db)
    _seed(db, c)
    facts = llm_facts(build_figures(db, c, TODAY))
    assert unverified_numbers({"headline": "Clicks doubled"}, facts) == ["doubled"]


def test_fallback_output_is_template_prose_that_passes_its_own_validator(db):
    c = _campaign(db)
    _seed(db, c)
    f = build_figures(db, c, TODAY)
    out = fallback_output(f)
    assert set(out) == {"headline", "summary", "wins", "watch", "next_step"}
    assert "₦4,500.75" in out["summary"] and "300" in out["summary"]
    assert unverified_numbers(out, llm_facts(f)) == []
    empty = fallback_output(build_figures(db, _campaign(db), TODAY))
    assert unverified_numbers(empty, llm_facts(build_figures(db, _campaign(db), TODAY))) == []
    assert "no data" in empty["summary"].lower()
