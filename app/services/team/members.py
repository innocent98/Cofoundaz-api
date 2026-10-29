"""Member and invitation management for the Team page (light version, 2026-10-29): change a role,
remove a member, leave a workspace, list / resend / revoke invitations, the Audit tab and the
comments index.

Removal flips `memberships.status` to `removed` (the row stays, so past activity stays
attributed). `require_workspace` only resolves ACTIVE memberships, so the removed person loses
every route and the realtime stream at once. Their Documents grants in this workspace are revoked
in the same transaction (handoff 0b-41).
"""

import base64
import binascii
import csv
import io
import secrets
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, Forbidden, NotFound
from app.db.models.audit import AuditLog
from app.db.models.auth import AuthSession
from app.db.models.comment import Comment
from app.db.models.document import DocumentGrant, DocumentShare
from app.db.models.enums import InvitationStatus, MembershipRole, MembershipStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.services.auth.emails import invitation_email
from app.services.auth.sessions import hash_token
from app.services.team import service as team_svc

INVITE_TTL = timedelta(days=14)
# What the invite modal offers; investors are not assignable here (they arrive via Funding).
ASSIGNABLE = frozenset(MembershipRole) - {MembershipRole.investor}
AUDIT_PREFIXES = ("team.", "workspace.")


def _names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str | None, str]]:
    if not ids:
        return {}
    rows = (
        db.query(User.id, UserProfile.full_name, User.email)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(User.id.in_(ids))
        .all()
    )
    return {r[0]: (r[1], r[2]) for r in rows}


# ----------------------------------------------------------------------
# Members
# ----------------------------------------------------------------------


def _active_member(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    m = (
        db.query(Membership)
        .filter_by(startup_id=startup_id, user_id=user_id, status=MembershipStatus.active)
        .with_for_update()
        .first()
    )
    if m is None:
        raise NotFound()
    return m


def _founder_count(db: Session, startup_id: uuid.UUID) -> int:
    return (
        db.query(Membership)
        .filter_by(
            startup_id=startup_id, role=MembershipRole.founder, status=MembershipStatus.active
        )
        .count()
    )


def _lock_workspace_for(db: Session, startup_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """Serialise founder-level changes per workspace and re-check, under the lock, that the actor
    is still an active founder: two founders demoting or removing each other (or one leaving)
    at the same moment cannot leave the workspace without a founder."""
    db.query(Startup.id).filter(Startup.id == startup_id).with_for_update().one()
    still = (
        db.query(Membership.id)
        .filter_by(
            startup_id=startup_id,
            user_id=actor_id,
            role=MembershipRole.founder,
            status=MembershipStatus.active,
        )
        .first()
    )
    if still is None:
        raise Forbidden()


def change_role(
    db: Session, *, startup: Startup, actor: User, target_user_id: uuid.UUID, role: MembershipRole
) -> Membership:
    if target_user_id == actor.id:
        raise AppError("CANNOT_CHANGE_SELF", "You can't change your own role.", 409)
    if role not in ASSIGNABLE:
        raise AppError(
            "VALIDATION_ERROR",
            "Pick a role from the list.",
            422,
            field_errors=[{"field": "role", "message": "That role can't be assigned here."}],
        )
    _lock_workspace_for(db, startup.id, actor.id)
    m = _active_member(db, startup.id, target_user_id)
    if m.role != role:
        if m.role == MembershipRole.founder:
            # Only founders invite, so a demoted founder's pending invitations (which may carry
            # the founder role) are revoked rather than left to outlive their authority.
            _revoke_sent_invitations(db, startup.id, m.user_id)
        m.role = role
        db.flush()
        write_audit(
            db,
            "team.member.role_changed",
            actor_user_id=actor.id,
            startup_id=startup.id,
            entity_type="membership",
            entity_id=m.id,
        )
    return m


def revoke_document_grants(
    db: Session, *, startup_id: uuid.UUID, user: User, revoked_by: uuid.UUID
) -> int:
    """Revoke every live Documents grant this person holds in the workspace, by account or by
    the email it was addressed to (a grant outlives membership otherwise: handoff 0b-41)."""
    from sqlalchemy import or_

    n = (
        db.query(DocumentGrant)
        .filter(
            DocumentGrant.startup_id == startup_id,
            DocumentGrant.revoked_at.is_(None),
            or_(
                DocumentGrant.grantee_user_id == user.id,
                DocumentGrant.grantee_email == user.email.lower(),
            ),
        )
        .update(
            {DocumentGrant.revoked_at: datetime.now(UTC), DocumentGrant.revoked_by_id: revoked_by},
            synchronize_session=False,
        )
    )
    db.flush()
    return n


def _revoke_sent_invitations(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID) -> int:
    n = (
        db.query(Invitation)
        .filter(
            Invitation.startup_id == startup_id,
            Invitation.invited_by == user_id,
            Invitation.status == InvitationStatus.pending,
        )
        .update({Invitation.status: InvitationStatus.revoked}, synchronize_session=False)
    )
    db.flush()
    return n


def _end_membership(
    db: Session, *, startup: Startup, m: Membership, actor_id: uuid.UUID, action: str
) -> dict[str, int]:
    user = db.get(User, m.user_id)
    assert user is not None
    m.status = MembershipStatus.removed
    db.flush()
    grants = revoke_document_grants(db, startup_id=startup.id, user=user, revoked_by=actor_id)
    # Links they handed out must die with their membership, and so must invitations they sent
    # (a removed founder could otherwise still bring people in).
    now = datetime.now(UTC)
    shares = (
        db.query(DocumentShare)
        .filter(
            DocumentShare.startup_id == startup.id,
            DocumentShare.shared_by_id == m.user_id,
            DocumentShare.revoked_at.is_(None),
        )
        .update({DocumentShare.revoked_at: now}, synchronize_session=False)
    )
    invites = _revoke_sent_invitations(db, startup.id, m.user_id)
    write_audit(
        db,
        action,
        actor_user_id=actor_id,
        startup_id=startup.id,
        entity_type="membership",
        entity_id=m.id,
    )
    event_bus.publish(
        db,
        "workspace.member.removed",
        {"startup_id": str(startup.id), "user_id": str(m.user_id), "actor_id": str(actor_id)},
    )
    return {
        "document_grants_revoked": grants,
        "share_links_revoked": shares,
        "invitations_revoked": invites,
    }


def remove_member(
    db: Session, *, startup: Startup, actor: User, target_user_id: uuid.UUID
) -> dict[str, int]:
    if target_user_id == actor.id:
        raise AppError("CANNOT_REMOVE_SELF", "Use Leave workspace to remove yourself.", 409)
    _lock_workspace_for(db, startup.id, actor.id)
    m = _active_member(db, startup.id, target_user_id)
    return _end_membership(
        db, startup=startup, m=m, actor_id=actor.id, action="team.member.removed"
    )


def leave(db: Session, *, startup: Startup, user: User) -> dict[str, int]:
    # Serialise founders leaving at the same time so two co-founders cannot both walk out.
    db.query(Startup.id).filter(Startup.id == startup.id).with_for_update().one()
    m = _active_member(db, startup.id, user.id)
    if m.role == MembershipRole.founder and _founder_count(db, startup.id) <= 1:
        raise AppError(
            "LAST_FOUNDER",
            "You are the only founder. Make someone else a founder before you leave.",
            409,
        )
    return _end_membership(db, startup=startup, m=m, actor_id=user.id, action="team.member.left")


def list_members_detailed(
    db: Session, startup: Startup, *, include_last_active: bool
) -> list[dict[str, Any]]:
    """Active members with join date and (founders only) last sign-in, for the Members tab.
    The sign-in time is account-wide, so it is not shown to the other roles."""
    base = team_svc.list_members(db, startup)
    ids = {uuid.UUID(m["user_id"]) for m in base}
    joined: dict[uuid.UUID, datetime | None] = dict(
        db.query(Membership.user_id, Membership.joined_at)
        .filter(Membership.startup_id == startup.id, Membership.user_id.in_(ids))
        .tuples()
        .all()
    )
    last: dict[uuid.UUID, datetime] = (
        dict(
            db.query(AuthSession.user_id, func.max(AuthSession.created_at))
            .filter(AuthSession.user_id.in_(ids))
            .group_by(AuthSession.user_id)
            .tuples()
            .all()
        )
        if include_last_active
        else {}
    )
    out = []
    for m in base:
        uid = uuid.UUID(m["user_id"])
        j, la = joined.get(uid), last.get(uid)
        out.append(
            {
                **m,
                "joined_at": j.isoformat() if j else None,
                "last_active_at": la.isoformat() if la else None,
            }
        )
    return out


# ----------------------------------------------------------------------
# Invitations
# ----------------------------------------------------------------------


def list_invitations(db: Session, startup: Startup) -> list[dict[str, Any]]:
    """Invitations still waiting (pending, including ones past their expiry: those can be
    re-sent). Newest first."""
    rows = (
        db.query(Invitation)
        .filter(Invitation.startup_id == startup.id, Invitation.status == InvitationStatus.pending)
        .order_by(Invitation.created_at.desc())
        .all()
    )
    names = _names(db, {r.invited_by for r in rows})
    now = datetime.now(UTC)
    return [
        {
            "id": str(r.id),
            "email": r.email,
            "role": r.role.value,
            "invited_by": names.get(r.invited_by, (None, ""))[0],
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
            "expired": r.expires_at < now,
        }
        for r in rows
    ]


def _pending(db: Session, startup_id: uuid.UUID, invitation_id: uuid.UUID) -> Invitation:
    inv = (
        db.query(Invitation)
        .filter_by(id=invitation_id, startup_id=startup_id)
        .with_for_update()
        .first()
    )
    if inv is None:
        raise NotFound()
    if inv.status != InvitationStatus.pending:
        raise AppError(
            "INVITATION_NOT_PENDING", "This invitation was already accepted or revoked.", 409
        )
    return inv


def resend_invitation(
    db: Session, *, startup: Startup, actor: User, invitation_id: uuid.UUID
) -> Any:
    """New secret and a fresh 14 days; the old link stops working. Returns the email to send
    AFTER the caller commits (never email for a change that rolled back)."""
    inv = _pending(db, startup.id, invitation_id)
    raw = secrets.token_urlsafe(32)
    inv.token_hash = hash_token(raw)
    inv.expires_at = datetime.now(UTC) + INVITE_TTL
    db.flush()
    write_audit(
        db,
        "team.invitation.resent",
        actor_user_id=actor.id,
        startup_id=startup.id,
        entity_type="invitation",
        entity_id=inv.id,
    )
    name = actor.profile.full_name if actor.profile else None
    return invitation_email(
        inv.email, raw, startup_name=startup.name, inviter_name=name, role=inv.role.value
    )


def revoke_invitation(
    db: Session, *, startup: Startup, actor: User, invitation_id: uuid.UUID
) -> None:
    inv = _pending(db, startup.id, invitation_id)
    inv.status = InvitationStatus.revoked
    db.flush()
    write_audit(
        db,
        "team.invitation.revoked",
        actor_user_id=actor.id,
        startup_id=startup.id,
        entity_type="invitation",
        entity_id=inv.id,
    )


# ----------------------------------------------------------------------
# Audit tab
# ----------------------------------------------------------------------


def _audit_query(db: Session, startup_id: uuid.UUID) -> Any:
    from sqlalchemy import or_

    return db.query(AuditLog).filter(
        AuditLog.startup_id == startup_id,
        or_(*[AuditLog.action.like(f"{p}%") for p in AUDIT_PREFIXES]),
    )


def _audit_rows(db: Session, rows: list[AuditLog]) -> list[dict[str, Any]]:
    members = {r.entity_id for r in rows if r.entity_type == "membership" and r.entity_id}
    invites = {r.entity_id for r in rows if r.entity_type == "invitation" and r.entity_id}
    member_user: dict[uuid.UUID, uuid.UUID] = (
        dict(
            db.query(Membership.id, Membership.user_id)
            .filter(Membership.id.in_(members))
            .tuples()
            .all()
        )
        if members
        else {}
    )
    invite_email: dict[uuid.UUID, str] = (
        dict(
            db.query(Invitation.id, Invitation.email)
            .filter(Invitation.id.in_(invites))
            .tuples()
            .all()
        )
        if invites
        else {}
    )
    names = _names(
        db,
        {r.actor_user_id for r in rows if r.actor_user_id} | set(member_user.values()),
    )

    def label(uid: uuid.UUID | None) -> str | None:
        if uid is None or uid not in names:
            return None
        return names[uid][0] or names[uid][1]

    out = []
    for r in rows:
        target = None
        if r.entity_type == "membership" and r.entity_id in member_user:
            target = label(member_user[r.entity_id])
        elif r.entity_type == "invitation" and r.entity_id is not None:
            target = invite_email.get(r.entity_id)
        out.append(
            {
                "id": str(r.id),
                "action": r.action,
                "actor": label(r.actor_user_id),
                "target": target,
                "created_at": r.created_at.isoformat(),
            }
        )
    return out


def _encode_cursor(row: AuditLog) -> str:
    return base64.urlsafe_b64encode(f"{row.created_at.isoformat()}|{row.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def audit_log(
    db: Session, startup: Startup, *, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Newest first. The cursor is (time, id): rows written in one transaction (a batch of
    invitations) share a timestamp, so time alone would skip some at a page boundary."""
    q = _audit_query(db, startup.id)
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
    return _audit_rows(db, rows), next_cursor


def _csv_safe(cell: str) -> str:
    """A cell a spreadsheet could run as a formula gets a leading apostrophe: the first
    character that is not whitespace or a control/format character is one of = + - @, or the
    cell starts with a tab or carriage return."""
    if cell[:1] in ("\t", "\r"):
        return "'" + cell
    for ch in cell:
        if ch.isspace() or unicodedata.category(ch).startswith("C"):
            continue
        return "'" + cell if ch in "=+-@" else cell
    return cell


def audit_csv(db: Session, startup: Startup) -> str:
    rows = (
        _audit_query(db, startup.id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(5000)
        .all()
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["when", "actor", "action", "target"])
    for r in _audit_rows(db, rows):
        cells = [r["actor"] or "", r["action"], r["target"] or ""]
        w.writerow([r["created_at"], *[_csv_safe(c) for c in cells]])
    return buf.getvalue()


# ----------------------------------------------------------------------
# Comments index (the Comments tab)
# ----------------------------------------------------------------------


def comment_threads(
    db: Session, startup: Startup, user: User, *, filter_: str, limit: int = 30
) -> list[dict[str, Any]]:
    """Threads (one per entity and section) the caller wrote in or was mentioned in, newest
    activity first. `mentions` = threads that mention the caller; `unresolved` = threads with at
    least one open comment."""
    rows = (
        db.query(Comment)
        .filter(Comment.startup_id == startup.id)
        .order_by(Comment.created_at.desc())
        .limit(500)
        .all()
    )
    me = str(user.id)
    groups: dict[tuple[str, uuid.UUID, str | None], list[Comment]] = {}
    for c in rows:
        groups.setdefault((c.entity_type, c.entity_id, c.anchor), []).append(c)
    names = _names(db, {c.author_id for c in rows if c.author_id})
    out: list[dict[str, Any]] = []
    for (etype, eid, anchor), items in groups.items():
        involved = any(c.author_id == user.id or me in (c.mention_user_ids or []) for c in items)
        if not involved:
            continue
        mentions_me = any(me in (c.mention_user_ids or []) for c in items)
        open_n = sum(1 for c in items if c.resolved_at is None)
        if filter_ == "mentions" and not mentions_me:
            continue
        if filter_ == "unresolved" and open_n == 0:
            continue
        latest = items[0]
        try:
            context = team_svc._entity_in(db, startup, etype, eid)
        except NotFound:
            continue  # the entity was deleted
        author = names.get(latest.author_id, (None, "")) if latest.author_id else (None, "")
        out.append(
            {
                "entity_type": etype,
                "entity_id": str(eid),
                "anchor": anchor,
                "context": context,
                "comment_count": len(items),
                "unresolved_count": open_n,
                "mentions_me": mentions_me,
                "last_comment": {
                    "author": author[0] or author[1] or None,
                    "snippet": latest.body[: team_svc.SNIPPET],
                    "created_at": latest.created_at.isoformat(),
                },
            }
        )
    out.sort(key=lambda t: t["last_comment"]["created_at"], reverse=True)
    return out[:limit]
