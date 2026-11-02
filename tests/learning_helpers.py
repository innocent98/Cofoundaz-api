"""Shared builders for the Academy tests."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.enums import (
    AssessmentStatus,
    AssessmentType,
    ExperimentType,
    InterviewVerdict,
    StartupStage,
)
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.models.validation import Experiment, Interview
from tests.factories import create_startup, create_user


def founder_and_startup(
    db: Session, stage: StartupStage | None = StartupStage.validation, name: str = "Ada Founder"
) -> tuple[User, Startup]:
    user = create_user(db)
    user.profile.full_name = name
    startup = create_startup(db, owner=user, stage=stage)
    db.flush()
    return user, startup


def add_assessment(db: Session, startup: Startup, scores: dict[str, int]) -> None:
    a = Assessment(
        startup_id=startup.id,
        type=AssessmentType.initial,
        status=AssessmentStatus.completed,
        bank_version="v1",
        created_by=startup.created_by,
        completed_at=datetime.now(UTC),
    )
    db.add(a)
    db.flush()
    db.add(
        AssessmentResult(
            assessment_id=a.id,
            dimension_scores=scores,
            overall_provisional=round(sum(scores.values()) / len(scores)),
            narrative="n",
        )
    )
    db.flush()


def add_interview_and_experiment(db: Session, startup: Startup) -> None:
    db.add(
        Interview(
            startup_id=startup.id,
            interviewee="Tola",
            held_on=date.today(),
            verdict=InterviewVerdict.supports,
        )
    )
    db.add(Experiment(startup_id=startup.id, name="Smoke", type=ExperimentType.smoke_test))
    db.flush()


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"
