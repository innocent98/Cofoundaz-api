from app.db.models.enums import RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.services.health_score.recommendations import generate_recommendations
from tests.factories import create_recommendation, create_startup, create_user

WEAK = {"product": 30, "market": 30, "money": 30, "legal": 30, "team": 30}
STRONG = {"product": 90, "market": 90, "money": 90, "legal": 90, "team": 90}


def test_generates_for_weak_dimensions(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, WEAK)
    db.flush()
    rows = db.query(HealthRecommendation).filter_by(startup_id=s.id).all()
    assert len(rows) == 10  # 2 catalog entries per dimension, all below threshold
    assert all(r.priority >= 1 for r in rows)


def test_no_recommendations_when_all_strong(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, STRONG)
    db.flush()
    assert db.query(HealthRecommendation).filter_by(startup_id=s.id).count() == 0


def test_dedupe_by_key_on_regeneration(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, WEAK)
    db.flush()
    generate_recommendations(db, s.id, WEAK)
    db.flush()
    assert db.query(HealthRecommendation).filter_by(startup_id=s.id).count() == 10  # no dupes


def test_never_resurrect_user_dismissed(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_recommendation(db, s, key="money.runway_model", status=RecommendationStatus.dismissed)
    generate_recommendations(db, s.id, WEAK)
    db.flush()
    row = db.query(HealthRecommendation).filter_by(startup_id=s.id, key="money.runway_model").one()
    assert row.status == RecommendationStatus.dismissed  # not revived to pending


def test_accepted_left_untouched(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_recommendation(
        db, s, key="money.runway_model", status=RecommendationStatus.accepted, priority=9
    )
    generate_recommendations(db, s.id, WEAK)
    db.flush()
    row = db.query(HealthRecommendation).filter_by(startup_id=s.id, key="money.runway_model").one()
    assert row.status == RecommendationStatus.accepted


def test_recovered_pending_is_deleted(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    generate_recommendations(db, s.id, WEAK)
    db.flush()
    generate_recommendations(db, s.id, STRONG)
    db.flush()  # everything recovered
    remaining = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=s.id, status=RecommendationStatus.pending)
        .count()
    )
    assert remaining == 0  # unacted pendings removed
