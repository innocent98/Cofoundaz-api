"""Health Score live signals (PRD Module 06) — engine, blend, ramp, recs, triggers, readers."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.activity import ActivityLog
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import (
    AssessmentStatus,
    AssumptionStatus,
    InterviewVerdict,
    MembershipRole,
    RecommendationStatus,
    RiskLevel,
    RoadmapStatus,
    StartupStage,
)
from app.db.models.health_score import HealthRecommendation, HealthScore, HealthSignal
from app.db.models.validation import Assumption, Interview
from app.platform.events import DispatchingEventBus
from app.services.health_score import service as hs
from app.services.health_score import triggers
from app.services.health_score.live_signals import SIGNALS, SIGNALS_BY_KEY, read_signals
from app.services.notifications.registry import SPECS
from tests.factories import (
    create_assessment,
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)

BASE = {"product": 60, "market": 60, "money": 60, "legal": 60, "team": 60}


def _assessed(db, *, stage=StartupStage.validation, days_ago=30, scores=BASE, industry="Fintech"):
    u = create_user(db)
    s = create_startup(db, owner=u, stage=stage, industry=industry)
    create_membership(db, u, s, role=MembershipRole.founder)
    a = create_assessment(db, s, status=AssessmentStatus.completed)
    a.completed_at = datetime.now(UTC) - timedelta(days=days_ago)
    db.add(
        AssessmentResult(
            assessment_id=a.id, dimension_scores=scores, overall_provisional=60, narrative="n"
        )
    )
    db.flush()
    return u, s


def _reading(readings, key):
    return next(r for r in readings if r.signal.key == key)


def test_signal_table_is_well_formed():
    keys = [s.key for s in SIGNALS]
    assert len(keys) == len(set(keys))
    assert {s.dimension for s in SIGNALS} == {"product", "market", "money", "legal", "team"}
    for s in SIGNALS:
        assert len(s.targets) == len(StartupStage) and s.href.startswith("/")


def test_readings_score_against_stage_targets(db):
    _u, s = _assessed(db)
    r = create_roadmap(db, startup=s)
    m = create_milestone(db, phase=create_phase(db, roadmap=r))
    create_task(db, milestone=m, status=RoadmapStatus.done)
    create_task(db, milestone=m)
    for i, st in enumerate((AssumptionStatus.validated, AssumptionStatus.invalidated)):
        db.add(Assumption(startup_id=s.id, statement=f"a{i}", risk=RiskLevel.high, status=st))
    db.add(
        Interview(
            startup_id=s.id,
            interviewee="Ada",
            held_on=date.today(),
            verdict=InterviewVerdict.supports,
        )
    )
    db.flush()
    readings = read_signals(db, s)
    roadmap = _reading(readings, "product.roadmap_progress")
    assert (roadmap.value, roadmap.target, roadmap.score) == (50.0, 20, 100.0)  # capped
    tested = _reading(readings, "market.assumptions_tested")
    assert (tested.value, tested.target, tested.score) == (2.0, 5, 40.0)
    assert _reading(readings, "market.interviews_30d").value == 1.0
    assert _reading(readings, "team.members").value == 1.0
    assert "money.runway_live" not in {x.signal.key for x in readings}  # no finance data


def test_live_share_ramps_over_fourteen_days():
    now = datetime(2026, 10, 18, tzinfo=UTC)
    assert hs.live_share(None, now) == 0.0
    assert hs.live_share(now, now) == 0.0
    assert hs.live_share(now - timedelta(days=7), now) == 0.25
    assert hs.live_share(now - timedelta(days=40), now) == 0.5


def test_blend_mixes_baseline_and_live(db):
    _u, s = _assessed(db)
    readings = read_signals(db, s)
    blended = hs.blend(BASE, readings, 0.5)
    # Team: members 1/2 = 50; activity and streak 0 -> live avg 16.7 -> 60*0.5 + 16.7*0.5
    assert blended["team"] == round(60 * 0.5 + (50 + 0 + 0) / 3 * 0.5)
    assert hs.blend(BASE, readings, 0.0) == BASE


def test_recompute_moves_score_with_real_work_and_writes_contributions(db):
    _u, s = _assessed(db, days_ago=30)
    before = hs.recompute_health_score(db, s, trigger="test").score
    for i in range(5):
        db.add(
            Assumption(
                startup_id=s.id,
                statement=f"x{i}",
                risk=RiskLevel.low,
                status=AssumptionStatus.validated,
            )
        )
    db.flush()
    after = hs.recompute_health_score(db, s, trigger="test")
    assert after.score > before
    sig = db.query(HealthSignal).filter_by(startup_id=s.id, key="market.assumptions_tested").one()
    assert float(sig.value) == 5.0 and float(sig.contribution) > 0
    total = sum(float(x.contribution) for x in db.query(HealthSignal).filter_by(startup_id=s.id))
    assert abs(total - after.score) <= 1.5  # every point traces to a signal


def test_live_recommendations_rank_by_lift_with_links(db):
    _u, s = _assessed(db)
    recs = hs.live_recommendations(read_signals(db, s))
    assert recs and recs == sorted(recs, key=lambda r: r["estimated_lift"], reverse=True)
    assert len(recs) <= hs.REC_LIMIT
    assert all(r["key"].startswith("live.") and r["href"].startswith("/") for r in recs)
    hs.recompute_health_score(db, s, trigger="test")
    stored = db.query(HealthRecommendation).filter_by(startup_id=s.id).all()
    assert any(r.key.startswith("live.") for r in stored)
    href = hs._serialize_rec(next(r for r in stored if r.key.startswith("live.")))["href"]
    assert href in {x.href for x in SIGNALS}


def test_ai_rewrite_only_enqueued_when_recs_are_new(db, monkeypatch):
    _u, s = _assessed(db)
    enqueued = []
    monkeypatch.setattr(hs.job_dispatcher, "enqueue", lambda *a, **k: enqueued.append(a[1]))
    hs.recompute_health_score(db, s, trigger="test")  # first: new recs -> enqueue
    hs.recompute_health_score(db, s, trigger="test")  # same recs -> no LLM spend
    assert enqueued.count("ai.health.recommendations") == 1


def test_dismissed_recommendations_stay_dismissed(db):
    _u, s = _assessed(db)
    hs.recompute_health_score(db, s, trigger="test")
    rec = db.query(HealthRecommendation).filter(HealthRecommendation.key.like("live.%")).first()
    rec.status = RecommendationStatus.dismissed
    db.flush()
    hs.recompute_health_score(db, s, trigger="test")
    db.refresh(rec)
    assert rec.status == RecommendationStatus.dismissed


def test_refresh_is_throttled_unless_forced(db):
    _u, s = _assessed(db)
    first = hs.refresh_health_score(db, s, trigger="t")
    db.flush()
    calls = []
    real = hs.recompute_health_score
    hs_mod_recompute = lambda *a, **k: calls.append(1) or real(*a, **k)  # noqa: E731
    try:
        hs.recompute_health_score = hs_mod_recompute
        assert hs.refresh_health_score(db, s, trigger="t") is first  # within 15 min
        assert calls == []
        hs.refresh_health_score(db, s, trigger="t", force=True)
        assert calls == [1]
    finally:
        hs.recompute_health_score = real


def test_refresh_noop_before_assessment(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    assert hs.refresh_health_score(db, s, trigger="t", force=True) is None


def test_events_trigger_a_refresh(db):
    _u, s = _assessed(db)
    bus = DispatchingEventBus()
    triggers.register(bus)
    bus.publish(db, "mission.task.completed", {"startup_id": str(s.id)})
    assert db.query(HealthScore).filter_by(startup_id=s.id).count() == 1
    bus.publish(db, "mission.task.completed", {})  # no startup: ignored


def test_summary_line_prd_pattern(db):
    _u, s = _assessed(db, scores={**BASE, "product": 90, "legal": 30})
    row = hs.recompute_health_score(db, s, trigger="assessment_complete")
    line = hs.summary_line(row, "validation")
    assert line.startswith("You're ") and "for validation stage." in line
    assert "Product is carrying you; Legal is holding you back." in line
    flat = HealthScore(score=60, band="healthy", dimension_scores=BASE)
    assert hs.summary_line(flat, None).endswith("Your five dimensions are evenly balanced.")


def test_overview_has_sparklines_and_top_three(db):
    _u, s = _assessed(db)
    data = hs.get_overview(db, s)
    assert data["status"] == "ok"
    assert all(isinstance(d["trend"], list) for d in data["dimensions"])
    assert len(data["top_recommendations"]) == 3
    assert all("href" in r for r in data["top_recommendations"])


def test_dimension_rows_have_target_and_links(db):
    _u, s = _assessed(db)
    hs.recompute_health_score(db, s, trigger="test")
    rows = hs.get_dimension(db, s, "market")["signals"]
    assert rows[0]["source"] == "assessment" and rows[0]["href"] == "/assessment"
    live = {r["key"]: r for r in rows if r["source"] == "live"}
    assert live["market.assumptions_tested"]["target"] == 5
    assert live["market.assumptions_tested"]["href"] == "/validation/assumptions"
    assert live["market.assumptions_tested"]["label"] == "Assumptions tested"


def test_history_markers_and_365_range(db):
    _u, s = _assessed(db)
    db.add(ActivityLog(startup_id=s.id, action="business.plan.generated", summary="x"))
    db.add(ActivityLog(startup_id=s.id, action="mission.task.added", summary="ignored"))
    db.flush()
    marks = hs.get_history_markers(db, s, "365d")
    assert [m["label"] for m in marks] == ["Business plan generated"]
    assert "365d" in hs.HISTORY_RANGES


def test_benchmarks_compute_real_percentiles(db):
    cohort = []
    for score in (40, 50, 60, 70, 80):
        _u, s = _assessed(db, scores=dict.fromkeys(BASE, score), days_ago=0)
        hs.recompute_health_score(db, s, trigger="assessment_complete")
        cohort.append(s)
    data = hs.get_benchmarks(db, cohort[2])
    assert data["status"] == "ok" and data["cohort"]["size"] >= 5
    overall = data["percentiles"]["overall"]
    assert overall["p25"] <= overall["p50"] <= overall["p75"]
    assert overall["you"] == db.query(HealthScore).filter_by(startup_id=cohort[2].id).one().score
    assert set(data["percentiles"]) == {"overall", "product", "market", "money", "legal", "team"}


def test_prd_health_notification_copy():
    dropped = SPECS["healthscore.dropped"]
    assert dropped.title({"delta_7d": -6}) == "Your Health Score dropped 6 points, worth a look"
    assert dropped.body({}) == "See why"
    record = SPECS["healthscore.record"]
    assert record.title({"score": 81}) == "New personal best: 81. Keep going."
    assert record.title({}) == "New personal best. Keep going."


def test_markers_endpoint_and_range_validation(client, db):
    u, s = _assessed(db)
    u.email_verified_at = datetime.now(UTC)
    db.commit()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    ok = client.get("/api/v1/health-score/history/markers", params={"range": "365d"}, headers=h)
    assert ok.status_code == 200, ok.text
    bad = client.get("/api/v1/health-score/history/markers", params={"range": "2y"}, headers=h)
    assert bad.status_code == 422
    assert (
        client.get("/api/v1/health-score/history", params={"range": "365d"}, headers=h).status_code
        == 200
    )


@pytest.mark.parametrize("key", sorted(SIGNALS_BY_KEY))
def test_every_signal_reads_without_error(db, key):
    _u, s = _assessed(db)
    value = SIGNALS_BY_KEY[key].read(db, s)
    assert value is None or value >= 0


def test_overview_refresh_is_committed_and_advances_updated_at(client, db):
    """Regression: a stale-on-read refresh must persist (it used to roll back) and the upsert
    must move updated_at (ON CONFLICT DO UPDATE skips the ORM onupdate)."""
    u, s = _assessed(db)
    u.email_verified_at = datetime.now(UTC)
    hs.recompute_health_score(db, s, trigger="assessment_complete")
    row = db.query(HealthScore).filter_by(startup_id=s.id).one()
    row.updated_at = datetime.now(UTC) - timedelta(hours=1)
    db.query(HealthSignal).filter(
        HealthSignal.startup_id == s.id, ~HealthSignal.key.like("assessment.%")
    ).delete(synchronize_session=False)
    db.commit()
    stale = row.updated_at
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    assert client.get("/api/v1/health-score", headers=h).status_code == 200
    db.rollback()  # anything not committed by the request is discarded here
    db.expire_all()
    fresh = db.query(HealthScore).filter_by(startup_id=s.id).one()
    assert fresh.updated_at > stale
    keys = {x.key for x in db.query(HealthSignal).filter_by(startup_id=s.id)}
    assert "market.assumptions_tested" in keys
