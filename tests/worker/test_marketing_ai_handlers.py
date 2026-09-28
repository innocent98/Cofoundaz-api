from app.core.config import settings
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.job import Job, JobStatus
from app.db.models.marketing import MarketingAiGeneration
from app.platform import llm_budget
from app.worker.handlers.marketing_ai import handle_marketing_copy, handle_marketing_plan_week
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        return self.payload


def _gen(db, startup_id, kind, created_by):
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=kind,
        inputs={"asset_type": "ad", "tone": "bold", "key_message": "Ship it"},
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    return g


def _job(kind, gid, sid):
    t = "ai.marketing.copy" if kind == MarketingGenerationKind.copy else "ai.marketing.plan_week"
    return Job(
        type=t,
        payload={"generation_id": str(gid), "startup_id": str(sid)},
        status=JobStatus.running,
    )


def test_copy_fills_three_variants(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.copy, u.id)
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: _FakeLLM({"variants": ["a", "b", "c", "d"]})
    )
    handle_marketing_copy(db, _job(MarketingGenerationKind.copy, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    assert row.output["variants"] == ["a", "b", "c"]  # truncated to 3


def test_copy_over_budget_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.copy, u.id)
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_marketing_copy(db, _job(MarketingGenerationKind.copy, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.failed
    assert row.error == "over_budget"
    assert row.output == {}


def test_copy_idempotent_when_not_generating(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.copy, u.id)
    g.status = MarketingGenerationStatus.ready
    db.flush()
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_marketing_copy(db, _job(MarketingGenerationKind.copy, g.id, s.id))  # no-op, no raise


def test_plan_week_drops_invalid_channel(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.plan_week, u.id)
    payload = {
        "entries": [
            {"title": "A", "channel": "email", "body": "x", "day_offset": 0},
            {"title": "B", "channel": "not_a_channel", "body": "y", "day_offset": 1},
        ]
    }
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM(payload))
    handle_marketing_plan_week(db, _job(MarketingGenerationKind.plan_week, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    assert [e["channel"] for e in row.output["entries"]] == ["email"]  # invalid dropped
