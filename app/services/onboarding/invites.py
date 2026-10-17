import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, InviteEmailMismatch, NotFound, TokenInvalid
from app.core.logger import log
from app.db.models.enums import InvitationStatus, MembershipRole, MembershipStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.email import EmailMessage, get_email_sender
from app.platform.events import event_bus
from app.services.auth.emails import invitation_email
from app.services.auth.sessions import hash_token

_TTL = timedelta(days=14)
# Outbound-email abuse guard: at most this many invitations per workspace per rolling 24h,
# across both onboarding and "+ Invite" (each one sends an email we sign as Cofoundaz).
DAILY_INVITE_CAP = 50


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


@dataclass
class InviteBatch:
    """Result of `create_invitations`. The emails sit in `outbox` until the caller commits:
    an email must never go out for an invitation that then rolls back (that would also let a
    failing batch slip past the daily cap, which counts committed rows)."""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    outbox: list[EmailMessage] = field(default_factory=list)

    def response(self, undelivered: list[str]) -> dict[str, Any]:
        return {"created": self.created, "skipped": self.skipped, "undelivered": undelivered}


def deliver_invitations(batch: InviteBatch) -> list[str]:
    """Send the batch's emails. Call only AFTER the invitations are committed. A failure for one
    recipient is logged and reported, never raised: the invitation exists and can be re-sent."""
    undelivered = []
    sender = get_email_sender()
    for msg in batch.outbox:
        try:
            sender.send(msg)
        except Exception as exc:  # noqa: BLE001 - one bad address must not fail the rest
            log.warning(f"[invites] email to {msg.to} failed: {exc}")
            undelivered.append(msg.to)
    return undelivered


def create_invitations(
    db: Session, startup: Startup, inviter: User, items: list[dict]
) -> InviteBatch:
    batch = InviteBatch()
    # Serialize invitation batches per workspace so concurrent requests can't each pass the
    # daily-cap check below (row lock held until the caller's commit).
    db.query(Startup.id).filter(Startup.id == startup.id).with_for_update().one()
    sent_today = (
        db.query(Invitation)
        .filter(
            Invitation.startup_id == startup.id,
            Invitation.created_at >= datetime.now(UTC) - timedelta(days=1),
        )
        .count()
    )
    if sent_today + len(items) > DAILY_INVITE_CAP:
        raise AppError(
            "INVITE_LIMIT_REACHED",
            "You've sent a lot of invitations today. Try again tomorrow.",
            429,
        )
    # Prod runs with autoflush=False (app/db/session.py), so a per-item DB query
    # below would not see an Invitation added earlier in this same loop. Track
    # emails already handled in this call so repeats within one request are caught
    # even without a flush.
    handled_this_call: set[str] = set()
    inviter_name = inviter.profile.full_name if inviter.profile else None
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
            batch.skipped.append(email)
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
        batch.outbox.append(
            invitation_email(
                email,
                raw,
                startup_name=startup.name,
                inviter_name=inviter_name,
                role=role.value,
            )
        )
        event_bus.publish(
            db,
            "workspace.member.invited",
            {"startup_id": str(startup.id), "email": email, "role": role.value},
        )
        batch.created.append(email)
    db.flush()
    return batch


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
