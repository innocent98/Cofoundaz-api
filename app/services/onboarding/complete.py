from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import OnboardingIncomplete
from app.db.models.enums import JobStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.roadmap.service import generate_roadmap


def _gate(startup: Startup, user: User) -> list[dict[str, str]]:
    errs: list[dict[str, str]] = []
    if not (user.profile and user.profile.full_name):
        errs.append({"field": "full_name", "message": "Tell us your name (step 1)."})
    if not startup.name:
        errs.append({"field": "name", "message": "Name your startup (step 2)."})
    if not startup.industry:
        errs.append({"field": "industry", "message": "Pick your industry (step 3)."})
    if not startup.stage:
        errs.append({"field": "stage", "message": "Pick your stage (step 3)."})
    if not startup.profile.goals:
        errs.append({"field": "goals", "message": "Choose at least one goal (step 4)."})
    return errs


def complete_onboarding(db: Session, startup: Startup, user: User) -> dict[str, Any]:
    if startup.profile.onboarding_completed_at is not None:
        return {
            "completed": True,
            "assessment_pending": startup.profile.assessment_pending,
            "job_ids": [],
        }

    errs = _gate(startup, user)
    if errs:
        raise OnboardingIncomplete(field_errors=errs)

    startup.profile.onboarding_completed_at = datetime.now(UTC)
    startup.profile.assessment_pending = True

    generate_roadmap(db, startup, actor=user)
    j1 = job_dispatcher.enqueue(db, "roadmap.generate", {"startup_id": str(startup.id)}, startup.id)
    j1.status = JobStatus.succeeded
    j2 = job_dispatcher.enqueue(
        db, "healthscore.initialize", {"startup_id": str(startup.id)}, startup.id
    )

    event_bus.publish(
        "onboarding.completed", {"startup_id": str(startup.id), "user_id": str(user.id)}
    )
    event_bus.publish(
        "notification.onboarding_complete",
        {"startup_id": str(startup.id), "user_id": str(user.id)},
    )

    db.flush()
    return {
        "completed": True,
        "assessment_pending": True,
        "job_ids": [str(j1.id), str(j2.id)],
    }
