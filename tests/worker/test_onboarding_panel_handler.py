import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import StartupStage
from app.db.models.job import Job, JobStatus
from app.platform import llm_budget
from app.worker.handlers.ai import handle_onboarding_panel
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get first customers"]
    s.profile.ai_panel = "templated"
    db.flush()
    return s


def _job(startup_id):
    return Job(
        type="ai.onboarding.panel",
        payload={"startup_id": str(startup_id)},
        status=JobStatus.running,
    )


def test_overwrites_ai_panel(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = _startup(db)
    fake = _FakeLLM("Welcome — a fintech founder at the idea stage.")
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert s.profile.ai_panel == "Welcome — a fintech founder at the idea stage."
    assert fake.calls == 1


def test_noop_when_startup_missing(db):
    handle_onboarding_panel(db, _job(uuid.uuid4()))  # no raise


def test_stub_marks_panel(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s = _startup(db)
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert "[stub-llm]" in s.profile.ai_panel


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = _startup(db)
    with pytest.raises(RuntimeError):
        handle_onboarding_panel(db, _job(s.id))
