from app.db.models.enums import EnrichmentStatus, StartupStage
from app.db.models.job import Job
from app.db.models.learning import LearningRecommendation
from app.services.learning.service import get_or_create_recommendation_reason
from tests.factories import create_startup, create_user


def _jobs(db, startup_id):
    return db.query(Job).filter_by(type="ai.learning.recommendations", startup_id=startup_id).all()


def test_first_call_creates_generating_row_and_enqueues(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    db.flush()
    row = get_or_create_recommendation_reason(db, s.id, s.stage)
    assert row.status == EnrichmentStatus.generating
    assert row.stage == "build"
    assert row.reason == "Recommended for your build stage."
    assert len(_jobs(db, s.id)) == 1


def test_second_call_returns_existing_no_duplicate_job(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    db.flush()
    get_or_create_recommendation_reason(db, s.id, s.stage)
    get_or_create_recommendation_reason(db, s.id, s.stage)
    assert db.query(LearningRecommendation).filter_by(startup_id=s.id).count() == 1
    assert len(_jobs(db, s.id)) == 1  # covers the repeat/race re-select branch


def test_stage_change_regenerates_and_reenqueues(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    db.flush()
    row = get_or_create_recommendation_reason(db, s.id, s.stage)
    row.status = EnrichmentStatus.ready
    row.reason = "AI text"
    db.flush()
    s.stage = StartupStage.growth
    db.flush()
    row2 = get_or_create_recommendation_reason(db, s.id, s.stage)
    assert row2.stage == "growth"
    assert row2.status == EnrichmentStatus.generating
    assert row2.reason == "Recommended for your growth stage."
    assert len(_jobs(db, s.id)) == 2


def test_none_stage_uses_generic_fallback(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = None
    db.flush()
    row = get_or_create_recommendation_reason(db, s.id, None)
    assert row.stage is None
    assert row.reason == "Recommended to help you get started."
