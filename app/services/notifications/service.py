import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.notification import Notification


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, row_id = raw.split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def create_notifications(
    db: Session,
    *,
    user_ids: list[uuid.UUID],
    startup_id: uuid.UUID,
    type: str,
    title: str,
    body: str,
    data: dict[str, Any],
) -> list[Notification]:
    rows = [
        Notification(
            user_id=uid,
            startup_id=startup_id,
            type=type,
            title=title,
            body=body,
            data=dict(data),
        )
        for uid in user_ids
    ]
    db.add_all(rows)
    db.flush()
    return rows


def list_notifications(
    db: Session,
    *,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    unread: bool,
    limit: int,
    cursor: str | None,
) -> tuple[list[Notification], str | None]:
    limit = max(1, min(limit, 50))
    q = (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.startup_id == startup_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    if unread:
        q = q.filter(Notification.read_at.is_(None))
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter(
            (Notification.created_at < c_ts)
            | ((Notification.created_at == c_ts) & (Notification.id < c_id))
        )
    rows = q.limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return rows, next_cursor


def unread_count(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID) -> int:
    return (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            Notification.read_at.is_(None),
        )
        .count()
    )


def mark_read(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, notification_id: Any
) -> Notification:
    row = (
        db.query(Notification)
        .filter_by(id=notification_id, user_id=user_id, startup_id=startup_id)
        .first()
    )
    if row is None:
        raise NotFound()
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        db.flush()
    return row


def mark_all_read(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID) -> int:
    n = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            Notification.read_at.is_(None),
        )
        .update({Notification.read_at: datetime.now(UTC)}, synchronize_session=False)
    )
    db.flush()
    return n


def serialize_notification(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "data": n.data,
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat(),
    }
