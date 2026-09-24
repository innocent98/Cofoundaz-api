import pytest

from app.core.config import settings
from app.db.models.business import BusinessPlan
from app.db.models.document import Document
from app.db.models.enums import BusinessPlanStatus, DocumentKind
from app.db.models.job import Job, JobStatus
from app.platform import events as events_mod
from app.services.business.plan_defs import PLAN_SECTIONS
from app.worker.handlers.plan import handle_plan_generate
from tests.factories import create_startup, create_user


def _job(plan_id, startup_id):
    return Job(
        type="business.plan.generate",
        payload={"plan_id": str(plan_id), "startup_id": str(startup_id)},
        status=JobStatus.running,
    )


def _plan(db, startup, user):
    p = BusinessPlan(
        startup_id=startup.id, status=BusinessPlanStatus.generating, created_by_id=user.id
    )
    db.add(p)
    db.flush()
    return p


def test_plan_generate_builds_document_and_completes(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = _plan(db, s, u)
    handle_plan_generate(db, _job(p.id, s.id))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.complete
    assert p.document_id is not None
    doc = db.get(Document, p.document_id)
    assert doc.kind == DocumentKind.business_plan and doc.ai_generated is True
    assert len(doc.sections) == len(PLAN_SECTIONS)
    assert all(sec["heading"] and "[stub-llm]" in sec["body"] for sec in doc.sections)
    assert any(e == "business.plan.generated" for e, _ in published)


def test_plan_generate_noop_when_missing_or_done(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    import uuid

    handle_plan_generate(db, _job(uuid.uuid4(), uuid.uuid4()))  # missing plan -> no raise
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = _plan(db, s, u)
    p.status = BusinessPlanStatus.complete
    db.flush()
    handle_plan_generate(db, _job(p.id, s.id))  # already complete -> no-op
    assert p.document_id is None


def test_plan_generate_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = _plan(db, s, u)
    with pytest.raises(RuntimeError):
        handle_plan_generate(db, _job(p.id, s.id))


def test_plan_skips_when_over_budget(db, monkeypatch):
    from app.platform import llm_budget

    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = _plan(db, s, u)
    llm_budget.debit(db, s.id, 100)  # over budget
    handle_plan_generate(db, _job(p.id, s.id))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.generating  # untouched — no partial plan
    assert p.document_id is None


def test_plan_generates_and_debits_under_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100_000)
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = _plan(db, s, u)
    handle_plan_generate(db, _job(p.id, s.id))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.complete
    assert p.document_id is not None
