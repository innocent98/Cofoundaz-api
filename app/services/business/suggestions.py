from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.business import BusinessCanvas, BusinessRecord, BusinessSuggestion
from app.db.models.enums import CanvasType, RecordKind, SuggestionOp, SuggestionStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.business.canvas_defs import empty_blocks
from app.services.business.records import _record, validate
from app.services.business.service import get_or_create_canvas, validate_blocks


def _parse_canvas_type(target: dict) -> CanvasType:
    try:
        return CanvasType(target["canvas_type"])
    except (KeyError, ValueError):
        raise NotFound() from None


def _parse_kind(target: dict) -> RecordKind:
    try:
        return RecordKind(target["kind"])
    except (KeyError, ValueError):
        raise NotFound() from None


def create_suggestion(
    db: Session,
    membership: Membership,
    op: SuggestionOp,
    target: dict,
    payload: dict | None,
    note: str | None,
) -> BusinessSuggestion:
    startup = db.query(Startup).filter_by(id=membership.startup_id).one()
    base_version: int | None = None

    if op == SuggestionOp.canvas_update:
        ctype = _parse_canvas_type(target)
        blocks = (payload or {}).get("blocks", {})
        validate_blocks(ctype, blocks)  # 422 on bad block
        canvas = get_or_create_canvas(db, startup, ctype)
        base_version = canvas.version
        target = {"canvas_type": ctype.value}
    elif op == SuggestionOp.record_create:
        kind = _parse_kind(target)
        validate(kind, (payload or {}).get("data", {}))  # 422 on bad data
        target = {"kind": kind.value}
    elif op == SuggestionOp.record_update:
        kind = _parse_kind(target)
        rec = _record(db, membership, kind, target.get("record_id"))  # 404 if missing/cross-tenant
        validate(kind, (payload or {}).get("data", {}))
        target = {"kind": kind.value, "record_id": str(rec.id)}
    elif op == SuggestionOp.record_delete:
        kind = _parse_kind(target)
        rec = _record(db, membership, kind, target.get("record_id"))  # 404
        payload = None
        target = {"kind": kind.value, "record_id": str(rec.id)}

    row = BusinessSuggestion(
        startup_id=startup.id,
        author_id=membership.user_id,
        op=op,
        target=target,
        payload=payload,
        base_version=base_version,
        note=note,
    )
    db.add(row)
    db.flush()
    event_bus.publish(
        "business.suggestion.created",
        {"startup_id": str(startup.id), "suggestion_id": str(row.id), "op": op.value},
    )
    return row


def list_suggestions(
    db: Session, startup: Startup, status: SuggestionStatus | None
) -> list[BusinessSuggestion]:
    q = db.query(BusinessSuggestion).filter_by(startup_id=startup.id)
    if status is not None:
        q = q.filter_by(status=status)
    return q.order_by(BusinessSuggestion.created_at.desc()).all()


def _current(db: Session, s: BusinessSuggestion) -> dict | None:
    if s.op == SuggestionOp.canvas_update:
        ctype = CanvasType(s.target["canvas_type"])
        canvas = db.query(BusinessCanvas).filter_by(startup_id=s.startup_id, type=ctype).first()
        if canvas is None:
            return {"blocks": empty_blocks(ctype), "version": 0}
        return {"blocks": canvas.blocks, "version": canvas.version}
    if s.op in (SuggestionOp.record_update, SuggestionOp.record_delete):
        rec = (
            db.query(BusinessRecord)
            .filter_by(id=s.target["record_id"], startup_id=s.startup_id)
            .first()
        )
        return {"data": rec.data} if rec else None
    return None  # record_create has no prior state


def serialize_suggestion(db: Session, s: BusinessSuggestion) -> dict[str, Any]:
    author = db.query(User).filter_by(id=s.author_id).first()
    resolver = db.query(User).filter_by(id=s.resolved_by_id).first() if s.resolved_by_id else None
    return {
        "id": str(s.id),
        "op": s.op.value,
        "target": s.target,
        "payload": s.payload,
        "base_version": s.base_version,
        "current": _current(db, s),
        "note": s.note,
        "status": s.status.value,
        "author": {
            "id": str(s.author_id),
            "name": author.profile.full_name if author and author.profile else None,
            "email": author.email if author else None,
        },
        "resolved_by": (
            {
                "id": str(s.resolved_by_id),
                "name": resolver.profile.full_name if resolver and resolver.profile else None,
            }
            if s.resolved_by_id
            else None
        ),
        "resolved_at": s.resolved_at.isoformat() if s.resolved_at else None,
        "created_at": s.created_at.isoformat(),
    }


def _suggestion(db: Session, membership: Membership, suggestion_id: Any) -> BusinessSuggestion:
    row = (
        db.query(BusinessSuggestion)
        .filter_by(id=suggestion_id, startup_id=membership.startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    return row
