import uuid

from app.core.config import settings
from app.db.models.enums import EnrichmentStatus, StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.learning import LearningRecommendation
from app.platform import llm_budget
from app.worker.handlers import ai as ai_handlers
from app.worker.handlers.ai import handle_learning_recommendations
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _row(db, startup_id, stage="build", status=EnrichmentStatus.generating, reason="fallback"):
    r = LearningRecommendation(startup_id=startup_id, stage=stage, reason=reason, status=status)
    db.add(r)
    db.flush()
    return r


def _job(startup_id):
    return Job(
        type="ai.learning.recommendations",
        payload={"startup_id": str(startup_id)},
        status=JobStatus.running,
    )


def test_writes_reason_and_marks_ready(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id)
    fake = _FakeLLM({"reason": "Because you're at build stage, focus on pricing."})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_learning_recommendations(db, _job(s.id))
    row = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.reason == "Because you're at build stage, focus on pricing."
    assert fake.calls == 1


def test_over_budget_keeps_generating(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id, reason="fallback")
    llm_budget.debit(db, s.id, 5)  # push over the 1-token budget
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))
    row = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert row.status == EnrichmentStatus.generating  # stuck on fallback, no crash
    assert row.reason == "fallback"


def test_idempotent_when_ready(db, monkeypatch):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id, status=EnrichmentStatus.ready, reason="done")
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))  # no LLM call, no raise


def test_none_stage_marks_ready_no_llm(db, monkeypatch):
    s = create_startup(db, owner=create_user(db))
    s.stage = None
    _row(db, s.id, stage=None, reason="Recommended to help you get started.")
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))
    row = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.reason == "Recommended to help you get started."


def test_empty_shelf_marks_ready_no_llm(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id)
    monkeypatch.setattr(ai_handlers, "recommended_courses", lambda stage, ids: [])
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))
    assert (
        db.query(LearningRecommendation).filter_by(startup_id=s.id).one().status
        == EnrichmentStatus.ready
    )


def test_truncates_reason_to_300(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"reason": "x" * 500}))
    handle_learning_recommendations(db, _job(s.id))
    assert len(db.query(LearningRecommendation).filter_by(startup_id=s.id).one().reason) == 300


def test_noop_when_startup_missing(db):
    handle_learning_recommendations(db, _job(uuid.uuid4()))  # no raise
