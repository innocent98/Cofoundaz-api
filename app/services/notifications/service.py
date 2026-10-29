import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, true
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from app.services.notifications.audience import audience_filter
from app.services.notifications.categories import (
    CRITICAL_TYPES,
    FILTER_TYPES,
    is_critical,
    module_for,
)


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
    hidden: bool = False,
) -> list[Notification]:
    rows = [
        Notification(
            user_id=uid,
            startup_id=startup_id,
            type=type,
            title=title,
            body=body,
            data=dict(data),
            hidden=hidden,
        )
        for uid in user_ids
    ]
    db.add_all(rows)
    db.flush()
    if hidden:
        return rows  # not in the inbox, so nothing to push to it
    pending = db.info.setdefault("pending_realtime", [])
    for n in rows:
        pending.append(
            (
                str(n.startup_id),
                str(n.user_id),
                {"event": "notification.created", "notification": serialize_notification(n)},
            )
        )
    return rows


def list_notifications(
    db: Session,
    *,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    role: MembershipRole,
    unread: bool,
    limit: int,
    cursor: str | None,
    filter_: str = "all",
    archived: bool = False,
) -> tuple[list[Notification], str | None]:
    limit = max(1, min(limit, 50))
    q = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            audience_filter(role),
            Notification.hidden.is_(False),
            _archive_clause(archived),
            _filter_clause(filter_),
        )
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


def _archive_clause(archived: bool) -> ColumnElement[bool]:
    return Notification.archived_at.isnot(None) if archived else Notification.archived_at.is_(None)


def _filter_clause(name: str) -> ColumnElement[bool]:
    """SQL for an inbox chip. `unread` and `urgent` are the PRD's pinned-priority view."""
    if name == "unread":
        return Notification.read_at.is_(None)
    if name == "urgent":
        return Notification.type.in_(sorted(CRITICAL_TYPES))
    if name == "billing":
        return Notification.type.like("billing.%")
    if name in FILTER_TYPES:
        return Notification.type.in_(sorted(FILTER_TYPES[name]))
    return true()


def unread_count(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, role: MembershipRole
) -> int:
    return (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            audience_filter(role),
            Notification.hidden.is_(False),
            Notification.archived_at.is_(None),
            Notification.read_at.is_(None),
        )
        .count()
    )


def mark_read(
    db: Session,
    *,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    role: MembershipRole,
    notification_id: Any,
) -> Notification:
    row = (
        db.query(Notification)
        .filter_by(id=notification_id, user_id=user_id, startup_id=startup_id)
        .filter(audience_filter(role), Notification.hidden.is_(False))
        .first()
    )
    if row is None:
        raise NotFound()
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        db.flush()
    return row


def mark_all_read(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, role: MembershipRole
) -> int:
    n = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            audience_filter(role),
            Notification.hidden.is_(False),
            Notification.archived_at.is_(None),
            Notification.read_at.is_(None),
        )
        .update({Notification.read_at: datetime.now(UTC)}, synchronize_session=False)
    )
    db.flush()
    return n


def _owned(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, role: MembershipRole, nid: Any
) -> Notification:
    row = (
        db.query(Notification)
        .filter_by(id=nid, user_id=user_id, startup_id=startup_id)
        .filter(audience_filter(role), Notification.hidden.is_(False))
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def archive(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, role: MembershipRole, nid: Any
) -> Notification:
    """Archive one row (also marks it read, so it leaves the unread count for good)."""
    row = _owned(db, user_id=user_id, startup_id=startup_id, role=role, nid=nid)
    now = datetime.now(UTC)
    if row.archived_at is None:
        row.archived_at = now
    if row.read_at is None:
        row.read_at = now
    db.flush()
    return row


def restore(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, role: MembershipRole, nid: Any
) -> Notification:
    row = _owned(db, user_id=user_id, startup_id=startup_id, role=role, nid=nid)
    row.archived_at = None
    db.flush()
    return row


def counts(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, role: MembershipRole
) -> dict[str, int]:
    """Counts behind the chip badges and the "N need you today" line, from one grouped query
    over the visible, non-archived rows."""
    rows = (
        db.query(Notification.type, Notification.read_at.is_(None), func.count())
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            audience_filter(role),
            Notification.hidden.is_(False),
            Notification.archived_at.is_(None),
        )
        .group_by(Notification.type, Notification.read_at.is_(None))
        .all()
    )
    out = dict.fromkeys(
        ("all", "unread", "urgent", "urgent_unread", "mentions", "ai", "deadlines", "billing"), 0
    )
    for type_, is_unread, n in rows:
        out["all"] += n
        if is_unread:
            out["unread"] += n
        if is_critical(type_):
            out["urgent"] += n
            if is_unread:
                out["urgent_unread"] += n
        for name, types in FILTER_TYPES.items():
            if type_ in types:
                out[name] += n
        if type_.startswith("billing."):
            out["billing"] += n
    out["archived"] = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.startup_id == startup_id,
            audience_filter(role),
            Notification.hidden.is_(False),
            Notification.archived_at.isnot(None),
        )
        .count()
    )
    return out


def serialize_notification(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "data": n.data,
        "read": n.read_at is not None,
        "archived": n.archived_at is not None,
        "priority": "critical" if is_critical(n.type) else "normal",
        "module": module_for(n.type),
        "created_at": n.created_at.isoformat(),
    }
