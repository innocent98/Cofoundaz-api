"""Nightly KPI capture (PRD 02.1: `kpi_snapshots`, nightly 02:00 cron) + threshold alerts.

`capture_snapshots` records today's value of each KPI that has a real figure, then compares it
with the previous snapshot and publishes `kpi.threshold.crossed` for every rule crossed. Values
are read with founder visibility (it's a system job); the alert audience is narrowed by role
in the notification registry, so a metric's alert only reaches roles that can see that metric.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.enums import MembershipRole
from app.db.models.kpi_snapshot import KpiSnapshot
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.dashboard.kpi_history import as_number
from app.services.dashboard.kpis import kpi_metrics


def current_values(metrics: dict[str, Any], tasks_done_week: int) -> dict[str, int | float]:
    """The snapshot-able number for each metric that has a real figure right now."""
    out: dict[str, int | float] = {"tasks_done_week": tasks_done_week}
    rev = metrics.get("revenue", {})
    if rev.get("status") == "ok":
        out["revenue"] = rev["value_minor"]
    run = metrics.get("runway", {})
    if run.get("status") in ("ok", "out_of_cash"):
        out["runway"] = run["months"]
    pipe = metrics.get("pipeline", {})
    if pipe.get("status") == "ok":
        out["pipeline"] = pipe["value_minor"]
    ctr = metrics.get("campaign_ctr", {})
    if ctr.get("status") == "ok":
        out["campaign_ctr"] = ctr["ctr_percent"]
    return out


@dataclass(frozen=True)
class Rule:
    metric: str
    direction: str  # "up" | "down"
    threshold: float
    audience: str  # "finance" | "marketing" -> notification recipients by role
    label: str

    def crossed(self, prev: float, now: float) -> bool:
        if self.direction == "down":
            return prev >= self.threshold > now
        return prev < self.threshold <= now


def _naira(minor: float) -> str:
    v = minor / 100
    for unit, size in (("M", 1_000_000), ("K", 1_000)):
        if v >= size:
            return f"₦{v / size:.1f}{unit}".replace(".0" + unit, unit)
    return f"₦{v:,.0f}"


def _fmt(metric: str, value: float) -> str:
    if metric == "revenue":
        return _naira(value)
    if metric == "runway":
        return f"{value:g} months"
    return f"{value:g}%"


# Defaults (documented in the SOP). Runway's 6-month line is already covered by the existing
# finance.runway.low alert, so this adds the more urgent 3-month line.
RULES: tuple[Rule, ...] = (
    Rule("runway", "down", 3, "finance", "Runway"),
    Rule("revenue", "up", 10_000_000, "finance", "Monthly revenue"),
    Rule("revenue", "up", 100_000_000, "finance", "Monthly revenue"),
    Rule("revenue", "up", 1_000_000_000, "finance", "Monthly revenue"),
    Rule("campaign_ctr", "down", 1.0, "marketing", "Campaign CTR"),
)


def _previous(db: Session, startup_id: Any, metric: str, before: date) -> Decimal | None:
    row = (
        db.query(KpiSnapshot.value)
        .filter(
            KpiSnapshot.startup_id == startup_id,
            KpiSnapshot.metric == metric,
            KpiSnapshot.period < before,
        )
        .order_by(KpiSnapshot.period.desc())
        .first()
    )
    return row[0] if row else None


def capture_snapshots(db: Session, startup: Startup, tasks_done_week: int) -> dict[str, Any]:
    """Upsert today's snapshots and publish threshold crossings. Idempotent per day."""
    today = date.today()
    values = current_values(kpi_metrics(db, startup, MembershipRole.founder), tasks_done_week)
    crossings = []
    for metric, value in values.items():
        prev = _previous(db, startup.id, metric, today)
        db.execute(
            insert(KpiSnapshot)
            .values(startup_id=startup.id, metric=metric, period=today, value=value)
            .on_conflict_do_update(
                constraint="uq_kpi_snapshot_metric_period", set_={"value": value}
            )
        )
        if prev is None:
            continue
        for rule in RULES:
            if rule.metric == metric and rule.crossed(as_number(metric, prev), value):
                title = (
                    f"{rule.label} crossed {_fmt(metric, rule.threshold)}: "
                    f"now {_fmt(metric, value)}"
                )
                crossings.append(title)
                event_bus.publish(
                    db,
                    "kpi.threshold.crossed",
                    {
                        "startup_id": str(startup.id),
                        "metric": metric,
                        "threshold": rule.threshold,
                        "value": value,
                        "audience": rule.audience,
                        "title": title,
                    },
                )
    db.flush()
    return {"captured": sorted(values), "crossings": crossings}
