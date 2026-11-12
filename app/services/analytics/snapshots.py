"""Nightly catalog snapshots (D-ANA-3 c): point metrics land in `kpi_snapshots`, the table the
dashboard tiles already read, so trends and comparison periods have history. Flow metrics are not
stored: they are exactly recomputable from the source data for any past period."""

import logging

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.kpi_snapshot import KpiSnapshot
from app.db.models.startup import Startup
from app.services.analytics import periods as per
from app.services.analytics import service as svc
from app.services.analytics.resolvers import resolve_value

log = logging.getLogger(__name__)


def capture(db: Session, startup: Startup) -> list[str]:
    today = per.today_utc()
    p = per.Period(today, today)
    stored: list[str] = []
    for key, (m, c) in svc.all_metrics(db, startup.id).items():
        if m.kind != "point":
            continue
        try:
            with db.begin_nested():
                value = resolve_value(db, startup.id, m, p, c)
                if value is None:
                    continue
                db.execute(
                    insert(KpiSnapshot)
                    .values(startup_id=startup.id, metric=key, period=today, value=value)
                    .on_conflict_do_update(
                        constraint="uq_kpi_snapshot_metric_period", set_={"value": value}
                    )
                )
                stored.append(key)
        except Exception:  # noqa: BLE001 - one broken source must not stop the other metrics
            log.exception("analytics snapshot failed for %s (%s)", key, startup.id)
    return stored
