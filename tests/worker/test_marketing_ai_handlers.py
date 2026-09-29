from app.core.config import settings
from app.db.models.enums import ChannelKey, MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.job import Job, JobStatus
from app.db.models.marketing import MarketingAiGeneration, MarketingChannel, SeoKeyword
from app.platform import llm_budget
from app.worker.handlers.marketing_ai import (
    handle_marketing_channel_fit,
    handle_marketing_channel_plan,
    handle_marketing_content_gap,
    handle_marketing_copy,
    handle_marketing_plan_week,
    normalize_channel_mix,
)
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        return self.payload


def _gen(db, startup_id, kind, created_by, inputs=None):
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=kind,
        inputs=(
            inputs
            if inputs is not None
            else {"asset_type": "ad", "tone": "bold", "key_message": "Ship it"}
        ),
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    return g


def _job(kind, gid, sid):
    t = f"ai.marketing.{kind.value}"
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


def test_normalize_scales_to_100_and_drops_invalid_keys():
    out = normalize_channel_mix({"search": 30, "email": 30, "not_a_channel": 40})
    assert set(out) <= {c.value for c in ChannelKey}
    assert sum(out.values()) == 100
    assert "not_a_channel" not in out


def test_normalize_even_split_when_nothing_valid():
    out = normalize_channel_mix({})
    assert sum(out.values()) == 100
    assert len(out) == 8


def test_normalize_rejects_bool_str_and_negative_values():
    out = normalize_channel_mix({"search": True, "email": "30", "content_seo": -5, "referral": 20})
    assert sum(out.values()) == 100
    # only referral survived as a valid, non-negative, non-bool numeric value
    assert out["referral"] == 100


def test_normalize_rejects_nan_and_inf():
    # NaN/Inf must be dropped, not crash; with no other valid values -> even split summing to 100.
    out = normalize_channel_mix({"search": float("nan"), "email": float("inf")})
    assert sum(out.values()) == 100
    assert len(out) == 8
    # a finite value alongside a non-finite one: only the finite one survives, scaled to 100.
    out2 = normalize_channel_mix({"search": float("nan"), "email": 40})
    assert out2["email"] == 100
    assert sum(out2.values()) == 100


def test_normalize_largest_remainder_sums_exactly_100():
    out = normalize_channel_mix(
        {
            "organic_social": 1,
            "paid_social": 1,
            "search": 1,
            "email": 1,
            "content_seo": 1,
            "partnerships": 1,
            "events": 1,
            "referral": 1,
        }
    )
    assert sum(out.values()) == 100
    assert len(out) == 8


def test_channel_plan_fills_normalized_mix_and_rationale(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(
        db,
        s.id,
        MarketingGenerationKind.channel_plan,
        u.id,
        inputs={"objective": "awareness", "budget": 5000},
    )
    payload = {"channel_mix": {"search": 30, "email": 30, "referral": 41}, "rationale": "focus"}
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM(payload))
    handle_marketing_channel_plan(db, _job(MarketingGenerationKind.channel_plan, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    assert sum(row.output["channel_mix"].values()) == 100
    assert row.output["rationale"] == "focus"


def test_channel_plan_over_budget_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.channel_plan, u.id, inputs={"objective": "x"})
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_marketing_channel_plan(db, _job(MarketingGenerationKind.channel_plan, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.failed
    assert row.error == "over_budget"
    assert row.output == {}


def test_channel_fit_writes_notes_onto_rows_and_seeds(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.channel_fit, u.id, inputs={})
    payload = {"notes": {k.value: f"note for {k.value}" for k in ChannelKey}}
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM(payload))
    handle_marketing_channel_fit(db, _job(MarketingGenerationKind.channel_fit, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    rows = db.query(MarketingChannel).filter_by(startup_id=s.id).all()
    assert len(rows) == 8  # lazy-seeded
    by_key = {c.key: c for c in rows}
    assert by_key[ChannelKey.search].ai_fit_note == "note for search"
    assert by_key[ChannelKey.search].fit_note_generated_at is not None


def test_channel_fit_keeps_prior_note_when_key_omitted(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    prior = MarketingChannel(startup_id=s.id, key=ChannelKey.search, ai_fit_note="prior note")
    db.add(prior)
    db.flush()
    g = _gen(db, s.id, MarketingGenerationKind.channel_fit, u.id, inputs={})
    payload = {
        "notes": {k.value: f"note for {k.value}" for k in ChannelKey if k != ChannelKey.search}
    }
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM(payload))
    handle_marketing_channel_fit(db, _job(MarketingGenerationKind.channel_fit, g.id, s.id))
    row = db.query(MarketingChannel).filter_by(startup_id=s.id, key=ChannelKey.search).one()
    assert row.ai_fit_note == "prior note"  # omitted key keeps its prior note


def test_channel_fit_drops_invalid_key_from_notes(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.channel_fit, u.id, inputs={})
    payload = {
        "notes": {
            **{k.value: f"note for {k.value}" for k in ChannelKey},
            "not_a_channel": "bogus note",
        }
    }
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM(payload))
    handle_marketing_channel_fit(db, _job(MarketingGenerationKind.channel_fit, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    # invalid key dropped from output, valid keys all present
    assert "not_a_channel" not in row.output["notes"]
    assert set(row.output["notes"]) == {c.value for c in ChannelKey}
    # invalid key never reached any MarketingChannel row
    rows = db.query(MarketingChannel).filter_by(startup_id=s.id).all()
    assert {c.key.value for c in rows} == {c.value for c in ChannelKey}
    assert all(c.ai_fit_note != "bogus note" for c in rows)


def test_channel_fit_over_budget_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.channel_fit, u.id, inputs={})
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_marketing_channel_fit(db, _job(MarketingGenerationKind.channel_fit, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.failed
    assert row.error == "over_budget"
    assert row.output == {}


def test_content_gap_over_budget_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = _gen(db, s.id, MarketingGenerationKind.content_gap, u.id, inputs={})
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_marketing_content_gap(db, _job(MarketingGenerationKind.content_gap, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.failed
    assert row.error == "over_budget"
    assert row.output == {}


def test_content_gap_grounds_on_keywords_and_fills_gaps(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add(SeoKeyword(startup_id=s.id, keyword="daily savings"))
    db.flush()
    g = _gen(db, s.id, MarketingGenerationKind.content_gap, u.id, inputs={})
    captured = {}

    class _CapturingLLM(_FakeLLM):
        def complete_json(self, messages, *, schema, max_tokens):
            captured["text"] = " ".join(m.content for m in messages)
            return self.payload

    gap = {"title": "T", "target_keyword": "daily savings", "angle": "A"}
    payload = {"gaps": [gap, "junk", gap]}
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _CapturingLLM(payload))
    handle_marketing_content_gap(db, _job(MarketingGenerationKind.content_gap, g.id, s.id))
    row = db.query(MarketingAiGeneration).filter_by(id=g.id).one()
    assert row.status == MarketingGenerationStatus.ready
    assert isinstance(row.output["gaps"], list)
    assert row.output["gaps"] == [gap, gap]
    assert "daily savings" in captured["text"]
