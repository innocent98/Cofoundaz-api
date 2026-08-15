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
        "workspace.created", {"startup_id": str(startup.id), "created_by": str(user.id)}
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
        "notes": profile.notes,
        "invites": [
            {"email": i.email, "role": i.role.value, "status": i.status.value} for i in invites
        ],
    }
