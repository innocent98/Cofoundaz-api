import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import RecommendationEffort, RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.db.models.job import Job, JobStatus
from app.services.health_score.ai_recommendations import catalog_bodies
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_health_recommendations
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *a, **k):  # pragma: no cover
        raise AssertionError

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _rec(db, startup, key, *, status=RecommendationStatus.pending, body=None):
    cb = catalog_bodies()
    r = HealthRecommendation(
        startup_id=startup.id,
        dimension=key.split(".")[0],
        key=key,
        title="T",
        body=(body if body is not None else cb[key]),
        estimated_lift=5,
        effort=RecommendationEffort.low,
        status=status,
        priority=1,
    )
    db.add(r)
    db.flush()
    return r


def _job(startup_id):
    return Job(
        type="ai.health.recommendations",
        payload={"startup_id": str(startup_id)},
        status=JobStatus.running,
    )


def test_rewrites_pending_default_bodies(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp")
    _rec(db, s, "market.icp_definition")
    fake = _FakeLLM(
        {
            "recommendations": [
                {"key": "product.define_mvp", "body": "AI body A"},
                {"key": "market.icp_definition", "body": "AI body B"},
            ]
        }
    )
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_health_recommendations(db, _job(s.id))
    rows = {r.key: r.body for r in db.query(HealthRecommendation).filter_by(startup_id=s.id)}
    assert rows["product.define_mvp"] == "AI body A"
    assert rows["market.icp_definition"] == "AI body B"
    assert fake.calls == 1


def test_noop_and_no_llm_call_when_all_non_default(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp", body="already personalized")
    monkeypatch.setattr(
        ai_mod,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("no LLM call expected")),
    )
    handle_health_recommendations(db, _job(s.id))  # returns before LLM call
    row = db.query(HealthRecommendation).filter_by(startup_id=s.id).one()
    assert row.body == "already personalized"


def test_skips_accepted_and_dismissed(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp", status=RecommendationStatus.accepted)
    _rec(db, s, "market.icp_definition", status=RecommendationStatus.dismissed)
    monkeypatch.setattr(
        ai_mod,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("no LLM call expected")),
    )
    handle_health_recommendations(db, _job(s.id))  # neither is pending -> no call, no change
    cb = catalog_bodies()
    rows = {r.key: r.body for r in db.query(HealthRecommendation).filter_by(startup_id=s.id)}
    assert rows["product.define_mvp"] == cb["product.define_mvp"]
    assert rows["market.icp_definition"] == cb["market.icp_definition"]


def test_stub_marks_first_pending(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp")
    _rec(db, s, "market.icp_definition")
    handle_health_recommendations(db, _job(s.id))
    bodies = [r.body for r in db.query(HealthRecommendation).filter_by(startup_id=s.id)]
    assert any("[stub-llm]" in b for b in bodies)


def test_noop_when_startup_missing(db):
    handle_health_recommendations(db, _job(uuid.uuid4()))  # no raise


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp")
    with pytest.raises(RuntimeError):
        handle_health_recommendations(db, _job(s.id))
