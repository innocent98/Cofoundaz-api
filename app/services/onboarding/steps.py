from sqlalchemy.orm import Session

from app.db.models.startup import Startup
from app.db.models.user import User
from app.schemas.onboarding import OnboardingStatePatch

_PROFILE_FIELDS = ("full_name", "role_title", "country", "phone", "how_heard")
_STARTUP_FIELDS = ("name", "description", "website", "industry", "business_model", "stage")
_SPROFILE_FIELDS = ("goals", "notes")


def apply_step(db: Session, startup: Startup, user: User, patch: OnboardingStatePatch) -> None:
    data = patch.model_dump(exclude_unset=True, exclude={"step"})
    for f in _PROFILE_FIELDS:
        if f in data:
            setattr(user.profile, f, data[f])
    for f in _STARTUP_FIELDS:
        if f in data:
            setattr(startup, f, data[f])
    for f in _SPROFILE_FIELDS:
        if f in data:
            setattr(startup.profile, f, data[f])
    startup.profile.onboarding_step = max(startup.profile.onboarding_step, patch.step)
    db.flush()
