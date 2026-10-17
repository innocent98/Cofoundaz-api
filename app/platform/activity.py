import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.activity import ActivityLog
from app.db.models.user import UserProfile


def serialize_activity(row: ActivityLog, actor_name: str | None) -> dict[str, Any]:
    """One activity item, exactly as `GET /dashboard/activity` returns it, and as the live
    `activity.created` stream event carries it (so the FE can prepend it unchanged)."""
    actor = (
        {"id": str(row.actor_user_id), "name": actor_name}
        if row.actor_user_id is not None
        else None
    )
    return {
        "id": str(row.id),
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": str(row.entity_id) if row.entity_id else None,
        "summary": row.summary,
        "meta": row.meta,
        "actor": actor,
        "created_at": row.created_at.isoformat(),
    }


def write_activity(
    db: Session,
    *,
    startup_id: uuid.UUID,
    action: str,
    summary: str,
    actor_user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> ActivityLog:
    row = ActivityLog(
        startup_id=startup_id,
        actor_user_id=actor_user_id,
        action=action,
        summary=summary,
        entity_type=entity_type,
        entity_id=entity_id,
        meta=meta,
    )
    db.add(row)
    db.flush()
    db.refresh(row, ["created_at"])  # server default: load it for the live event
    actor_name = (
        db.query(UserProfile.full_name).filter(UserProfile.user_id == actor_user_id).scalar()
        if actor_user_id is not None
        else None
    )
    # Published to the workspace's activity channel only after the commit (and dropped on
    # rollback) by the session listeners in app/db/session.py: PRD workspace.{id}.activity.
    db.info.setdefault("pending_activity", []).append(
        (
            str(startup_id),
            {"event": "activity.created", "item": serialize_activity(row, actor_name)},
        )
    )
    return row
