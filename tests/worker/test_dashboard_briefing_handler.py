import uuid
from datetime import date

import pytest

from app.core.config import settings
from app.db.models.assessment import AssessmentResult
from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import AssessmentStatus, BriefingStatus
from app.db.models.job import Job, JobStatus
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_dashboard_briefing
from tests.factories import create_assessment, create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *a, **k):  # pragma: no cover
        raise AssertionError

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _seed(db, *, status=BriefingStatus.generating):
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, s, status=AssessmentStatus.completed)
    db.add(
        AssessmentResult(
            assessment_id=a.id,
            dimension_scores={"product": 30, "market": 30, "money": 30, "legal": 30, "team": 30},
            overall_provisional=30,
            narrative="n",
        )
    )
    row = DailyBriefing(
        startup_id=s.id,
        briefing_date=date.today(),
        status=status,
        briefing="p",
        risks="p",
        opportunities="p",
    )
    db.add(row)
    db.flush()
    return s, row


def _job(startup_id):
    return Job(
        type="ai.dashboard.briefing",
        payload={"startup_id": str(startup_id), "briefing_date": date.today().isoformat()},
        status=JobStatus.running,
    )


def test_fills_generating_row(db, monkeypatch):
    s, row = _seed(db)
    fake = _FakeLLM({"briefing": "B", "risks": "R", "opportunities": "O"})
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_dashboard_briefing(db, _job(s.id))
    db.refresh(row)
    assert (row.status, row.briefing, row.risks, row.opportunities) == (
        BriefingStatus.ready,
        "B",
        "R",
        "O",
    )
    assert fake.calls == 1


def test_noop_when_already_ready(db, monkeypatch):
    s, row = _seed(db, status=BriefingStatus.ready)
    monkeypatch.setattr(
        ai_mod, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError("no LLM call"))
    )
    handle_dashboard_briefing(db, _job(s.id))
    db.refresh(row)
    assert row.briefing == "p"  # untouched


def test_noop_when_startup_missing(db):
    handle_dashboard_briefing(db, _job(uuid.uuid4()))  # no raise


def test_stub_marks_fields(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, row = _seed(db)
    handle_dashboard_briefing(db, _job(s.id))
    db.refresh(row)
    assert row.status == BriefingStatus.ready
    assert "[stub-llm]" in row.briefing


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    s, row = _seed(db)
    with pytest.raises(RuntimeError):
        handle_dashboard_briefing(db, _job(s.id))
