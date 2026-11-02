"""Team read model + the generic comments thread (PRD 23.2), used by the Roadmap drawer first.

Comments are tenant-scoped and validated against the entity they're on (the entity must belong to
the caller's workspace). Mentions are explicit `mention_user_ids` (each must be an active member)
and notify via the `team.comment.mentioned` event.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, Forbidden, NotFound
from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.comment import Comment
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.events import event_bus

# Who may post: everyone but investors (PRD 05 access: BC suggest, M read shared). All may read.
COMMENTER_ROLES = frozenset(MembershipRole) - {MembershipRole.investor}
RESOLVER_ROLES = frozenset({MembershipRole.founder, MembershipRole.team_member})
ENTITY_TYPES = ("roadmap_milestone", "roadmap_task", "business_canvas", "business_record")
SNIPPET = 80


def list_members(db: Session, startup: Startup) -> list[dict[str, Any]]:
    """Active members for pickers and the directory: id, name, email, role."""
    rows = (
        db.query(Membership, User.email, UserProfile.full_name)
        .join(User, User.id == Membership.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(Membership.startup_id == startup.id, Membership.status == MembershipStatus.active)
        .order_by(UserProfile.full_name.asc().nullslast(), User.email.asc())
        .all()
    )
    return [
        {"user_id": str(m.user_id), "name": name, "email": email, "role": m.role.value}
        for m, email, name in rows
    ]


def _business_entity(
    db: Session, startup: Startup, entity_type: str, entity_id: uuid.UUID
) -> str | None:
    from app.services.business.export import CANVAS_TITLES, RECORD_TITLES

    if entity_type == "business_canvas":
        c = db.query(BusinessCanvas).filter_by(id=entity_id, startup_id=startup.id).first()
        return CANVAS_TITLES[c.type] if c is not None else None
    r = db.query(BusinessRecord).filter_by(id=entity_id, startup_id=startup.id).first()
    if r is None:
        return None
    return str(r.data.get("name") or RECORD_TITLES[r.kind])


def _check_anchor(db: Session, entity_type: str, entity_id: uuid.UUID, anchor: str | None) -> None:
    """An anchor is a canvas block key, valid only on business_canvas threads."""
    if anchor is None:
        return
    from app.services.business.canvas_defs import CANVAS_BLOCKS

    keys: set[str] = set()
    if entity_type == "business_canvas":
        canvas = db.get(BusinessCanvas, entity_id)
        keys = {b.key for b in CANVAS_BLOCKS[canvas.type]} if canvas is not None else set()
    if anchor not in keys:
        _bad_anchor()


def _bad_anchor() -> None:
    raise AppError(
        "VALIDATION_ERROR",
        "That section doesn't exist on this item.",
        422,
        field_errors=[{"field": "anchor", "message": "Not a section of this item."}],
    )


def _entity_in(db: Session, startup: Startup, entity_type: str, entity_id: uuid.UUID) -> str:
    """Return the entity's display title, or 404 if it isn't in this workspace."""
    if entity_type in ("business_canvas", "business_record"):
        title = _business_entity(db, startup, entity_type, entity_id)
        if title is None:
            raise NotFound()
        return title
    base = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup.id)
    )
    if entity_type == "roadmap_milestone":
        m = base.filter(RoadmapMilestone.id == entity_id).first()
        if m is not None:
            return m.title
    elif entity_type == "roadmap_task":
        t = (
            db.query(RoadmapTask)
            .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
            .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
            .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
            .filter(Roadmap.startup_id == startup.id, RoadmapTask.id == entity_id)
            .first()
        )
        if t is not None:
            return t.title
    raise NotFound()


def _names(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    if not user_ids:
        return {}
    rows = db.query(UserProfile.user_id, UserProfile.full_name).filter(
        UserProfile.user_id.in_(list(user_ids))
    )
    return dict(rows.tuples().all())


def serialize_comment(c: Comment, names: dict[uuid.UUID, str | None], viewer: uuid.UUID) -> dict:
    return {
        "id": str(c.id),
        "entity_type": c.entity_type,
        "entity_id": str(c.entity_id),
        "anchor": c.anchor,
        "author": (
            {"id": str(c.author_id), "name": names.get(c.author_id)} if c.author_id else None
        ),
        "body": c.body,
        "mention_user_ids": list(c.mention_user_ids or []),
        "created_at": c.created_at.isoformat(),
        "edited_at": c.edited_at.isoformat() if c.edited_at else None,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "is_mine": c.author_id == viewer,
    }


def list_comments(
    db: Session,
    startup: Startup,
    viewer: User,
    entity_type: str,
    entity_id: uuid.UUID,
    anchor: str | None = None,
) -> list[dict]:
    """Oldest first. Without `anchor`, the whole entity's thread (each row says its anchor)."""
    _entity_in(db, startup, entity_type, entity_id)
    q = db.query(Comment).filter(
        Comment.startup_id == startup.id,
        Comment.entity_type == entity_type,
        Comment.entity_id == entity_id,
    )
    if anchor is not None:
        q = q.filter(Comment.anchor == anchor)
    rows = q.order_by(Comment.created_at.asc(), Comment.id.asc()).all()
    names = _names(db, {c.author_id for c in rows if c.author_id})
    return [serialize_comment(c, names, viewer.id) for c in rows]


def _validate_mentions(db: Session, startup: Startup, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not ids:
        return []
    unique = list(dict.fromkeys(ids))
    active = {
        r[0]
        for r in db.query(Membership.user_id).filter(
            Membership.startup_id == startup.id,
            Membership.status == MembershipStatus.active,
            Membership.user_id.in_(unique),
        )
    }
    if len(active) != len(unique):
        raise AppError(
            "VALIDATION_ERROR",
            "You can only mention people in this workspace.",
            422,
            field_errors=[{"field": "mention_user_ids", "message": "Unknown member."}],
        )
    return unique


def create_comment(
    db: Session,
    startup: Startup,
    author: User,
    role: MembershipRole,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    body: str,
    mention_user_ids: list[uuid.UUID],
    anchor: str | None = None,
) -> dict:
    if role not in COMMENTER_ROLES:
        raise Forbidden()
    if anchor is not None and entity_type != "business_canvas":
        _bad_anchor()
    title = _entity_in(db, startup, entity_type, entity_id)
    _check_anchor(db, entity_type, entity_id, anchor)
    mentions = _validate_mentions(db, startup, mention_user_ids)
    c = Comment(
        startup_id=startup.id,
        entity_type=entity_type,
        entity_id=entity_id,
        anchor=anchor,
        author_id=author.id,
        body=body,
        mention_user_ids=[str(m) for m in mentions],
    )
    db.add(c)
    db.flush()
    author_name = (author.profile.full_name if author.profile else None) or "A teammate"
    snippet = body if len(body) <= SNIPPET else body[: SNIPPET - 1].rstrip() + "…"
    for uid in mentions:
        if uid == author.id:
            continue
        event_bus.publish(
            db,
            "team.comment.mentioned",
            {
                "startup_id": str(startup.id),
                "user_id": str(uid),
                "actor_id": str(author.id),
                "author_name": author_name,
                "snippet": snippet,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "entity_title": title,
                "comment_id": str(c.id),
            },
        )
    return serialize_comment(c, {author.id: author_name}, author.id)


def _own_comment(db: Session, startup: Startup, comment_id: uuid.UUID) -> Comment:
    c = db.query(Comment).filter_by(id=comment_id, startup_id=startup.id).first()
    # Document comments belong to the Documents routes (founder / team member, or a live grant);
    # this team API is open to every member, so it must never edit, delete or resolve them.
    if c is None or c.entity_type == "document":
        raise NotFound()
    return c


def edit_comment(
    db: Session, startup: Startup, user: User, comment_id: uuid.UUID, body: str
) -> dict:
    c = _own_comment(db, startup, comment_id)
    if c.author_id != user.id:
        raise Forbidden()
    c.body = body
    c.edited_at = datetime.now(UTC)
    db.flush()
    return serialize_comment(c, _names(db, {user.id}), user.id)


def delete_comment(db: Session, startup: Startup, user: User, comment_id: uuid.UUID) -> None:
    c = _own_comment(db, startup, comment_id)
    if c.author_id != user.id:
        raise Forbidden()
    db.delete(c)
    db.flush()


def set_resolved(
    db: Session,
    startup: Startup,
    user: User,
    role: MembershipRole,
    comment_id: uuid.UUID,
    resolved: bool,
) -> dict:
    c = _own_comment(db, startup, comment_id)
    if role not in RESOLVER_ROLES and c.author_id != user.id:
        raise Forbidden()
    c.resolved_at = datetime.now(UTC) if resolved else None
    db.flush()
    names = _names(db, {c.author_id} if c.author_id else set())
    return serialize_comment(c, names, user.id)
