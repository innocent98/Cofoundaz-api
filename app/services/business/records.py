from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.business.record_defs import RECORD_SCHEMAS


def validate(kind: RecordKind, data: dict[str, Any]) -> dict[str, Any]:
    try:
        model = RECORD_SCHEMAS[kind](**data)
    except ValidationError as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Some fields are invalid.",
            422,
            field_errors=[
                {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ],
        ) from exc
    return model.model_dump(mode="json")


def list_records(db: Session, startup: Startup, kind: RecordKind) -> list[BusinessRecord]:
    return (
        db.query(BusinessRecord)
        .filter_by(startup_id=startup.id, kind=kind)
        .order_by(BusinessRecord.position.asc())
        .all()
    )


def create_record(db: Session, startup: Startup, kind: RecordKind, data: dict) -> BusinessRecord:
    clean = validate(kind, data)
    count = db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=kind).count()
    row = BusinessRecord(startup_id=startup.id, kind=kind, data=clean, position=count)
    db.add(row)
    db.flush()
    if count == 0:
        event_bus.publish(
            db,
            "business.artifact.completed",
            {"startup_id": str(startup.id), "artifact": kind.value},
        )
    return row


def update_record(db: Session, record: BusinessRecord, data: dict) -> BusinessRecord:
    record.data = validate(record.kind, data)
    db.flush()
    return record


def delete_record(db: Session, record: BusinessRecord) -> None:
    db.delete(record)
    db.flush()


def serialize_record(record: BusinessRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "kind": record.kind.value,
        "data": record.data,
        "position": record.position,
    }


def _record(
    db: Session, membership: Membership, kind: RecordKind, record_id: Any
) -> BusinessRecord:
    row = (
        db.query(BusinessRecord)
        .filter_by(id=record_id, startup_id=membership.startup_id, kind=kind)
        .first()
    )
    if row is None:
        raise NotFound()
    return row
