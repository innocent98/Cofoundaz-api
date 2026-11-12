"""Metric resolution with per-metric authorisation (D-ANA-4) and a 5-minute cache (D-ANA-3 c).

Order matters and is the security property: authorise first, then touch the cache or the source.
The cache holds only workspace-level resolved numbers (never keyed by role), so a locked metric can
never be served from it: it is refused before the cache is read.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.analytics import CustomMetric
from app.db.models.enums import MembershipRole
from app.db.models.kpi_snapshot import KpiSnapshot
from app.db.models.startup import Startup
from app.services.analytics import catalog as cat
from app.services.analytics import periods as per
from app.services.analytics.errors import validation
from app.services.analytics.resolvers import has_ledger, resolve_value
from app.services.finance.cashflow import cash_flow_summary

log = logging.getLogger(__name__)
CACHE_SECONDS = 300
NO_DATA_CACHE_SECONDS = 30
RATE_LIMIT_PER_MINUTE = 40  # heavy reads per workspace (D-ANA-L4)
CURRENCY = "NGN"
MAX_BATCH = 24
MAX_POINTS = 12


def check_rate(startup_id: uuid.UUID) -> None:
    """Per-workspace limit on the heavy reads (Overview, By hub, batch resolve): one Overview can
    run about 50 queries and one resolve up to 24 metrics x 13 queries. A fixed one-minute window
    in Redis; fails open if Redis is down (the limit protects the database, not security)."""
    try:
        r = get_redis()
        key = f"analytics:rl:{startup_id}:{int(datetime.now(UTC).timestamp() // 60)}"
        used = int(r.incr(key))  # type: ignore[arg-type]
        if used == 1:
            r.expire(key, 90)
    except Exception:  # noqa: BLE001
        return
    if used > RATE_LIMIT_PER_MINUTE:
        raise AppError("RATE_LIMITED", "Too many report requests. Try again in a minute.", 429)


def custom_metrics(db: Session, startup_id: uuid.UUID) -> list[CustomMetric]:
    return (
        db.query(CustomMetric)
        .filter(CustomMetric.startup_id == startup_id)
        .order_by(CustomMetric.created_at.asc())
        .all()
    )


def _as_metric(c: CustomMetric) -> cat.Metric:
    return cat.Metric(
        c.key,
        c.label,
        c.unit,
        "custom",
        "point",
        "Entered by you. The latest monthly value on or before the end of the period.",
        source_note="Entered by you",
        custom=True,
    )


def all_metrics(
    db: Session, startup_id: uuid.UUID
) -> dict[str, tuple[cat.Metric, CustomMetric | None]]:
    out: dict[str, tuple[cat.Metric, CustomMetric | None]] = {m.key: (m, None) for m in cat.STATIC}
    for c in custom_metrics(db, startup_id):
        out[c.key] = (_as_metric(c), c)
    return out


def catalog_for(db: Session, startup: Startup, role: MembershipRole) -> dict[str, Any]:
    """The picker: every entry the viewer may use, grouped by hub. Hubs the viewer cannot open are
    listed as locked with no metric detail beyond the hub name (so the builder can say why)."""
    metrics = all_metrics(db, startup.id)
    hubs: list[dict[str, Any]] = []
    for hub in cat.HUB_ORDER:
        if cat.can_see(role, hub):
            hubs.append(
                {
                    "hub": hub,
                    "label": cat.HUB_LABELS[hub],
                    "locked": False,
                    "metrics": [_describe(m) for m, _ in metrics.values() if m.hub == hub],
                }
            )
        elif role in cat.ANALYTICS_ROLES:
            hubs.append({"hub": hub, "label": cat.HUB_LABELS[hub], "locked": True, "metrics": []})
    return {"hubs": hubs, "currency": CURRENCY}


def _describe(m: cat.Metric) -> dict[str, Any]:
    return {
        "key": m.key,
        "label": m.label,
        "unit": m.unit,
        "hub": m.hub,
        "kind": m.kind,
        "definition": m.definition,
        "source_note": m.source_note,
        "good": m.good,
        "group_by": list(m.group_by),
        "entered_by_you": m.custom,
    }


# ---- cache ----
def _cache_key(
    sid: uuid.UUID, key: str, p: per.Period, cmp: per.Period | None, series: bool
) -> str:
    c = f"{cmp.start}:{cmp.end}" if cmp else "-"
    return f"analytics:v1:{sid}:{key}:{p.start}:{p.end}:{c}:{int(series)}"


def _cache_get(k: str) -> dict[str, Any] | None:
    try:
        raw = get_redis().get(k)
        return json.loads(str(raw)) if raw else None
    except Exception:  # noqa: BLE001 - the cache is an optimisation, never a dependency
        return None


def _cache_set(k: str, v: dict[str, Any], ttl: int = CACHE_SECONDS) -> None:
    try:
        get_redis().setex(k, ttl, json.dumps(v))
    except Exception:  # noqa: BLE001
        log.debug("analytics cache write skipped", exc_info=True)


def invalidate(startup_id: uuid.UUID) -> None:
    """Drop this workspace's cached numbers (called after custom metric edits)."""
    try:
        r = get_redis()
        for k in r.scan_iter(match=f"analytics:v1:{startup_id}:*", count=200):
            r.delete(k)
    except Exception:  # noqa: BLE001
        log.debug("analytics cache invalidate skipped", exc_info=True)


# ---- snapshots (point history) ----
def snapshot_before(
    db: Session, sid: uuid.UUID, key: str, day: Any, within_days: int = 7
) -> float | None:
    from datetime import timedelta

    row = (
        db.query(KpiSnapshot.value)
        .filter(
            KpiSnapshot.startup_id == sid,
            KpiSnapshot.metric == key,
            KpiSnapshot.period <= day,
            KpiSnapshot.period >= day - timedelta(days=within_days),
        )
        .order_by(KpiSnapshot.period.desc())
        .first()
    )
    return None if row is None else float(row[0])


def _snapshot_series(db: Session, sid: uuid.UUID, key: str, p: per.Period) -> list[dict[str, Any]]:
    rows = (
        db.query(KpiSnapshot.period, KpiSnapshot.value)
        .filter(
            KpiSnapshot.startup_id == sid,
            KpiSnapshot.metric == key,
            KpiSnapshot.period >= p.start,
            KpiSnapshot.period <= p.end,
        )
        .order_by(KpiSnapshot.period.asc())
        .all()
    )
    if len(rows) > MAX_POINTS:
        step = len(rows) / MAX_POINTS
        rows = [rows[int(i * step)] for i in range(MAX_POINTS - 1)] + [rows[-1]]
    return [
        {"from": d.isoformat(), "to": d.isoformat(), "value": _round(float(v))} for d, v in rows
    ]


def _round(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(v, 2)


# ---- resolution ----
def _compute(
    db: Session,
    startup: Startup,
    m: cat.Metric,
    c: CustomMetric | None,
    p: per.Period,
    cmp: per.Period | None,
    with_series: bool,
) -> dict[str, Any]:
    value = resolve_value(db, startup.id, m, p, c)
    out: dict[str, Any] = {
        "key": m.key,
        "label": m.label,
        "unit": m.unit,
        "hub": m.hub,
        "kind": m.kind,
        "good": m.good,
        "source_note": m.source_note,
        "definition": m.definition,
        "currency": CURRENCY if m.unit == "money" else None,
        "status": _status(db, startup.id, m, value),
        "value": value,
        "period": p.iso(),
        "as_of": datetime.now(UTC).isoformat(),
        "hub_path": cat.HUB_PATHS[m.hub],
        "compare": None,
        "series": [],
    }
    if cmp is not None:
        if m.kind == "flow" or m.custom:
            before = resolve_value(db, startup.id, m, cmp, c)
        else:
            before = snapshot_before(db, startup.id, m.key, cmp.end)
        out["compare"] = {"period": cmp.iso(), **_delta(value, before)}
    if with_series:
        out["series"] = _series(db, startup, m, c, p)
    return out


def _status(db: Session, sid: uuid.UUID, m: cat.Metric, value: Any) -> str:
    """ok | no_data, plus the two honest runway states the dashboard tile also has: `not_burning`
    (profitable or flat: no runway to count down) and `out_of_cash` (burning with no cash left)."""
    if m.key == "finance.runway_months":
        if value is None:
            s = cash_flow_summary(db, startup_id=sid) if has_ledger(db, sid) else None
            return "not_burning" if s is not None and s["monthly_burn"] == 0 else "no_data"
        if value == 0:
            return "out_of_cash"
    return "ok" if value is not None else "no_data"


def _delta(now: Any, before: Any) -> dict[str, Any]:
    if now is None or before is None:
        return {
            "value": before if before is None else _round(float(before)),
            "delta": None,
            "delta_pct": None,
        }
    delta = now - before
    pct = round(delta * 100 / before, 1) if before else None
    return {"value": _round(float(before)), "delta": _round(float(delta)), "delta_pct": pct}


def _series(
    db: Session, startup: Startup, m: cat.Metric, c: CustomMetric | None, p: per.Period
) -> list[dict[str, Any]]:
    if m.kind == "flow":
        pts = []
        for b in per.buckets(p):
            v = resolve_value(db, startup.id, m, b, c)
            pts.append({"from": b.start.isoformat(), "to": b.end.isoformat(), "value": v})
        return pts if any(x["value"] is not None for x in pts) else []
    if m.custom:
        pts = []
        for b in per.buckets(p) if p.days > 31 else [p]:
            v = resolve_value(db, startup.id, m, b, c)
            pts.append({"from": b.start.isoformat(), "to": b.end.isoformat(), "value": v})
        return pts if any(x["value"] is not None for x in pts) else []
    return _snapshot_series(db, startup.id, m.key, p)


def resolve(
    db: Session,
    startup: Startup,
    role: MembershipRole,
    key: str,
    p: per.Period,
    compare_mode: str = "none",
    with_series: bool = False,
) -> dict[str, Any]:
    metrics = all_metrics(db, startup.id)
    entry = metrics.get(key)
    if entry is None:
        return {"key": key, "status": "unknown", "message": "Metric no longer available."}
    m, c = entry
    if not cat.can_see(role, m.hub):  # authorise BEFORE the cache or the source is touched
        if m.custom:
            # A custom key is the workspace's own naming: never confirm it exists (or leak its
            # label) to a role that cannot read custom metrics. Same shape as a missing key.
            return {"key": key, "status": "unknown", "message": "Metric no longer available."}
        return {
            "key": key,
            "label": m.label,
            "hub": m.hub,
            "status": "locked",
            "needs": m.hub,
            "needs_label": cat.HUB_LABELS[m.hub],
        }
    cmp = per.comparison(p, compare_mode)
    ck = _cache_key(startup.id, key, p, cmp, with_series)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    out = _compute(db, startup, m, c, p, cmp, with_series)
    # An empty answer is cached too (it still costs the queries), but only briefly so the first
    # data a founder adds shows within half a minute.
    _cache_set(ck, out, NO_DATA_CACHE_SECONDS if out["status"] == "no_data" else CACHE_SECONDS)
    return out


def resolve_many(
    db: Session,
    startup: Startup,
    role: MembershipRole,
    specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(specs) > MAX_BATCH:
        raise validation("metrics", f"Ask for at most {MAX_BATCH} metrics at a time.")
    out = []
    for s in specs:
        p = per.resolve(s.get("range", "30d"), s.get("from_", s.get("from")), s.get("to"))
        out.append(
            resolve(
                db,
                startup,
                role,
                str(s.get("key", "")),
                p,
                s.get("compare", "none"),
                bool(s.get("series", False)),
            )
        )
    return out
