import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import InviteEmailMismatch, NotFound, TokenInvalid
from app.db.models.enums import InvitationStatus, MembershipRole, MembershipStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.email import EmailMessage, get_email_sender
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token

_TTL = timedelta(days=14)


def _is_active_member_email(db: Session, startup: Startup, email: str) -> bool:
    return (
        db.query(Membership)
        .join(User, User.id == Membership.user_id)
        .filter(
            Membership.startup_id == startup.id,
            Membership.status == MembershipStatus.active,
            User.email == email,
        )
        .first()
        is not None
    )


def create_invitations(
    db: Session, startup: Startup, inviter: User, items: list[dict]
) -> dict[str, Any]:
    created, skipped = [], []
    # Prod runs with autoflush=False (app/db/session.py), so a per-item DB query
    # below would not see an Invitation added earlier in this same loop. Track
    # emails already handled in this call so repeats within one request are caught
    # even without a flush.
    handled_this_call: set[str] = set()
    for item in items:
        email, role = item["email"], MembershipRole(item["role"])
        email_key = email.lower()
        pending = (
            db.query(Invitation)
            .filter(
                Invitation.startup_id == startup.id,
                Invitation.email == email,
                Invitation.status == InvitationStatus.pending,
            )
            .first()
        )
        if (
            email_key in handled_this_call
            or pending is not None
            or _is_active_member_email(db, startup, email)
        ):
            skipped.append(email)
            continue
        handled_this_call.add(email_key)
        raw = secrets.token_urlsafe(32)
        db.add(
            Invitation(
                startup_id=startup.id,
                email=email,
                role=role,
                token_hash=hash_token(raw),
                invited_by=inviter.id,
                expires_at=datetime.now(UTC) + _TTL,
            )
        )
        get_email_sender().send(
            EmailMessage(
                to=email,
                subject=f"You're invited to join {startup.name or 'a startup'} on Cofoundaz",
                html=f"<p>Accept your invitation — token: <code>{raw}</code></p>",
            )
        )
        event_bus.publish(
            db,
            "workspace.member.invited",
            {"startup_id": str(startup.id), "email": email, "role": role.value},
        )
        created.append(email)
    db.flush()
    return {"created": created, "skipped": skipped}


def preview_invitation(db: Session, token: str) -> dict[str, Any]:
    inv = db.query(Invitation).filter(Invitation.token_hash == hash_token(token)).first()
    if inv is None:
        raise NotFound()
    if inv.status != InvitationStatus.pending or inv.expires_at < datetime.now(UTC):
        # Same 404 as an unknown token — don't leak whether a token existed but expired
        # or was already used (matches accept_invitation's pending/expiry check).
        raise NotFound()
    startup = db.query(Startup).filter(Startup.id == inv.startup_id).first()
    inviter = db.query(UserProfile).filter(UserProfile.user_id == inv.invited_by).first()
    return {
        "startup_name": startup.name if startup else None,
        "role": inv.role.value,
        "inviter_name": inviter.full_name if inviter else None,
        "email": inv.email,
        "status": inv.status.value,
    }


def accept_invitation(db: Session, user: User, token: str) -> Membership:
    inv = db.query(Invitation).filter(Invitation.token_hash == hash_token(token)).first()
    now = datetime.now(UTC)
    if inv is None or inv.status != InvitationStatus.pending or inv.expires_at < now:
        raise TokenInvalid()
    if user.email.lower() != inv.email.lower():
        raise InviteEmailMismatch()
    existing = (
        db.query(Membership)
        .filter(Membership.user_id == user.id, Membership.startup_id == inv.startup_id)
        .first()
    )
    if existing is None:
        existing = Membership(
            user_id=user.id,
            startup_id=inv.startup_id,
            role=inv.role,
            status=MembershipStatus.active,
            invited_by=inv.invited_by,
            joined_at=now,
        )
        db.add(existing)
    inv.status = InvitationStatus.accepted
    inv.accepted_at = now
    inv.accepted_user_id = user.id
    db.flush()
    event_bus.publish(
        db,
        "workspace.member.joined",
        {"startup_id": str(inv.startup_id), "user_id": str(user.id), "role": inv.role.value},
    )
    return existing
