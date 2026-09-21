import pytest

from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.worker.handlers.ai import handle_assessment_narrative
from tests.factories import create_assessment, create_startup, create_user


def _completed_result(db, startup):
    # create an assessment + a result row with a templated narrative to be overwritten
    from app.db.models.assessment import AssessmentResult
    from app.db.models.enums import AssessmentStatus

    a = create_assessment(db, startup=startup, status=AssessmentStatus.completed)
    r = AssessmentResult(
        assessment_id=a.id,
        dimension_scores={"team": 60, "market": 50},
        overall_provisional=55,
        narrative="TEMPLATED narrative.",
    )
    db.add(r)
    db.flush()
    return a, r


def _job(startup_id, assessment_id):
    return Job(
        type="ai.assessment.narrative",
        payload={"startup_id": str(startup_id), "assessment_id": str(assessment_id)},
        status=JobStatus.running,
    )


def test_handler_overwrites_narrative_with_llm_text(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    a, r = _completed_result(db, s)
    handle_assessment_narrative(db, _job(s.id, a.id))
    db.refresh(r)
    assert r.narrative.startswith("[stub-llm]")


def test_handler_is_a_noop_when_result_missing(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, startup=s)  # no result row
    handle_assessment_narrative(db, _job(s.id, a.id))  # must not raise


def test_handler_fails_loud_when_llm_errors(db, monkeypatch):
    # openai provider + empty key => get_llm_client() returns OpenAILLMClient, complete() raises
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    a, r = _completed_result(db, s)
    with pytest.raises(RuntimeError):
        handle_assessment_narrative(db, _job(s.id, a.id))
