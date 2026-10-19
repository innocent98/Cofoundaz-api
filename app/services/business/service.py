import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, CanvasVersionConflict, NotFound
from app.db.models.business import (
    BusinessCanvas,
    BusinessCanvasVersion,
    BusinessPlan,
    BusinessRecord,
)
from app.db.models.enums import CanvasType, RecordKind
from app.db.models.startup import Startup
from app.db.models.user import User
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


# D-BB-1: how many snapshots each canvas keeps (oldest pruned first).
MAX_CANVAS_VERSIONS = 50
VERSION_SOURCES = ("manual", "ai", "suggestion", "restore")


def snapshot_canvas(
    db: Session, canvas: BusinessCanvas, *, user_id: uuid.UUID | None, source: str
) -> None:
    """Record the canvas's current state as a version and prune beyond the cap. No commit."""
    canvas.updated_by_id = user_id
    db.add(
        BusinessCanvasVersion(
            canvas_id=canvas.id,
            version=canvas.version,
            blocks=dict(canvas.blocks),
            source=source,
            edited_by_id=user_id,
        )
    )
    db.flush()
    stale = [
        v.id
        for v in db.query(BusinessCanvasVersion.id)
        .filter_by(canvas_id=canvas.id)
        .order_by(BusinessCanvasVersion.version.desc())
        .offset(MAX_CANVAS_VERSIONS)
        .all()
    ]
    if stale:
        db.query(BusinessCanvasVersion).filter(BusinessCanvasVersion.id.in_(stale)).delete(
            synchronize_session=False
        )


def save_canvas(
    db: Session,
    canvas: BusinessCanvas,
    blocks: dict,
    expected_version: int,
    *,
    user_id: uuid.UUID | None = None,
    source: str = "manual",
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
    snapshot_canvas(db, canvas, user_id=user_id, source=source)
    now_complete = completion(canvas.type, canvas.blocks)["status"] == "complete"
    if now_complete and not was_complete:
        event_bus.publish(
            db,
            "business.artifact.completed",
            {"startup_id": str(canvas.startup_id), "canvas_type": canvas.type.value},
        )
    return canvas


def _version(db: Session, canvas: BusinessCanvas, version: int) -> BusinessCanvasVersion:
    row = db.query(BusinessCanvasVersion).filter_by(canvas_id=canvas.id, version=version).first()
    if row is None:
        raise NotFound()
    return row


def list_versions(db: Session, canvas: BusinessCanvas) -> list[BusinessCanvasVersion]:
    return (
        db.query(BusinessCanvasVersion)
        .filter_by(canvas_id=canvas.id)
        .order_by(BusinessCanvasVersion.version.desc())
        .all()
    )


def get_version(db: Session, canvas: BusinessCanvas, version: int) -> BusinessCanvasVersion:
    return _version(db, canvas, version)


def restore_version(
    db: Session, canvas: BusinessCanvas, version: int, expected_version: int, user_id: uuid.UUID
) -> BusinessCanvas:
    """Write the snapshot's blocks as a NEW version (history is never rewritten)."""
    snap = _version(db, canvas, version)
    return save_canvas(
        db, canvas, dict(snap.blocks), expected_version, user_id=user_id, source="restore"
    )


def display_name(user: User) -> str:
    return (user.profile.full_name if user.profile else None) or user.email


def person(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "name": display_name(user)}


def serialize_version(v: BusinessCanvasVersion, *, with_blocks: bool = False) -> dict:
    out = {
        "version": v.version,
        "source": v.source,
        "edited_by": person(v.edited_by),
        "created_at": v.created_at.isoformat(),
    }
    if with_blocks:
        out["blocks"] = v.blocks
    return out


def serialize_canvas(canvas: BusinessCanvas) -> dict:
    return {
        "type": canvas.type.value,
        "version": canvas.version,
        "blocks": canvas.blocks,
        "block_defs": [
            {"key": b.key, "label": b.label, "kind": b.kind} for b in _defs(canvas.type)
        ],
        "completion": completion(canvas.type, canvas.blocks),
        "updated_at": canvas.updated_at.isoformat() if canvas.updated_at else None,
        "updated_by": person(canvas.updated_by),
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
                # Never-saved canvases have no "last edited" (a lazily created row is not an edit).
                "updated_at": (
                    existing[canvas_type].updated_at.isoformat()
                    if canvas_type in existing and existing[canvas_type].version > 1
                    else None
                ),
            }
        )
    counts: dict[RecordKind, int] = dict(
        db.query(BusinessRecord.kind, func.count(BusinessRecord.id))
        .filter_by(startup_id=startup.id)
        .group_by(BusinessRecord.kind)
        .all()  # type: ignore[arg-type]
    )
    last_edit: dict[RecordKind, Any] = dict(
        db.query(BusinessRecord.kind, func.max(BusinessRecord.updated_at))
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
                "updated_at": last_edit[kind].isoformat() if kind in last_edit else None,
            }
        )
    plan = (
        db.query(BusinessPlan)
        .filter_by(startup_id=startup.id)
        .order_by(BusinessPlan.created_at.desc())
        .first()
    )
    rows.append(
        {
            "type": "plan",
            "label": "Business Plan",
            # none | generating | complete | failed (BusinessPlanStatus)
            "status": plan.status.value if plan else "none",
            "document_id": str(plan.document_id) if plan and plan.document_id else None,
            "updated_at": plan.updated_at.isoformat() if plan else None,
        }
    )
    return rows
