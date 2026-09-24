import uuid

import pytest

from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.db.models.roadmap import RoadmapReplan
from app.platform import llm_budget
from app.worker.handlers.ai import handle_roadmap_rationale
from tests.factories import create_roadmap, create_startup, create_user


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _replan(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, s)
    replan = RoadmapReplan(
        roadmap_id=r.id,
        applied_by=u.id,
        change_count=1,
        changes=[
            {
                "title": "Validate demand",
                "old_due": "2026-08-17",
                "new_due": "2026-09-03",
                "reason": "10 days overdue and not yet done.",
            }
        ],
        summary="Re-planned 1 milestone",
        rationale="templated fallback",
    )
    db.add(replan)
    db.flush()
    return replan


def _job(replan_id):
    return Job(
        type="ai.roadmap.rationale", payload={"replan_id": str(replan_id)}, status=JobStatus.running
    )


def test_overwrites_rationale(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    replan = _replan(db)
    fake = _FakeLLM("Your roadmap shifted because demand validation slipped.")
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_roadmap_rationale(db, _job(replan.id))
    db.refresh(replan)
    assert replan.rationale == "Your roadmap shifted because demand validation slipped."
    assert fake.calls == 1


def test_noop_when_replan_missing(db):
    handle_roadmap_rationale(db, _job(uuid.uuid4()))  # no raise


def test_stub_marks_rationale(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    replan = _replan(db)
    handle_roadmap_rationale(db, _job(replan.id))
    db.refresh(replan)
    assert "[stub-llm]" in replan.rationale


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    replan = _replan(db)
    with pytest.raises(RuntimeError):
        handle_roadmap_rationale(db, _job(replan.id))
