"""Dashboard KPI tiles, read off the Finance and Marketing modules.

Every metric carries a `status` so the FE never has to infer meaning from a null:
  ok          -- a real figure (which may legitimately be 0)
  no_data     -- the module exists but this workspace hasn't recorded anything yet
  restricted  -- the caller's role can't read the source module (mirrors the module's route gate)
  not_built   -- the source module has no backend yet (Sales -> pipeline)
Runway adds `not_burning` (break-even/profitable: no runway to count down) and `out_of_cash`.

Each metric is computed in its own SAVEPOINT, so one failing source degrades
only its own tile to `{"error": True}` instead of blanking the strip.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models.enums import (
    FINANCE_ROLES,
    MARKETING_ROLES,
    MarketingMetricName,
    MembershipRole,
)
from app.db.models.finance import Transaction
from app.db.models.marketing import MarketingMetric
from app.db.models.startup import Startup
from app.services.dashboard.kpi_history import trend
from app.services.finance.cashflow import cash_flow_summary
from app.services.marketing.analytics import _rate

CTR_RANGE_DAYS = 30


def _has_transactions(db: Session, startup: Startup) -> bool:
    # Same `date <= today` cut as cash_flow_summary, so a workspace holding only
    # future-dated rows reads as no_data rather than a misleading 0.
    today = datetime.now(UTC).date()
    return (
        db.query(Transaction.id)
        .filter(Transaction.startup_id == startup.id, Transaction.date <= today)
        .first()
        is not None
    )


def _revenue(db: Session, startup: Startup, role: MembershipRole) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "no_data",
        "value_minor": None,
        "currency": None,
        "basis": "trailing_3mo_avg",
    }
    if role not in FINANCE_ROLES:
        return {**out, "status": "restricted"}
    if not _has_transactions(db, startup):
        return out
    summary = cash_flow_summary(db, startup_id=startup.id)
    return {
        **out,
        "status": "ok",
        "value_minor": summary["monthly_revenue"],
        "currency": summary["currency"],
    }


def _runway(db: Session, startup: Startup, role: MembershipRole) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "no_data",
        "months": None,
        "is_low": False,
        "monthly_burn_minor": None,
        "cash_on_hand_minor": None,
        "currency": None,
    }
    if role not in FINANCE_ROLES:
        return {**out, "status": "restricted"}
    if not _has_transactions(db, startup):
        return out
    s = cash_flow_summary(db, startup_id=startup.id)
    filled = {
        **out,
        "is_low": s["runway_low"],
        "monthly_burn_minor": s["monthly_burn"],
        "cash_on_hand_minor": s["cash_on_hand"],
        "currency": s["currency"],
    }
    if s["monthly_burn"] == 0:
        return {**filled, "status": "not_burning"}
    if s["runway_months"] is None:  # burning with cash_on_hand <= 0 (see cash_flow_summary)
        return {**filled, "status": "out_of_cash", "months": 0}
    return {**filled, "status": "ok", "months": s["runway_months"]}


def _campaign_ctr(db: Session, startup: Startup, role: MembershipRole) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "no_data",
        "ctr_percent": None,
        "impressions": None,
        "clicks": None,
        "range": f"{CTR_RANGE_DAYS}d",
    }
    if role not in MARKETING_ROLES:
        return {**out, "status": "restricted"}
    cutoff = datetime.now(UTC).date() - timedelta(days=CTR_RANGE_DAYS)

    def _total(metric: MarketingMetricName) -> Any:
        return func.coalesce(
            func.sum(case((MarketingMetric.metric == metric, MarketingMetric.value), else_=0)), 0
        )

    impressions, clicks = (
        db.query(MarketingMetric)
        .filter(MarketingMetric.startup_id == startup.id, MarketingMetric.ts >= cutoff)
        .with_entities(_total(MarketingMetricName.impressions), _total(MarketingMetricName.clicks))
        .one()
    )
    impressions, clicks = int(impressions), int(clicks)
    # CTR is undefined without impressions; _rate would report 0.0, which reads as "nobody clicked".
    if impressions == 0:
        return out
    return {
        **out,
        "status": "ok",
        "ctr_percent": _rate(clicks, impressions),
        "impressions": impressions,
        "clicks": clicks,
    }


def _pipeline() -> dict[str, Any]:
    # Sales has no backend yet; keep the shape stable so the FE wiring won't change when it lands.
    return {"status": "not_built", "value_minor": None, "currency": None}


# Which field of each tile holds its number (for the snapshot trend).
_TREND_FIELD = {"revenue": "value_minor", "runway": "months", "campaign_ctr": "ctr_percent"}


def _with_trend(db: Session, startup: Startup, metric: str, tile: dict[str, Any]) -> dict[str, Any]:
    """Attach `trend` (7-day delta + 14-day series) to tiles the caller may see. Restricted,
    errored and not-built tiles get none: history must not leak what the value would."""
    if tile.get("error") or tile.get("status") in ("restricted", "not_built"):
        return tile
    current = tile.get(_TREND_FIELD[metric])
    return {**tile, "trend": trend(db, startup.id, metric, current)}


def kpi_metrics(db: Session, startup: Startup, role: MembershipRole) -> dict[str, Any]:
    run = _savepoint(db)
    revenue = run(lambda: _revenue(db, startup, role))
    runway = run(lambda: _runway(db, startup, role))
    ctr = run(lambda: _campaign_ctr(db, startup, role))
    return {
        "revenue": run(lambda: _with_trend(db, startup, "revenue", revenue)),
        "runway": run(lambda: _with_trend(db, startup, "runway", runway)),
        "pipeline": _pipeline(),
        "campaign_ctr": run(lambda: _with_trend(db, startup, "campaign_ctr", ctr)),
    }


def _savepoint(db: Session) -> Callable[[Callable[[], Any]], Any]:
    def run(fn: Callable[[], Any]) -> Any:
        try:
            with db.begin_nested():
                return fn()
        except Exception:  # noqa: BLE001 - per-tile isolation: one source must not blank the strip
            return {"error": True}

    return run
