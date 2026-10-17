import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.finance import Transaction
from app.db.models.job import Job
from app.db.models.kpi_snapshot import KpiSnapshot
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.dashboard.kpi_history import trend
from app.services.dashboard.kpi_snapshots import RULES, Rule, capture_snapshots, current_values
from app.services.dashboard.kpis import kpi_metrics
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


@pytest.fixture
def ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s


def _txn(db, s, direction, amount, days_ago=0):
    db.add(
        Transaction(
            startup_id=s.id,
            date=date.today() - timedelta(days=days_ago),
            description="seed",
            amount_minor=amount,
            currency="NGN",
            direction=direction,
        )
    )
    db.flush()


def _snap(db, s, metric, value, days_ago):
    db.add(
        KpiSnapshot(
            startup_id=s.id,
            metric=metric,
            period=date.today() - timedelta(days=days_ago),
            value=Decimal(str(value)),
        )
    )
    db.flush()


def _rows(db, s):
    return {
        r.metric: r.value
        for r in db.query(KpiSnapshot).filter_by(startup_id=s.id, period=date.today())
    }


def test_capture_records_only_real_figures_and_is_idempotent(db, ws):
    _u, s = ws
    out = capture_snapshots(db, s, tasks_done_week=2)
    assert out == {"captured": ["tasks_done_week"], "crossings": []}  # no finance data yet
    _txn(db, s, TransactionDirection.inflow, 9_000_000, days_ago=200)
    _txn(db, s, TransactionDirection.outflow, 6_000_000)
    capture_snapshots(db, s, tasks_done_week=3)
    capture_snapshots(db, s, tasks_done_week=4)  # same day: updates, no duplicate rows
    rows = _rows(db, s)
    assert set(rows) == {"tasks_done_week", "revenue", "runway"}
    assert rows["tasks_done_week"] == Decimal("4")
    assert rows["runway"] == Decimal("1.5")
    assert db.query(KpiSnapshot).filter_by(startup_id=s.id).count() == 3


def test_current_values_maps_statuses():
    metrics = {
        "revenue": {"status": "no_data"},
        "runway": {"status": "out_of_cash", "months": 0},
        "campaign_ctr": {"status": "ok", "ctr_percent": 2.5},
    }
    assert current_values(metrics, 1) == {"tasks_done_week": 1, "runway": 0, "campaign_ctr": 2.5}
    assert "runway" not in current_values({"runway": {"status": "not_burning"}}, 0)


def test_trend_delta_and_series(db, ws):
    _u, s = ws
    _snap(db, s, "revenue", 1_000_000, days_ago=7)
    _snap(db, s, "revenue", 1_500_000, days_ago=3)
    _snap(db, s, "revenue", 9_999_999, days_ago=20)  # outside the 14-day window
    t = trend(db, s.id, "revenue", 2_000_000)
    assert t["delta"] == 1_000_000
    assert [p["value"] for p in t["series"]] == [1_000_000, 1_500_000]
    assert t["series"][0]["period"] == (date.today() - timedelta(days=7)).isoformat()
    assert trend(db, s.id, "revenue", None)["delta"] is None
    _snap(db, s, "runway", 4.25, days_ago=7)
    assert trend(db, s.id, "runway", 3.0)["delta"] == -1.25


def test_tiles_carry_trend_only_when_visible(db, ws):
    _u, s = ws
    _txn(db, s, TransactionDirection.inflow, 3_000_000)
    _snap(db, s, "revenue", 400_000, days_ago=7)
    founder = kpi_metrics(db, s, MembershipRole.founder)
    assert founder["revenue"]["trend"]["delta"] == 1_000_000 - 400_000
    investor = kpi_metrics(db, s, MembershipRole.investor)
    assert "trend" not in investor["revenue"]  # history must not leak a restricted value
    assert "trend" not in investor["pipeline"]


@pytest.mark.parametrize(
    ("rule", "prev", "now", "expected"),
    [
        (Rule("runway", "down", 3, "finance", "Runway"), 4, 2.5, True),
        (Rule("runway", "down", 3, "finance", "Runway"), 2.5, 2, False),  # already below
        (Rule("revenue", "up", 100, "finance", "Rev"), 99, 100, True),
        (Rule("revenue", "up", 100, "finance", "Rev"), 100, 150, False),
    ],
)
def test_rule_crossing(rule, prev, now, expected):
    assert rule.crossed(prev, now) is expected


def test_threshold_crossing_publishes_with_copy(db, ws):
    _u, s = ws
    _snap(db, s, "runway", 4, days_ago=1)
    _txn(db, s, TransactionDirection.inflow, 9_000_000, days_ago=200)
    _txn(db, s, TransactionDirection.outflow, 6_000_000)  # runway 1.5 months
    before = len(event_bus.published)
    out = capture_snapshots(db, s, tasks_done_week=0)
    assert out["crossings"] == ["Runway crossed 3 months: now 1.5 months"]
    events = [e for e in event_bus.published[before:] if e[0] == "kpi.threshold.crossed"]
    assert events[0][1]["audience"] == "finance"
    # no previous snapshot -> no alert on the very first capture
    other = create_startup(db, owner=create_user(db))
    assert capture_snapshots(db, other, tasks_done_week=0)["crossings"] == []


def test_revenue_milestone_copy(db, ws):
    _u, s = ws
    _snap(db, s, "revenue", 90_000_000, days_ago=1)
    _txn(db, s, TransactionDirection.inflow, 360_000_000)  # 120,000,000 / month
    out = capture_snapshots(db, s, tasks_done_week=0)
    assert "Monthly revenue crossed ₦1M: now ₦1.2M" in out["crossings"]


def test_rules_cover_the_documented_defaults():
    assert {(r.metric, r.direction, r.threshold) for r in RULES} == {
        ("runway", "down", 3),
        ("revenue", "up", 10_000_000),
        ("revenue", "up", 100_000_000),
        ("revenue", "up", 1_000_000_000),
        ("campaign_ctr", "down", 1.0),
    }


def test_alert_reaches_only_roles_that_can_see_the_metric(db, ws):
    founder, s = ws
    accountant = create_user(db)
    create_membership(db, accountant, s, role=MembershipRole.accountant)
    investor = create_user(db)
    create_membership(db, investor, s, role=MembershipRole.investor)
    db.flush()
    register()
    event_bus.publish(
        db,
        "kpi.threshold.crossed",
        {"startup_id": str(s.id), "audience": "finance", "title": "Runway crossed 3 months"},
    )
    event_bus.publish(
        db,
        "kpi.threshold.crossed",
        {"startup_id": str(s.id), "audience": "marketing", "title": "Campaign CTR crossed 1%"},
    )
    got = {
        (n.user_id, n.title)
        for n in db.query(Notification).filter_by(startup_id=s.id, type="kpi.threshold.crossed")
    }
    assert got == {
        (founder.id, "Runway crossed 3 months"),
        (accountant.id, "Runway crossed 3 months"),
        (founder.id, "Campaign CTR crossed 1%"),
    }


def test_scheduler_window_and_claim(db, ws, monkeypatch):
    from app.core.config import settings
    from app.worker import scheduler

    _u, s = ws
    monkeypatch.setattr(settings, "SCHEDULER_TIMEZONE", "UTC")
    early = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)
    late = datetime(2026, 10, 17, 2, 30, tzinfo=UTC)
    assert scheduler._due_kpi_snapshots(db, early) == []
    due = [d for d in scheduler._due_kpi_snapshots(db, late) if d.scope_key == str(s.id)]
    assert due[0].job_type == "scheduled.kpi.snapshot"
    assert scheduler._claim(db, "kpi.snapshot", str(s.id), "2026-10-17")
    assert [d for d in scheduler._due_kpi_snapshots(db, late) if d.scope_key == str(s.id)] == []


def test_handler(db, ws):
    from app.worker.handlers.scheduled import handle_kpi_snapshot

    _u, s = ws
    handle_kpi_snapshot(db, Job(type="scheduled.kpi.snapshot", payload={"startup_id": str(s.id)}))
    assert "tasks_done_week" in _rows(db, s)
    handle_kpi_snapshot(
        db, Job(type="scheduled.kpi.snapshot", payload={"startup_id": str(uuid.uuid4())})
    )  # missing startup: no-op


def test_copy_formatting():
    from app.services.dashboard.kpi_snapshots import _fmt

    assert _fmt("revenue", 50_000) == "₦500"
    assert _fmt("revenue", 10_000_000) == "₦100K"
    assert _fmt("campaign_ctr", 0.8) == "0.8%"
    assert _fmt("runway", 2.5) == "2.5 months"
