from datetime import UTC, datetime, timedelta

from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus
from app.db.models.health_score import HealthScore, HealthScoreHistory, HealthSignal
from app.platform.events import event_bus
from app.services.health_score.service import recompute_health_score
from tests.factories import create_assessment, create_history, create_startup, create_user


def _complete_with_scores(db, startup, scores):
    a = create_assessment(db, startup, status=AssessmentStatus.completed)
    db.add(AssessmentResult(assessment_id=a.id, dimension_scores=scores,
                            overall_provisional=sum(scores.values()) // 5, narrative="n"))
    db.flush()
    return a


def test_recompute_writes_score_signals_history(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _complete_with_scores(db, s, {"product": 80, "market": 60, "money": 40, "legal": 100, "team": 20})
    hs = recompute_health_score(db, s, trigger="assessment_complete")
    assert hs.score == 60
    assert hs.band == "healthy"
    assert db.query(HealthSignal).filter_by(startup_id=s.id).count() == 5
    assert db.query(HealthScoreHistory).filter_by(startup_id=s.id).count() == 1


def test_recompute_pending_when_no_assessment(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    assert recompute_health_score(db, s) is None
    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 0


def test_recompute_upserts_and_appends_history(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _complete_with_scores(db, s, {"product": 50, "market": 50, "money": 50, "legal": 50, "team": 50})
    recompute_health_score(db, s)
    recompute_health_score(db, s)
    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 1  # upsert, not duplicate
    assert db.query(HealthScoreHistory).filter_by(startup_id=s.id).count() == 2  # appended twice


def test_recompute_emits_updated_and_record(db, monkeypatch):
    # R3: a record requires a prior maximum to beat. Seed a lower prior history
    # point so the new (higher) score is a genuine record.
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_history(db, s, score=50, computed_at=datetime.now(UTC) - timedelta(days=2))
    _complete_with_scores(db, s, {"product": 70, "market": 70, "money": 70, "legal": 70, "team": 70})
    recompute_health_score(db, s)
    events = [e for e, _ in published]
    assert "healthscore.updated" in events
    assert "healthscore.record" in events


def test_first_score_is_not_a_record(db, monkeypatch):
    # R3: the first-ever score has no prior maximum, so it must NOT emit a record.
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    _complete_with_scores(db, s, {"product": 70, "market": 70, "money": 70, "legal": 70, "team": 70})
    recompute_health_score(db, s)
    events = [e for e, _ in published]
    assert "healthscore.updated" in events
    assert "healthscore.record" not in events


def test_recompute_dropped_event(db, monkeypatch):
    published = []
    monkeypatch.setattr(event_bus, "publish", lambda e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    # a prior history point 8 days ago at 80
    create_history(db, s, score=80, computed_at=datetime.now(UTC) - timedelta(days=8))
    _complete_with_scores(db, s, {"product": 60, "market": 60, "money": 60, "legal": 60, "team": 60})
    recompute_health_score(db, s)
    assert "healthscore.dropped" in [e for e, _ in published]
