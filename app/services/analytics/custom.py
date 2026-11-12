"""Founder-entered custom metrics (D-ANA-2 b): users, MRR, churn, anything the platform cannot see."""

import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.analytics import CustomMetric, CustomMetricPoint
from app.services.analytics import service as svc
from app.services.analytics.errors import not_found, validation

MAX_METRICS = 20
MAX_POINTS_PER_CALL = 36
UNITS = ("count", "money", "percent")
MAX_VALUE = Decimal("999999999999")


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return s[:30] or "metric"


def _month(raw: str) -> date:
    """`2026-10` or `2026-10-17` -> the first of that month (ValueError when malformed)."""
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", raw.strip())
    if m is None:
        raise ValueError(raw)
    return date(int(m[1]), int(m[2]), 1)


def serialize(m: CustomMetric, points: list[CustomMetricPoint] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(m.id),
        "key": m.key,
        "label": m.label,
        "unit": m.unit,
        "currency": svc.CURRENCY if m.unit == "money" else None,
    }
    if points is not None:
        out["points"] = [
            {"period": p.period.isoformat(), "value": svc._round(float(p.value))} for p in points
        ]
    return out


def list_all(db: Session, startup_id: uuid.UUID) -> list[dict[str, Any]]:
    out = []
    for m in svc.custom_metrics(db, startup_id):
        pts = (
            db.query(CustomMetricPoint)
            .filter(CustomMetricPoint.metric_id == m.id)
            .order_by(CustomMetricPoint.period.desc())
            .limit(24)
            .all()
        )
        out.append(serialize(m, list(reversed(pts))))
    return out


def create(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, label: str, unit: str) -> dict:
    label = (label or "").strip()
    if not label or len(label) > 80:
        raise validation("label", "Give the metric a name of up to 80 characters.")
    if unit not in UNITS:
        raise validation("unit", f"Pick one of: {', '.join(UNITS)}.")
    existing = svc.custom_metrics(db, startup_id)
    if len(existing) >= MAX_METRICS:
        raise validation("label", f"You can keep up to {MAX_METRICS} custom metrics.")
    base = _slug(label)
    taken = {m.key for m in existing}
    key, n = f"custom.{base}", 2
    while key in taken:
        key = f"custom.{base[:27]}_{n}"
        n += 1
    m = CustomMetric(startup_id=startup_id, key=key, label=label, unit=unit, created_by=user_id)
    db.add(m)
    db.flush()
    svc.invalidate(startup_id)
    return serialize(m, [])


def _get(db: Session, startup_id: uuid.UUID, metric_id: uuid.UUID) -> CustomMetric:
    m = (
        db.query(CustomMetric)
        .filter(CustomMetric.id == metric_id, CustomMetric.startup_id == startup_id)
        .one_or_none()
    )
    if m is None:
        raise not_found("Metric not found.")
    return m


def put_points(
    db: Session,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    metric_id: uuid.UUID,
    points: list[dict[str, Any]],
) -> dict:
    m = _get(db, startup_id, metric_id)
    if not points or len(points) > MAX_POINTS_PER_CALL:
        raise validation("points", f"Send between 1 and {MAX_POINTS_PER_CALL} monthly values.")
    seen: set[date] = set()
    today = date.today()
    rows = []
    for i, p in enumerate(points):
        try:
            d = _month(str(p.get("period", "")))
        except ValueError:
            raise validation(f"points.{i}.period", "Use a month like 2026-10.") from None
        if d in seen:
            raise validation(f"points.{i}.period", "That month appears twice.")
        if d > today.replace(day=1) or d.year < 2000:
            raise validation(f"points.{i}.period", "Pick a month up to this one.")
        seen.add(d)
        try:
            v = Decimal(str(p.get("value")))
        except (InvalidOperation, ValueError):
            raise validation(f"points.{i}.value", "Enter a number.") from None
        if not v.is_finite() or abs(v) > MAX_VALUE or (m.unit != "percent" and v < 0):
            raise validation(f"points.{i}.value", "That number is out of range.")
        if m.unit in ("count", "money") and v != v.to_integral_value():
            raise validation(
                f"points.{i}.value",
                (
                    "Use whole numbers (money in kobo)."
                    if m.unit == "money"
                    else "Use a whole number."
                ),
            )
        rows.append({"metric_id": m.id, "period": d, "value": v, "entered_by": user_id})
    stmt = insert(CustomMetricPoint).values(rows)
    db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_custom_metric_point",
            set_={"value": stmt.excluded.value, "entered_by": stmt.excluded.entered_by},
        )
    )
    db.flush()
    svc.invalidate(startup_id)
    pts = (
        db.query(CustomMetricPoint)
        .filter(CustomMetricPoint.metric_id == m.id)
        .order_by(CustomMetricPoint.period.asc())
        .all()
    )
    return serialize(m, pts)


def delete(db: Session, startup_id: uuid.UUID, metric_id: uuid.UUID) -> None:
    m = _get(db, startup_id, metric_id)
    db.delete(m)
    db.flush()
    svc.invalidate(startup_id)
