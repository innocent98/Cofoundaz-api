from datetime import date

from app.core.config import settings
from app.db.models.enums import StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.llm_usage import LlmUsageDaily
from app.platform import llm_budget
from app.worker.handlers.ai import handle_onboarding_panel
from tests.factories import create_startup, create_user


class _FakeClient:
    def __init__(self, text, tokens):
        self.text = text
        self.last_usage_tokens = tokens
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _ready(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get customers"]
    s.profile.ai_panel = "templated"
    db.flush()
    return s


def _job(sid):
    return Job(
        type="ai.onboarding.panel", payload={"startup_id": str(sid)}, status=JobStatus.running
    )


def test_handler_enriches_and_debits_under_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    fake = _FakeClient("AI panel text", 250)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    s = _ready(db)
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert s.profile.ai_panel == "AI panel text"
    row = db.query(LlmUsageDaily).filter_by(startup_id=s.id, usage_date=date.today()).one()
    assert row.tokens_used == 250


def test_handler_skips_when_over_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    fake = _FakeClient("AI panel text", 250)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    s = _ready(db)
    llm_budget.debit(db, s.id, 100)  # at budget
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert s.profile.ai_panel == "templated"  # skipped — templated kept
    assert fake.calls == 0
