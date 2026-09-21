from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, CanvasVersionConflict
from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.enums import CanvasType, RecordKind
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.business.canvas_defs import CANVAS_BLOCKS, BlockDef, empty_blocks


def _defs(canvas_type: CanvasType) -> tuple[BlockDef, ...]:
    return CANVAS_BLOCKS[canvas_type]


def get_or_create_canvas(db: Session, startup: Startup, canvas_type: CanvasType) -> BusinessCanvas:
    """Lazily fetch or create the canvas row for `(startup, canvas_type)`.

    Unguarded check-then-INSERT would let two concurrent first-loads of the same
    (startup_id, type) both pass the `row is None` check and both INSERT -- the
    loser's flush raises `IntegrityError` against `uq_business_canvas_startup_type`.
    Mirrors `app/services/dashboard/service.py::_get_or_generate_today_race_safe`:
    run the insert inside a SAVEPOINT so a losing racer rolls back only its own
    attempt, then re-select -- under READ COMMITTED the winner's now-committed row
    is visible.
    """
    row = db.query(BusinessCanvas).filter_by(startup_id=startup.id, type=canvas_type).first()
    if row is None:
        try:
            with db.begin_nested():
                row = BusinessCanvas(
                    startup_id=startup.id,
                    type=canvas_type,
                    blocks=empty_blocks(canvas_type),
                    version=1,
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            # A concurrent caller won the race -- their row is now committed and visible.
            row = db.query(BusinessCanvas).filter_by(startup_id=startup.id, type=canvas_type).one()
    return row


def validate_blocks(canvas_type: CanvasType, blocks: dict) -> None:
    kinds = {b.key: b.kind for b in _defs(canvas_type)}
    for key, value in blocks.items():
        if key not in kinds:
            raise AppError(
                "VALIDATION_ERROR",
                f"Unknown block '{key}' for this canvas.",
                422,
                field_errors=[{"field": key, "message": "Not a block on this canvas."}],
            )
        if kinds[key] == "text" and not isinstance(value, str):
            raise AppError(
                "VALIDATION_ERROR",
                f"Block '{key}' must be text.",
                422,
                field_errors=[{"field": key, "message": "Expected text."}],
            )
        if kinds[key] == "list" and not (
            isinstance(value, list) and all(isinstance(i, str) for i in value)
        ):
            raise AppError(
                "VALIDATION_ERROR",
                f"Block '{key}' must be a list of text items.",
                422,
                field_errors=[{"field": key, "message": "Expected a list of text."}],
            )


def _is_filled(value: Any) -> bool:
    return bool(value)  # non-empty str or non-empty list


def completion(canvas_type: CanvasType, blocks: dict) -> dict:
    defs = _defs(canvas_type)
    total = len(defs)
    filled = sum(1 for b in defs if _is_filled(blocks.get(b.key)))
    if filled == 0:
        status = "start"
    elif filled == total:
        status = "complete"
    else:
        status = "continue"
    return {
        "filled_blocks": filled,
        "total_blocks": total,
        "completion_pct": round(filled / total * 100) if total else 0,
        "status": status,
    }


def save_canvas(
    db: Session, canvas: BusinessCanvas, blocks: dict, expected_version: int
) -> BusinessCanvas:
    validate_blocks(canvas.type, blocks)
    if expected_version != canvas.version:
        raise CanvasVersionConflict(
            message="This canvas was changed elsewhere. Reload and reapply your edits."
        )
    was_complete = completion(canvas.type, canvas.blocks)["status"] == "complete"
    merged = empty_blocks(canvas.type)
    merged.update(blocks)
    canvas.blocks = merged
    canvas.version += 1
    db.flush()
    now_complete = completion(canvas.type, canvas.blocks)["status"] == "complete"
    if now_complete and not was_complete:
        event_bus.publish(
            db,
            "business.artifact.completed",
            {"startup_id": str(canvas.startup_id), "canvas_type": canvas.type.value},
        )
    return canvas


def serialize_canvas(canvas: BusinessCanvas) -> dict:
    return {
        "type": canvas.type.value,
        "version": canvas.version,
        "blocks": canvas.blocks,
        "block_defs": [
            {"key": b.key, "label": b.label, "kind": b.kind} for b in _defs(canvas.type)
        ],
        "completion": completion(canvas.type, canvas.blocks),
    }


def overview(db: Session, startup: Startup) -> list[dict]:
    existing = {c.type: c for c in db.query(BusinessCanvas).filter_by(startup_id=startup.id).all()}
    rows = []
    for canvas_type in CanvasType:
        blocks = (
            existing[canvas_type].blocks if canvas_type in existing else empty_blocks(canvas_type)
        )
        rows.append(
            {
                "type": canvas_type.value,
                "label": canvas_type.value.replace("_", " ").title(),
                **completion(canvas_type, blocks),
            }
        )
    counts: dict[RecordKind, int] = dict(
        db.query(BusinessRecord.kind, func.count(BusinessRecord.id))
        .filter_by(startup_id=startup.id)
        .group_by(BusinessRecord.kind)
        .all()  # type: ignore[arg-type]
    )
    for kind in RecordKind:
        count = counts.get(kind, 0)
        complete = count >= 1
        rows.append(
            {
                "type": kind.value,
                "label": kind.value.replace("_", " ").title(),
                "status": "complete" if complete else "start",
                "completion_pct": 100 if complete else 0,
                "count": count,
            }
        )
    return rows
