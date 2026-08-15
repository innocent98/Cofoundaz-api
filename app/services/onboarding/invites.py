import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import InvitationStatus, MembershipRole, MembershipStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
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
            "workspace.member.invited",
            {"startup_id": str(startup.id), "email": email, "role": role.value},
        )
        created.append(email)
    db.flush()
    return {"created": created, "skipped": skipped}
