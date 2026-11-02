import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import EnrichmentStatus, StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.learning import LearningRecommendation
from app.platform import llm_budget
from app.services.learning import recommendations as rec
from app.worker.handlers.ai import handle_learning_recommendations
from tests.factories import create_startup, create_user

COURSE = "validation-talk-to-customers"


@pytest.fixture(autouse=True)
def _content(academy):
    return academy


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.messages = None
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        self.messages = messages
        return self.payload


def _startup(db, stage=StartupStage.validation):
    s = create_startup(db, owner=create_user(db))
    s.stage = stage
    db.flush()
    return s


def _row(
    db, startup, *, key=None, status=EnrichmentStatus.generating, reason="fallback", course=COURSE
):
    key = key or rec.context_key(startup.stage, rec.gather_gaps(db, startup.id))
    r = LearningRecommendation(
        startup_id=startup.id,
        course_id=course,
        context_key=key,
        stage=startup.stage.value if startup.stage else None,
        reason=reason,
        status=status,
    )
    db.add(r)
    db.flush()
    return r


def _job(startup_id, key, course=COURSE):
    return Job(
        type="ai.learning.recommendations",
        payload={"startup_id": str(startup_id), "course_id": course, "context_key": key},
        status=JobStatus.running,
    )


def _no_llm(monkeypatch):
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )


def test_writes_reason_and_marks_ready(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = _startup(db)
    row = _row(db, s)
    fake = _FakeLLM({"reason": "You have not talked to customers yet, so start here."})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_learning_recommendations(db, _job(s.id, row.context_key))
    db.refresh(row)
    assert row.status == EnrichmentStatus.ready
    assert row.reason == "You have not talked to customers yet, so start here."
    assert fake.calls == 1
    prompt = fake.messages[1].content
    assert "Talk to customers" in prompt and "customer interview" in prompt  # the matched gap
    assert prompt.startswith("<context>")


def test_a_job_queued_for_an_older_context_is_ignored(db, monkeypatch):
    s = _startup(db)
    row = _row(db, s, key="current-key")
    _no_llm(monkeypatch)
    handle_learning_recommendations(db, _job(s.id, "stale-key"))
    db.refresh(row)
    assert row.status == EnrichmentStatus.generating and row.reason == "fallback"


def test_jobs_without_a_course_or_row_are_noops(db, monkeypatch):
    s = _startup(db)
    _no_llm(monkeypatch)
    legacy = Job(
        type="ai.learning.recommendations",
        payload={"startup_id": str(s.id)},
        status=JobStatus.running,
    )
    handle_learning_recommendations(db, legacy)  # queued before reasons were per course
    handle_learning_recommendations(db, _job(s.id, "k"))  # no row yet


def test_over_budget_keeps_generating(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    s = _startup(db)
    row = _row(db, s)
    llm_budget.debit(db, s.id, 5)  # push over the 1-token budget
    _no_llm(monkeypatch)
    handle_learning_recommendations(db, _job(s.id, row.context_key))
    db.refresh(row)
    assert row.status == EnrichmentStatus.generating and row.reason == "fallback"


def test_idempotent_when_ready(db, monkeypatch):
    s = _startup(db)
    row = _row(db, s, status=EnrichmentStatus.ready, reason="done")
    _no_llm(monkeypatch)
    handle_learning_recommendations(db, _job(s.id, row.context_key))
    assert row.reason == "done"


def test_no_stage_marks_ready_without_an_llm_call(db, monkeypatch):
    s = _startup(db, stage=None)
    row = _row(db, s, reason="Recommended to help you get started.")
    _no_llm(monkeypatch)
    handle_learning_recommendations(db, _job(s.id, row.context_key))
    db.refresh(row)
    assert row.status == EnrichmentStatus.ready
    assert row.reason == "Recommended to help you get started."


def test_a_course_removed_from_the_catalog_marks_ready_without_an_llm_call(db, monkeypatch):
    s = _startup(db)
    row = _row(db, s, course="gone-course")
    _no_llm(monkeypatch)
    handle_learning_recommendations(db, _job(s.id, row.context_key, course="gone-course"))
    db.refresh(row)
    assert row.status == EnrichmentStatus.ready


def test_truncates_reason_to_300(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = _startup(db)
    row = _row(db, s)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"reason": "x" * 500}))
    handle_learning_recommendations(db, _job(s.id, row.context_key))
    db.refresh(row)
    assert len(row.reason) == 300


def test_noop_when_startup_missing(db):
    handle_learning_recommendations(db, _job(uuid.uuid4(), "k"))  # no raise
