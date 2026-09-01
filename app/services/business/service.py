from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, CanvasVersionConflict
from app.db.models.business import BusinessCanvas
from app.db.models.enums import CanvasType
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.business.canvas_defs import CANVAS_BLOCKS, BlockDef, empty_blocks


def _defs(canvas_type: CanvasType) -> tuple[BlockDef, ...]:
    return CANVAS_BLOCKS[canvas_type]


def get_or_create_canvas(db: Session, startup: Startup, canvas_type: CanvasType) -> BusinessCanvas:
    row = db.query(BusinessCanvas).filter_by(startup_id=startup.id, type=canvas_type).first()
    if row is None:
        row = BusinessCanvas(
            startup_id=startup.id,
            type=canvas_type,
            blocks=empty_blocks(canvas_type),
            version=1,
        )
        db.add(row)
        db.flush()
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
            "This canvas was changed elsewhere. Reload and reapply your edits."
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
    return rows
