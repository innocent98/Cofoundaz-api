from datetime import UTC, datetime

from app.db.models.enums import JobStatus, StartupStage
from app.db.models.job import Job
from app.db.models.roadmap import Roadmap
from app.services.onboarding.complete import complete_onboarding
from tests.factories import create_startup, create_user


def test_completing_onboarding_generates_roadmap_inline(db):
    user = create_user(db, email_verified_at=datetime.now(UTC))
    user.profile.full_name = "Amara"
    startup = create_startup(
        db, owner=user, name="Acme", industry="fintech", stage=StartupStage.validation
    )
    startup.profile.goals = ["ship_mvp"]
    db.flush()

    result = complete_onboarding(db, startup, user)

    assert db.query(Roadmap).filter_by(startup_id=startup.id).count() == 1
    assert len(result["job_ids"]) == 2  # roadmap + healthscore job ids still present for FE parity

    roadmap_job = db.query(Job).filter_by(id=result["job_ids"][0]).one()
    assert roadmap_job.type == "roadmap.generate"
    assert roadmap_job.status == JobStatus.succeeded
