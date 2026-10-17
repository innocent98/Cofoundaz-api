"""Read side of `kpi_snapshots`: week-over-week delta + sparkline series for a tile.

Kept free of imports from `kpis.py` (which calls into here) so there is no import cycle.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.kpi_snapshot import KpiSnapshot

SERIES_DAYS = 14
DELTA_DAYS = 7
# Metrics stored as whole numbers (minor units / counts); the rest are decimals.
INTEGER_METRICS = frozenset({"revenue", "tasks_done_week"})


def as_number(metric: str, value: Decimal) -> int | float:
    return int(value) if metric in INTEGER_METRICS else float(round(value, 2))


def trend(db: Session, startup_id: Any, metric: str, current: int | float | None) -> dict[str, Any]:
    """`{"delta": current - value 7 days ago (None without that snapshot or a current value),
    "series": [{"period", "value"}, ...] last 14 days ascending}`."""
    today = date.today()
    rows = (
        db.query(KpiSnapshot.period, KpiSnapshot.value)
        .filter(
            KpiSnapshot.startup_id == startup_id,
            KpiSnapshot.metric == metric,
            KpiSnapshot.period > today - timedelta(days=SERIES_DAYS),
        )
        .order_by(KpiSnapshot.period.asc())
        .all()
    )
    series = [{"period": p.isoformat(), "value": as_number(metric, v)} for p, v in rows]
    week_ago = next((v for p, v in rows if p == today - timedelta(days=DELTA_DAYS)), None)
    delta = None
    if current is not None and week_ago is not None:
        diff = current - as_number(metric, week_ago)
        delta = int(diff) if metric in INTEGER_METRICS else round(float(diff), 2)
    return {"delta": delta, "series": series}
