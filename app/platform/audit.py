import uuid

from sqlalchemy.orm import Session

from app.db.models.audit import AuditLog


def write_audit(
    db: Session,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    on_behalf_of_user_id: uuid.UUID | None = None,
    startup_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    row = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        on_behalf_of_user_id=on_behalf_of_user_id,
        startup_id=startup_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before_hash=before_hash,
        after_hash=after_hash,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(row)
    db.flush()
    return row
