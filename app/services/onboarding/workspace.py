import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import OnboardingAlreadyComplete
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.startup import Startup, StartupProfile
from app.db.models.user import User
from app.platform.events import event_bus


def joined_workspace(db: Session, user: User, preferred_id: uuid.UUID | None) -> Startup | None:
    """An onboarded workspace `user` joined by invitation (they did not create it): the one
    named by `preferred_id` (the X-Workspace-Id the app sends) when they belong to it, else
    the earliest joined if they own no workspace. None means "use the owner rules"."""
    rows = (
        db.query(Startup)
        .join(Membership, Membership.startup_id == Startup.id)
        .join(StartupProfile, StartupProfile.startup_id == Startup.id)
        .filter(
            Membership.user_id == user.id,
            Membership.status == MembershipStatus.active,
            Startup.created_by != user.id,
            Startup.deleted_at.is_(None),
            StartupProfile.onboarding_completed_at.is_not(None),
        )
        .order_by(Membership.created_at, Membership.id)
        .all()
    )
    for startup in rows:
        if startup.id == preferred_id:
            return startup
    if not rows:
        return None
    # No usable header: answer as a member only when they own no workspace of their own.
    return None if _own_workspace(db, user) is not None else rows[0]


def _own_workspace(db: Session, user: User) -> Startup | None:
    return (
        db.query(Startup)
        .filter(Startup.created_by == user.id, Startup.deleted_at.is_(None))
        .order_by(Startup.created_at.desc())
        .first()
    )


def is_untouched_draft(startup: Startup) -> bool:
    """A workspace created for onboarding that nobody started filling in."""
    profile = startup.profile
    return (
        startup.name is None
        and profile is not None
        and profile.onboarding_completed_at is None
        and profile.onboarding_step <= 1
    )


def serialize_member_state(startup: Startup) -> dict[str, Any]:
    """Onboarding state of a workspace the caller joined: it is complete, and the founder's
    own onboarding answers and invite list stay private."""
    return {
        "step": startup.profile.onboarding_step,
        "completed": True,
        "assessment_pending": False,
        "founder_profile": None,
        "startup": {"id": str(startup.id), "name": startup.name},
        "goals": [],
        "ai_panel": None,
        "notes": None,
        "invites": [],
    }


def resolve_or_create_workspace(db: Session, user: User) -> Startup:
    existing = (
        db.query(Startup)
        .filter(Startup.created_by == user.id, Startup.deleted_at.is_(None))
        .order_by(Startup.created_at.desc())
        .first()
    )
    if existing is not None:
        return existing

    startup = Startup(name=None, created_by=user.id)
    startup.profile = StartupProfile()
    db.add(startup)
    db.flush()
    db.add(
        Membership(
            user_id=user.id,
            startup_id=startup.id,
            role=MembershipRole.founder,
            status=MembershipStatus.active,
            joined_at=datetime.now(UTC),
        )
    )
    db.flush()
    event_bus.publish(
        db, "workspace.created", {"startup_id": str(startup.id), "created_by": str(user.id)}
    )
    return startup


def ensure_draft(startup: Startup) -> None:
    if startup.profile.onboarding_completed_at is not None:
        raise OnboardingAlreadyComplete()


def serialize_state(db: Session, startup: Startup, user: User) -> dict[str, Any]:
    profile = startup.profile
    founder_profile = user.profile
    invites = db.query(Invitation).filter_by(startup_id=startup.id).all()
    return {
        "step": profile.onboarding_step,
        "completed": profile.onboarding_completed_at is not None,
        "assessment_pending": profile.assessment_pending,
        "founder_profile": {
            "full_name": founder_profile.full_name if founder_profile else None,
            "role_title": founder_profile.role_title if founder_profile else None,
            "country": founder_profile.country if founder_profile else None,
            "phone": founder_profile.phone if founder_profile else None,
            "how_heard": founder_profile.how_heard if founder_profile else None,
        },
        "startup": {
            "id": str(startup.id),
            "name": startup.name,
            "description": startup.description,
            "website": startup.website,
            "logo_url": startup.logo_url,
            "industry": startup.industry,
            "business_model": startup.business_model.value if startup.business_model else None,
            "stage": startup.stage.value if startup.stage else None,
        },
        "goals": profile.goals or [],
        "ai_panel": profile.ai_panel,
        "notes": profile.notes,
        "invites": [
            {"email": i.email, "role": i.role.value, "status": i.status.value} for i in invites
        ],
    }
