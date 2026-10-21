import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, true
from sqlalchemy.orm import Session

from app.db.models.activity import ActivityLog
from app.db.models.enums import (
    FINANCE_ROLES,
    MARKETING_ROLES,
    MISSION_ROLES,
    SALES_ROLES,
    MembershipRole,
)
from app.db.models.user import UserProfile

# Who may read an activity row (D-MIS-8 + #153). An action's namespace is its first dotted
# segment ("mission.task.added" -> "mission"); a row is restricted by its namespace OR its
# entity_type, so a mission task can't surface through some other action name.
_RESTRICTED_NAMESPACES: dict[str, frozenset[MembershipRole]] = {
    "mission": MISSION_ROLES,
    "finance": FINANCE_ROLES,
    "marketing": MARKETING_ROLES,
    "sales": SALES_ROLES,
}
_RESTRICTED_ENTITIES: dict[str, frozenset[MembershipRole]] = {
    "mission_task": MISSION_ROLES,
    "transaction": FINANCE_ROLES,
    "invoice": FINANCE_ROLES,
    "expense": FINANCE_ROLES,
    "budget": FINANCE_ROLES,
    "campaign": MARKETING_ROLES,
    "post": MARKETING_ROLES,
    "keyword": MARKETING_ROLES,
    "segment": MARKETING_ROLES,
    "sales_stage": SALES_ROLES,
    "sales_settings": SALES_ROLES,
}
# Open to every role: #153 kept health score, roadmap, business and document data open;
# assessment.completed is already shown to every role as a health-score history marker, and
# member.joined is workspace membership, which every member can see.
_OPEN_NAMESPACES: frozenset[str] = frozenset(
    {"healthscore", "roadmap", "business", "document", "assessment", "member", "workspace"}
)
# A namespace in neither list fails closed: only the core team sees it until it is classified.
_UNCLASSIFIED_ROLES: frozenset[MembershipRole] = frozenset(
    {MembershipRole.founder, MembershipRole.team_member}
)


def activity_visible(role: MembershipRole, action: str, entity_type: str | None) -> bool:
    """Python twin of `activity_visibility_filter`, for the live `activity.created` stream."""
    namespace = action.split(".", 1)[0]
    if namespace in _RESTRICTED_NAMESPACES:
        allowed = role in _RESTRICTED_NAMESPACES[namespace]
    elif namespace in _OPEN_NAMESPACES:
        allowed = True
    else:
        allowed = role in _UNCLASSIFIED_ROLES
    entity_roles = _RESTRICTED_ENTITIES.get(entity_type) if entity_type else None
    return allowed and (entity_roles is None or role in entity_roles)


def activity_visibility_filter(role: MembershipRole) -> ColumnElement[bool]:
    """SQL predicate for the rows `role` may read. Applied in the query (not after it) so
    `LIMIT` counts visible rows only: cursor pages stay full and no row is fetched twice."""
    namespace = func.split_part(ActivityLog.action, ".", 1)
    hidden_ns = [ns for ns, roles in _RESTRICTED_NAMESPACES.items() if role not in roles]
    if role in _UNCLASSIFIED_ROLES:
        ns_ok = namespace.notin_(hidden_ns) if hidden_ns else true()
    else:
        visible_ns = sorted(_OPEN_NAMESPACES) + [
            ns for ns, roles in _RESTRICTED_NAMESPACES.items() if role in roles
        ]
        ns_ok = namespace.in_(visible_ns)
    hidden_entities = [e for e, roles in _RESTRICTED_ENTITIES.items() if role not in roles]
    if not hidden_entities:
        return ns_ok
    return and_(
        ns_ok,
        or_(ActivityLog.entity_type.is_(None), ActivityLog.entity_type.notin_(hidden_entities)),
    )


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
