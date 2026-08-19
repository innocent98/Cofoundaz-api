from sqlalchemy.orm import Session

from app.db.models.startup import Startup
from app.db.models.user import User
from app.schemas.onboarding import OnboardingStatePatch

_PROFILE_FIELDS = ("full_name", "role_title", "country", "phone", "how_heard")
_STARTUP_FIELDS = ("name", "description", "website", "industry", "business_model", "stage")
_SPROFILE_FIELDS = ("goals", "notes")

# Columns that are NOT NULL at the DB level. An explicit `{"field": null}` in the patch
# (as opposed to omitting the field) survives `exclude_unset=True`, so writing it through
# would hit a NotNullViolation at flush. Treat an explicit null for these as a no-op
# instead of a 500.
_NON_NULLABLE = {"goals", "role_title"}


def apply_step(db: Session, startup: Startup, user: User, patch: OnboardingStatePatch) -> None:
    data = patch.model_dump(exclude_unset=True, exclude={"step"})
    for f in _PROFILE_FIELDS:
        if f in data and not (data[f] is None and f in _NON_NULLABLE):
            setattr(user.profile, f, data[f])
    for f in _STARTUP_FIELDS:
        if f in data and not (data[f] is None and f in _NON_NULLABLE):
            setattr(startup, f, data[f])
    for f in _SPROFILE_FIELDS:
        if f in data and not (data[f] is None and f in _NON_NULLABLE):
            setattr(startup.profile, f, data[f])
    startup.profile.onboarding_step = max(startup.profile.onboarding_step, patch.step)
    db.flush()
