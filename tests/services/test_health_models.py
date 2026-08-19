import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import RecommendationStatus
from app.db.models.health_score import (
    HealthRecommendation,
    HealthScore,
    HealthScoreHistory,
    HealthSignal,
)
from tests.factories import create_startup, create_user


def test_health_score_one_per_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(HealthScore(startup_id=s.id, score=70, band="healthy",
                        dimension_scores={"product": 70}, source="assessment", config_version=1))
    db.flush()
    db.add(HealthScore(startup_id=s.id, score=80, band="thriving",
                        dimension_scores={"product": 80}, source="assessment", config_version=1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_recommendation_key_unique_per_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    for _ in range(2):
        db.add(HealthRecommendation(
            startup_id=s.id, dimension="money", key="money.runway_model",
            title="t", body="b", estimated_lift=8, effort="medium",
            status=RecommendationStatus.pending, priority=1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_signal_and_history_insert(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(HealthSignal(startup_id=s.id, dimension="money", key="assessment.money",
                        value=40, contribution=8.0, source_ref="assessment:x"))
    db.add(HealthScoreHistory(startup_id=s.id, score=70, dimension_scores={"money": 40},
                              delta=0, trigger="test", config_version=1))
    db.flush()
    assert db.query(HealthSignal).count() == 1
    assert db.query(HealthScoreHistory).count() == 1
