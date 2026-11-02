"""Super Admin audit explorer: filtered, cursor-paged reads of `audit_log` and a CSV export."""

import base64
import binascii
import csv
import io
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Query, Session

from app.core.errors import AppError
from app.db.models.audit import AuditLog
from app.db.models.user import User
from app.services.team.members import _csv_safe

CSV_ROW_LIMIT = 5000


def _bad_cursor() -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Invalid pagination cursor.",
        422,
        field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
    )


def _encode_cursor(row: AuditLog) -> str:
    return base64.urlsafe_b64encode(f"{row.created_at.isoformat()}|{row.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise _bad_cursor() from exc


def _filtered(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str | None,
    startup_id: uuid.UUID | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Query[AuditLog]:
    q = db.query(AuditLog)
    if actor_id:
        q = q.filter(AuditLog.actor_user_id == actor_id)
    if action:
        # `staff.` matches the family; an exact action matches itself. LIKE wildcards are escaped.
        escaped = action.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.filter(
            AuditLog.action.like(f"{escaped}%", escape="\\")
            if action.endswith(".")
            else AuditLog.action == action
        )
    if startup_id:
        q = q.filter(AuditLog.startup_id == startup_id)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    return q


def _rows(db: Session, rows: list[AuditLog]) -> list[dict[str, Any]]:
    ids = {r.actor_user_id for r in rows if r.actor_user_id}
    emails = (
        dict(db.query(User.id, User.email).filter(User.id.in_(ids)).tuples().all()) if ids else {}
    )
    return [
        {
            "id": str(r.id),
            "created_at": r.created_at.isoformat(),
            "action": r.action,
            "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
            "actor_email": emails.get(r.actor_user_id) if r.actor_user_id else None,
            "workspace_id": str(r.startup_id) if r.startup_id else None,
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id) if r.entity_id else None,
            "reason": r.reason,
            "details": r.details,
            "ip": r.ip,
        }
        for r in rows
    ]


def explore(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str | None,
    startup_id: uuid.UUID | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Newest first; the cursor is (time, id) because rows from one transaction share a time."""
    q = _filtered(
        db,
        actor_id=actor_id,
        action=action,
        startup_id=startup_id,
        date_from=date_from,
        date_to=date_to,
    )
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter(
            (AuditLog.created_at < c_ts) | ((AuditLog.created_at == c_ts) & (AuditLog.id < c_id))
        )
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])
    return _rows(db, rows), next_cursor


def export_csv(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str | None,
    startup_id: uuid.UUID | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[str, int]:
    q = _filtered(
        db,
        actor_id=actor_id,
        action=action,
        startup_id=startup_id,
        date_from=date_from,
        date_to=date_to,
    )
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(CSV_ROW_LIMIT).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["when", "actor", "action", "workspace_id", "entity_type", "entity_id", "reason"])
    for r in _rows(db, rows):
        cells = [
            r["actor_email"] or "",
            r["action"],
            r["workspace_id"] or "",
            r["entity_type"] or "",
            r["entity_id"] or "",
            r["reason"] or "",
        ]
        w.writerow([r["created_at"], *[_csv_safe(c) for c in cells]])
    return buf.getvalue(), len(rows)
