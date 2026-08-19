from datetime import UTC, datetime, timedelta

from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentStatus
from app.db.models.health_score import HealthScore, HealthScoreHistory, HealthSignal
from app.platform.events import event_bus
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.service import complete_assessment
from app.services.health_score.service import recompute_health_score
from tests.factories import (
    create_answer,
    create_assessment,
    create_history,
    create_startup,
    create_user,
)

# Chosen to skip every conditional follow-up question, same set used by
# tests/services/assessment/test_complete_concurrency.py, so the assessment is fully
# (and minimally) answered.
_MINIMAL_ANSWERS = [
    ("product_stage", "idea"),
    ("market_clarity", 3),
    ("market_research", "none"),
    ("has_revenue", "no"),
    ("runway_confidence", 3),
    ("incorporated", "no"),
    ("team_size", "solo"),
    ("team_confidence", 3),
]


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


def test_recompute_returns_fresh_score_after_upsert(db):
    # Regression test for identity-map staleness: `prev = db.query(HealthScore)...`
    # loads the row into the session identity map BEFORE the raw-core
    # on_conflict_do_update upsert runs. Without populate_existing on the final
    # requery, the returned object would keep the OLD score even though the DB
    # row was correctly updated.
    u = create_user(db)
    s = create_startup(db, owner=u)

    a1 = _complete_with_scores(db, s, {"product": 50, "market": 50, "money": 50, "legal": 50, "team": 50})
    a1.completed_at = datetime.now(UTC) - timedelta(minutes=5)
    db.flush()
    hs1 = recompute_health_score(db, s)
    assert hs1.score == 50

    a2 = _complete_with_scores(db, s, {"product": 90, "market": 90, "money": 90, "legal": 90, "team": 90})
    a2.completed_at = datetime.now(UTC)
    db.flush()
    hs2 = recompute_health_score(db, s)

    assert hs2.score == 90  # must reflect the NEW value, not the stale identity-mapped 50
    assert db.query(HealthScore).filter_by(startup_id=s.id).one().score == 90


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


def test_complete_assessment_triggers_health_score(db):
    # Integration test: complete_assessment (assessment service) must trigger the
    # Health Score recompute inline, in the same transaction, without going through a
    # job queue -- as of Module 06 the healthscore.recalculate stub job is retired.
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u, bank_version=ASSESSMENT_BANK.version)
    for key, value in _MINIMAL_ANSWERS:
        create_answer(db, a, question_key=key, value=value)
    db.flush()

    complete_assessment(db, a, s)

    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 1
    hs = db.query(HealthScore).filter_by(startup_id=s.id).one()
    assert hs.score is not None


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
