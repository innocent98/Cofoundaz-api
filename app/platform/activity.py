import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.activity import ActivityLog


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
    return row
