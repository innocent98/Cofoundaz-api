from datetime import date

from app.core.config import settings
from app.db.models.llm_usage import LlmUsageDaily
from app.platform import llm_budget as lb
from tests.factories import create_startup, create_user


class _FakeClient:
    def __init__(self, text, tokens):
        self.text = text
        self.last_usage_tokens = tokens
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return {"ok": True}


def _used(db, s):
    r = db.query(LlmUsageDaily).filter_by(startup_id=s.id, usage_date=date.today()).one_or_none()
    return r.tokens_used if r else 0


def test_debit_upserts_and_increments(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    lb.debit(db, s.id, 100)
    lb.debit(db, s.id, 50)
    assert _used(db, s) == 150


def test_over_budget_threshold(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 200)
    u = create_user(db)
    s = create_startup(db, owner=u)
    assert lb.over_budget(db, s.id) is False
    lb.debit(db, s.id, 200)
    assert lb.over_budget(db, s.id) is True


def test_unlimited_when_budget_non_positive(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 0)
    u = create_user(db)
    s = create_startup(db, owner=u)
    lb.debit(db, s.id, 10_000)
    assert lb.over_budget(db, s.id) is False


def test_metered_complete_debits_actual(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1000)
    fake = _FakeClient("hello", 321)
    monkeypatch.setattr(lb, "get_llm_client", lambda: fake)
    u = create_user(db)
    s = create_startup(db, owner=u)
    out = lb.metered_complete(db, s.id, [], max_tokens=100)
    assert out == "hello" and fake.calls == 1
    assert _used(db, s) == 321


def test_metered_complete_skips_when_over_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    fake = _FakeClient("hello", 321)
    monkeypatch.setattr(lb, "get_llm_client", lambda: fake)
    u = create_user(db)
    s = create_startup(db, owner=u)
    lb.debit(db, s.id, 100)  # at budget
    out = lb.metered_complete(db, s.id, [], max_tokens=100)
    assert out is None and fake.calls == 0  # no LLM call
    assert _used(db, s) == 100  # no debit


def test_is_ai_enrichment_job():
    assert lb.is_ai_enrichment_job("ai.dashboard.briefing")
    assert lb.is_ai_enrichment_job("business.persona.ai_fill")
    assert lb.is_ai_enrichment_job("business.plan.generate")
    assert not lb.is_ai_enrichment_job("roadmap.replanned")
    assert not lb.is_ai_enrichment_job("email.notification")
