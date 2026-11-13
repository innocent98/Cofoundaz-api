"""One resolver per metric. `value(db, startup_id, metric, period)` returns a number, or None when
the workspace has no data for it (never a fake 0). Flow metrics aggregate over the period; point
metrics are read as of `period.end` (live sources are only exact for today, so history comes from
`kpi_snapshots`, see snapshots.py)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.analytics import CustomMetric, CustomMetricPoint
from app.db.models.enums import (
    AssumptionStatus,
    InvestorStage,
    MarketingMetricName,
    MissionTaskStatus,
    RoadmapStatus,
)
from app.db.models.finance import Transaction
from app.db.models.funding import Investor
from app.db.models.health_score import HealthScore
from app.db.models.marketing import MarketingMetric
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.sales import Deal
from app.db.models.validation import Assumption
from app.services.analytics.catalog import Metric
from app.services.analytics.periods import Period, today_utc
from app.services.finance.cashflow import (
    _base_query,
    _inflow_outflow_sums,
    _revenue_inflow_sum,
    cash_flow_summary,
)
from app.services.sales import analytics as sales_analytics
from app.services.sales.overview import pipeline_kpi

Number = int | float


def _bounds(p: Period) -> tuple[datetime, datetime]:
    return (
        datetime.combine(p.start, time.min, tzinfo=UTC),
        datetime.combine(p.end + timedelta(days=1), time.min, tzinfo=UTC),
    )


def has_ledger(db: Session, sid: uuid.UUID) -> bool:
    return (
        db.scalar(select(Transaction.id).where(Transaction.startup_id == sid).limit(1)) is not None
    )


def _fin_flows(db: Session, sid: uuid.UUID, p: Period) -> tuple[int, int]:
    base = _base_query(db, startup_id=sid, today=p.end).filter(Transaction.date >= p.start)
    _in, out = _inflow_outflow_sums()
    rev, outflow = base.with_entities(_revenue_inflow_sum(), out).one()
    return int(rev), int(outflow)


def _revenue(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    return _fin_flows(db, sid, p)[0] if has_ledger(db, sid) else None


def _expenses(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    return _fin_flows(db, sid, p)[1] if has_ledger(db, sid) else None


def _net_burn(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if not has_ledger(db, sid):
        return None
    rev, out = _fin_flows(db, sid, p)
    return max(0, out - rev)


def _cash_balance(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if not has_ledger(db, sid):
        return None
    inflow, outflow = _inflow_outflow_sums()
    i, o = _base_query(db, startup_id=sid, today=p.end).with_entities(inflow, outflow).one()
    return int(i) - int(o)


def _runway(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if p.end < today_utc() or not has_ledger(db, sid):
        return None  # a historic runway is only known from a snapshot
    s = cash_flow_summary(db, startup_id=sid)
    if s["monthly_burn"] == 0:
        return None  # profitable or flat: there is no runway to count down
    return 0 if s["runway_months"] is None else float(s["runway_months"])


def _pipeline(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    return pipeline_kpi(db, sid) if p.end >= today_utc() else None


def _deals(db: Session, sid: uuid.UUID, p: Period) -> dict[str, Any] | None:
    if db.scalar(select(Deal.id).where(Deal.startup_id == sid).limit(1)) is None:
        return None
    lo, hi = _bounds(p)
    won = sales_analytics.won_and_cycle(db, sid, lo, hi)
    wl = sales_analytics.win_loss(db, sid, lo, hi, won_count=won["won_count"])
    return {**won, **wl}


def _deals_won(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    d = _deals(db, sid, p)
    return None if d is None else d["won_count"]


def _closed_revenue(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    d = _deals(db, sid, p)
    return None if d is None else d["won_value_minor"]


def _win_rate(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    d = _deals(db, sid, p)
    return None if d is None else d["win_rate_pct"]


def _cycle(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    d = _deals(db, sid, p)
    return None if d is None else d["avg_cycle_days"]


def _mk_totals(db: Session, sid: uuid.UUID, p: Period) -> dict[MarketingMetricName, int]:
    rows = (
        db.query(MarketingMetric.metric, func.coalesce(func.sum(MarketingMetric.value), 0))
        .filter(
            MarketingMetric.startup_id == sid,
            MarketingMetric.ts >= p.start,
            MarketingMetric.ts <= p.end,
        )
        .group_by(MarketingMetric.metric)
        .all()
    )
    return {m: int(v) for m, v in rows}


def _mk(name: MarketingMetricName) -> Callable[[Session, uuid.UUID, Period], Number | None]:
    def fn(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
        totals = _mk_totals(db, sid, p)
        return totals.get(name) if totals else None

    return fn


def _ctr(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    totals = _mk_totals(db, sid, p)
    imp = totals.get(MarketingMetricName.impressions, 0)
    if imp == 0:
        return None  # undefined without impressions; 0.0 would read as "nobody clicked"
    return round(totals.get(MarketingMetricName.clicks, 0) * 100 / imp, 2)


def _validated_pct(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if p.end < today_utc():
        return None
    total, done = (
        db.query(
            func.count(Assumption.id),
            func.count(Assumption.id).filter(Assumption.status == AssumptionStatus.validated),
        )
        .filter(Assumption.startup_id == sid)
        .one()
    )
    return round(done * 100 / total, 1) if total else None


def _health(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if p.end < today_utc():
        return None
    return db.scalar(select(HealthScore.score).where(HealthScore.startup_id == sid))


def _tasks_done(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    lo, hi = _bounds(p)
    has_any = db.scalar(select(Mission.id).where(Mission.startup_id == sid).limit(1))
    if has_any is None:
        return None
    return (
        db.query(MissionTask)
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(
            Mission.startup_id == sid,
            MissionTask.status == MissionTaskStatus.done,
            MissionTask.completed_at >= lo,
            MissionTask.completed_at < hi,
        )
        .count()
    )


def _milestones_pct(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if p.end < today_utc():
        return None
    total, done = (
        db.query(
            func.count(RoadmapMilestone.id),
            func.count(RoadmapMilestone.id).filter(RoadmapMilestone.status == RoadmapStatus.done),
        )
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == sid)
        .one()
    )
    return round(done * 100 / total, 1) if total else None


def _investors(db: Session, sid: uuid.UUID, p: Period) -> Number | None:
    if p.end < today_utc():
        return None
    total = db.scalar(select(func.count(Investor.id)).where(Investor.startup_id == sid)) or 0
    if total == 0:
        return None
    return int(
        db.scalar(
            select(func.count(Investor.id)).where(
                Investor.startup_id == sid, Investor.stage != InvestorStage.passed.value
            )
        )
        or 0
    )


RESOLVERS: dict[str, Callable[[Session, uuid.UUID, Period], Number | None]] = {
    "finance.revenue": _revenue,
    "finance.expenses": _expenses,
    "finance.net_burn": _net_burn,
    "finance.cash_balance": _cash_balance,
    "finance.runway_months": _runway,
    "sales.pipeline_value": _pipeline,
    "sales.deals_won": _deals_won,
    "sales.closed_revenue": _closed_revenue,
    "sales.win_rate": _win_rate,
    "sales.avg_cycle_days": _cycle,
    "marketing.impressions": _mk(MarketingMetricName.impressions),
    "marketing.clicks": _mk(MarketingMetricName.clicks),
    "marketing.conversions": _mk(MarketingMetricName.conversions),
    "marketing.ctr": _ctr,
    "validation.assumptions_validated_pct": _validated_pct,
    "health.score": _health,
    "execution.tasks_done": _tasks_done,
    "execution.milestones_done_pct": _milestones_pct,
    "funding.investors_active": _investors,
}


def custom_value(db: Session, metric: CustomMetric, p: Period) -> Number | None:
    """Latest monthly entry on or before the period end (an entry is the whole month's figure)."""
    row = (
        db.query(CustomMetricPoint.value)
        .filter(CustomMetricPoint.metric_id == metric.id, CustomMetricPoint.period <= p.end)
        .order_by(CustomMetricPoint.period.desc())
        .first()
    )
    return None if row is None else _num(row[0], metric.unit)


def _num(v: Decimal, unit: str) -> Number:
    return int(v) if unit in ("money", "count") else float(round(v, 2))


def resolve_value(
    db: Session, sid: uuid.UUID, metric: Metric, p: Period, custom: CustomMetric | None = None
) -> Number | None:
    if metric.custom:
        if custom is None:  # a custom catalog entry always carries its row; never an assert
            return None
        return custom_value(db, custom, p)
    return RESOLVERS[metric.key](db, sid, p)
