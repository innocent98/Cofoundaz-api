from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.enums import MarketingMetricName, MembershipRole, TransactionDirection
from app.db.models.finance import Transaction
from app.db.models.marketing import MarketingMetric
from app.services.dashboard.kpis import kpi_metrics
from app.services.dashboard.service import get_summary
from tests.factories import create_startup, create_user

FOUNDER = MembershipRole.founder


def _today():
    return datetime.now(UTC).date()


def _txn(db, startup, direction, amount, *, days_ago=0, currency="NGN"):
    db.add(
        Transaction(
            startup_id=startup.id,
            date=_today() - timedelta(days=days_ago),
            description="seed",
            amount_minor=amount,
            currency=currency,
            direction=direction,
        )
    )
    db.flush()


def _metric(db, startup, metric, value, *, days_ago=0):
    db.add(
        MarketingMetric(
            startup_id=startup.id,
            ts=_today() - timedelta(days=days_ago),
            metric=metric,
            value=value,
        )
    )
    db.flush()


@pytest.fixture
def startup(db):
    owner = create_user(db)
    return create_startup(db, owner=owner)


def test_empty_workspace_reports_no_data_not_zero(db, startup):
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["revenue"] == {
        "status": "no_data",
        "value_minor": None,
        "currency": None,
        "basis": "trailing_3mo_avg",
    }
    assert m["runway"]["status"] == "no_data"
    assert m["runway"]["months"] is None
    assert m["campaign_ctr"]["status"] == "no_data"
    assert m["campaign_ctr"]["ctr_percent"] is None
    assert m["pipeline"] == {"status": "not_built", "value_minor": None, "currency": None}


def test_burning_startup_gets_revenue_and_runway(db, startup):
    _txn(db, startup, TransactionDirection.inflow, 60_000_000, days_ago=200)  # old raise
    _txn(db, startup, TransactionDirection.inflow, 3_000_000)
    _txn(db, startup, TransactionDirection.outflow, 9_000_000)

    m = kpi_metrics(db, startup, FOUNDER)
    assert m["revenue"]["status"] == "ok"
    assert m["revenue"]["value_minor"] == 1_000_000  # 3,000,000 / 3 months
    assert m["revenue"]["currency"] == "NGN"
    # cash 54,000,000; burn (9M-3M)/3 = 2,000,000/mo -> 27.0 months
    assert m["runway"] == {
        "status": "ok",
        "months": 27.0,
        "is_low": False,
        "monthly_burn_minor": 2_000_000,
        "cash_on_hand_minor": 54_000_000,
        "currency": "NGN",
    }


def test_low_runway_is_flagged(db, startup):
    _txn(db, startup, TransactionDirection.inflow, 9_000_000, days_ago=200)
    _txn(db, startup, TransactionDirection.outflow, 6_000_000)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["runway"]["status"] == "ok"
    assert m["runway"]["months"] == 1.5
    assert m["runway"]["is_low"] is True


def test_profitable_startup_is_not_burning(db, startup):
    _txn(db, startup, TransactionDirection.inflow, 9_000_000)
    _txn(db, startup, TransactionDirection.outflow, 3_000_000)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["runway"]["status"] == "not_burning"
    assert m["runway"]["months"] is None
    assert m["runway"]["is_low"] is False


def test_out_of_cash_reports_zero_months_and_alert(db, startup):
    _txn(db, startup, TransactionDirection.outflow, 3_000_000)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["runway"]["status"] == "out_of_cash"
    assert m["runway"]["months"] == 0
    assert m["runway"]["is_low"] is True


def test_real_zero_revenue_is_ok_not_no_data(db, startup):
    _txn(db, startup, TransactionDirection.outflow, 300)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["revenue"]["status"] == "ok"
    assert m["revenue"]["value_minor"] == 0


def test_future_dated_transactions_alone_are_no_data(db, startup):
    _txn(db, startup, TransactionDirection.inflow, 5_000, days_ago=-10)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["revenue"]["status"] == "no_data"
    assert m["runway"]["status"] == "no_data"


def test_campaign_ctr_over_last_30_days(db, startup):
    _metric(db, startup, MarketingMetricName.impressions, 2_000)
    _metric(db, startup, MarketingMetricName.clicks, 50)
    _metric(db, startup, MarketingMetricName.impressions, 99_999, days_ago=45)  # out of range
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["campaign_ctr"] == {
        "status": "ok",
        "ctr_percent": 2.5,
        "impressions": 2_000,
        "clicks": 50,
        "range": "30d",
    }


def test_clicks_without_impressions_is_no_data(db, startup):
    _metric(db, startup, MarketingMetricName.clicks, 10)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["campaign_ctr"]["status"] == "no_data"
    assert m["campaign_ctr"]["ctr_percent"] is None


def test_accountant_sees_finance_but_not_marketing(db, startup):
    _txn(db, startup, TransactionDirection.inflow, 3_000)
    _metric(db, startup, MarketingMetricName.impressions, 100)
    m = kpi_metrics(db, startup, MembershipRole.accountant)
    assert m["revenue"]["status"] == "ok"
    assert m["runway"]["status"] != "restricted"
    assert m["campaign_ctr"]["status"] == "restricted"
    assert m["campaign_ctr"]["impressions"] is None


@pytest.mark.parametrize(
    "role",
    [
        MembershipRole.mentor,
        MembershipRole.investor,
        MembershipRole.legal_advisor,
        MembershipRole.business_consultant,
    ],
)
def test_non_finance_roles_never_see_money(db, startup, role):
    _txn(db, startup, TransactionDirection.inflow, 3_000)
    _txn(db, startup, TransactionDirection.outflow, 9_000)
    _metric(db, startup, MarketingMetricName.impressions, 100)
    m = kpi_metrics(db, startup, role)
    assert m["revenue"]["status"] == "restricted"
    assert m["revenue"]["value_minor"] is None
    assert m["runway"]["status"] == "restricted"
    assert m["runway"]["months"] is None
    assert m["runway"]["cash_on_hand_minor"] is None
    assert m["campaign_ctr"]["status"] == "restricted"


def test_other_startups_data_never_leaks(db, startup):
    other = create_startup(db, owner=create_user(db))
    _txn(db, other, TransactionDirection.inflow, 3_000)
    _metric(db, other, MarketingMetricName.impressions, 100)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["revenue"]["status"] == "no_data"
    assert m["campaign_ctr"]["status"] == "no_data"


def test_one_failing_metric_does_not_blank_the_others(db, startup, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("finance down")

    monkeypatch.setattr("app.services.dashboard.kpis.cash_flow_summary", boom)
    _txn(db, startup, TransactionDirection.inflow, 3_000)
    _metric(db, startup, MarketingMetricName.impressions, 100)
    m = kpi_metrics(db, startup, FOUNDER)
    assert m["revenue"] == {"error": True}
    assert m["runway"] == {"error": True}
    assert m["campaign_ctr"]["status"] == "ok"


def test_summary_carries_metrics_and_keeps_legacy_keys_null(db, startup):
    owner = create_user(db)
    _txn(db, startup, TransactionDirection.inflow, 3_000)
    kpis = get_summary(db, startup, owner, role=FOUNDER)["kpis"]
    assert kpis["metrics"]["revenue"]["status"] == "ok"
    # Legacy scalars stay null so an older FE keeps rendering its honest placeholder.
    assert kpis["revenue"] is None
    assert kpis["runway"] is None
    assert kpis["pipeline_value"] is None
    assert kpis["campaign_performance"] is None
    assert "tasks_done_this_week" in kpis
