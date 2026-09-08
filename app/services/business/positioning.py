from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.business import BusinessPositioningMap, BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.startup import Startup

DEFAULT_AXES: dict[str, Any] = {
    "x": {"label": "Price", "low": "Low", "high": "High"},
    "y": {"label": "Quality", "low": "Low", "high": "High"},
}


def get_or_create_map(db: Session, startup: Startup) -> BusinessPositioningMap:
    """Lazily fetch/create the singleton map row for a startup.

    Mirrors get_or_create_canvas: SAVEPOINT-guarded insert + re-select so two
    concurrent first-loads don't both INSERT past the unique(startup_id).
    """
    row = db.query(BusinessPositioningMap).filter_by(startup_id=startup.id).first()
    if row is None:
        try:
            with db.begin_nested():
                row = BusinessPositioningMap(startup_id=startup.id, axes=dict(DEFAULT_AXES))
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.query(BusinessPositioningMap).filter_by(startup_id=startup.id).one()
    return row


def validate_axes(axes: dict) -> None:
    for axis in ("x", "y"):
        cfg = axes.get(axis)
        if not isinstance(cfg, dict):
            raise AppError(
                "VALIDATION_ERROR",
                f"Axis '{axis}' must be an object with label/low/high.",
                422,
                field_errors=[{"field": axis, "message": "Expected an object."}],
            )
        for key in ("label", "low", "high"):
            if not isinstance(cfg.get(key), str):
                raise AppError(
                    "VALIDATION_ERROR",
                    f"Axis '{axis}.{key}' must be text.",
                    422,
                    field_errors=[{"field": f"{axis}.{key}", "message": "Expected text."}],
                )


def update_axes(db: Session, row: BusinessPositioningMap, axes: dict) -> BusinessPositioningMap:
    validate_axes(axes)
    row.axes = axes
    db.flush()
    return row


def serialize_map(row: BusinessPositioningMap) -> dict[str, Any]:
    return {"axes": row.axes}


def assemble_map(db: Session, startup: Startup) -> dict[str, Any]:
    row = get_or_create_map(db, startup)
    comps = (
        db.query(BusinessRecord)
        .filter_by(startup_id=startup.id, kind=RecordKind.competitor)
        .order_by(BusinessRecord.position.asc())
        .all()
    )
    return {
        "axes": row.axes,
        "competitors": [
            {
                "id": str(c.id),
                "name": c.data.get("name"),
                "x": c.data.get("map_x"),
                "y": c.data.get("map_y"),
                "threat_level": c.data.get("threat_level"),
            }
            for c in comps
        ],
    }
