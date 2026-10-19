"""Business Builder PRD completion, slice (c): business plan options, readiness, progress,
failed state, draft-missing and section regenerate (08.11); revenue model projection and
"Send to financial model" (08.8, D-BB-5 revised); Value Proposition fit-lines (08.5)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.business import BusinessPlan
from app.db.models.document import Document
from app.db.models.enums import BusinessPlanStatus, FinancialModelStatus, MembershipRole
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job, JobStatus
from app.platform import llm_budget
from app.services.business.plan_defs import PLAN_SECTIONS
from app.worker.handlers.finance_model import handle_finance_model
from app.worker.handlers.plan import handle_plan_generate, handle_plan_section
from tests.factories import create_membership, create_startup, create_user

BB = "/api/v1/business-builder"


def _workspace(db, *roles):
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=founder)
    create_membership(db, founder, s, role=MembershipRole.founder)
    people = [(founder, _h(founder, s))]
    for role in roles:
        u = create_user(db, email_verified_at=datetime.now(UTC))
        create_membership(db, u, s, role=role)
        people.append((u, _h(u, s)))
    db.flush()
    return s, people


def _h(u, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


def _plan_job(db, plan_id, startup_id, attempts=1):
    return Job(
        type="business.plan.generate",
        payload={"plan_id": str(plan_id), "startup_id": str(startup_id)},
        status=JobStatus.running,
        attempts=attempts,
    )


# --- plan options, progress, failed ------------------------------------------------------------


def test_generate_stores_options_and_defaults(client, db):
    _s, [(_f, h)] = _workspace(db)
    r = client.post(f"{BB}/plan/generate", json={"audience": "bank", "length": "full"}, headers=h)
    assert r.status_code == 202, r.text
    plan = client.get(f"{BB}/plan", headers=h).json()["data"]
    assert plan["options"] == {"audience": "bank", "length": "full"}
    assert plan["progress"] == {
        "done": 0,
        "total": len(PLAN_SECTIONS),
        "stage": "Compiling your canvases",
    }
    assert plan["error"] is None
    _s2, [(_f2, h2)] = _workspace(db)  # (same-transaction plans would tie on created_at)
    r2 = client.post(f"{BB}/plan/generate", headers=h2)  # no body: defaults
    assert r2.status_code == 202
    assert client.get(f"{BB}/plan", headers=h2).json()["data"]["options"] == {
        "audience": "investors",
        "length": "concise",
    }


def test_generate_rejects_unknown_options(client, db):
    _s, [(_f, h)] = _workspace(db)
    r = client.post(f"{BB}/plan/generate", json={"audience": "aliens"}, headers=h)
    assert r.status_code == 422


def test_options_shape_the_prompt(db):
    from app.services.business.plan_prompt import build_section_messages

    msgs = build_section_messages(PLAN_SECTIONS[0], {}, {"audience": "bank", "length": "full"})
    text = " ".join(m.content for m in msgs)
    assert "lender" in text.lower() or "bank" in text.lower()
    concise = " ".join(
        m.content
        for m in build_section_messages(
            PLAN_SECTIONS[0], {}, {"audience": "internal", "length": "concise"}
        )
    )
    assert "concise" in concise.lower() and "internal" in concise.lower()


def test_completed_plan_reports_full_progress(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = BusinessPlan(startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=u.id)
    db.add(p)
    db.flush()
    handle_plan_generate(db, _plan_job(db, p.id, s.id))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.complete
    assert p.progress == {"done": len(PLAN_SECTIONS), "total": len(PLAN_SECTIONS), "stage": "Done"}


def test_over_budget_marks_the_plan_failed(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = BusinessPlan(startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=u.id)
    db.add(p)
    db.flush()
    llm_budget.debit(db, s.id, 100)
    handle_plan_generate(db, _plan_job(db, p.id, s.id))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.failed and p.error == "over_budget"


def test_llm_error_retries_then_fails_on_the_last_attempt(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    p = BusinessPlan(startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=u.id)
    db.add(p)
    db.flush()
    with pytest.raises(RuntimeError):  # not the last attempt: let the runner retry
        handle_plan_generate(db, _plan_job(db, p.id, s.id, attempts=1))
    assert p.status == BusinessPlanStatus.generating
    handle_plan_generate(db, _plan_job(db, p.id, s.id, attempts=settings.WORKER_MAX_ATTEMPTS))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.failed and p.error == "generation_failed"


# --- readiness + draft missing ------------------------------------------------------------------


def test_readiness_lists_every_artifact(client, db):
    _s, [(_f, h)] = _workspace(db)
    client.get(f"{BB}/canvases/swot", headers=h)
    client.put(
        f"{BB}/canvases/swot",
        json={
            "blocks": {
                "strengths": ["a"],
                "weaknesses": ["b"],
                "opportunities": ["c"],
                "threats": ["d"],
            },
            "version": 1,
        },
        headers=h,
    )
    client.get(f"{BB}/canvases/lean", headers=h)
    client.put(f"{BB}/canvases/lean", json={"blocks": {"problem": ["x"]}, "version": 1}, headers=h)
    r = client.get(f"{BB}/plan/readiness", headers=h)
    assert r.status_code == 200
    data = r.json()["data"]
    items = {i["type"]: i for i in data["items"]}
    assert items["swot"]["status"] == "ready"
    assert items["lean"]["status"] == "partial"
    assert items["persona"]["status"] == "missing"
    assert data["ready_count"] == 1 and data["total"] == 9
    assert set(data["missing"]) == set(items) - {"swot"}


def test_draft_missing_enqueues_fills_for_incomplete_artifacts(client, db):
    _s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    r = client.post(f"{BB}/plan/draft-missing", headers=h)
    assert r.status_code == 202, r.text
    jobs = r.json()["data"]["jobs"]
    assert {j["type"] for j in jobs} == {
        "business_model",
        "lean",
        "value_prop",
        "mission_vision",
        "swot",
        "persona",
        "revenue_stream",
        "competitor",
        "pricing",
    }
    assert all(db.get(Job, uuid.UUID(j["job_id"])) for j in jobs)
    assert client.post(f"{BB}/plan/draft-missing", headers=h_bc).status_code == 403


# --- section regenerate -------------------------------------------------------------------------


def _complete_plan(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(f, h)] = _workspace(db)
    p = BusinessPlan(startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=f.id)
    db.add(p)
    db.flush()
    handle_plan_generate(db, _plan_job(db, p.id, s.id))
    db.refresh(p)
    return s, f, h, p


def test_regenerate_a_section_rewrites_only_that_section(client, db, monkeypatch):
    s, _f, h, p = _complete_plan(db, monkeypatch)
    doc = db.get(Document, p.document_id)
    doc.sections = [{**sec, "body": "MY EDIT"} for sec in doc.sections]
    db.flush()
    before_version = doc.version
    r = client.post(f"{BB}/plan/sections/financials/regenerate", headers=h)
    assert r.status_code == 202, r.text
    job = db.get(Job, uuid.UUID(r.json()["data"]["job_id"]))
    assert job.type == "ai.business.plan_section"
    handle_plan_section(db, job)
    db.refresh(doc)
    bodies = {sec["heading"]: sec["body"] for sec in doc.sections}
    assert "[stub-llm]" in bodies["Financials & Projections"]
    assert bodies["Executive Summary"] == "MY EDIT"
    assert doc.version == before_version + 1
    assert job.result == {
        "status": "ready",
        "section": "financials",
        "document_version": doc.version,
    }


def test_regenerate_needs_a_finished_plan_and_a_known_section(client, db, monkeypatch):
    _s, [(_f, h)] = _workspace(db)
    assert client.post(f"{BB}/plan/sections/financials/regenerate", headers=h).status_code == 404
    _s2, _f2, h2, _p = _complete_plan(db, monkeypatch)
    assert client.post(f"{BB}/plan/sections/nope/regenerate", headers=h2).status_code == 404


def test_regenerate_when_the_heading_was_removed_reports_missing(client, db, monkeypatch):
    _s, _f, h, p = _complete_plan(db, monkeypatch)
    doc = db.get(Document, p.document_id)
    doc.sections = [sec for sec in doc.sections if sec["heading"] != "Team"]
    db.flush()
    job = db.get(
        Job,
        uuid.UUID(
            client.post(f"{BB}/plan/sections/team/regenerate", headers=h).json()["data"]["job_id"]
        ),
    )
    handle_plan_section(db, job)
    assert job.result["status"] == "section_missing"


# --- revenue model -------------------------------------------------------------------------------


def _streams(client, h):
    client.post(
        f"{BB}/revenue-streams",
        json={"data": {"name": "Subscriptions", "pricing_basis": "monthly", "est_monthly": 150000}},
        headers=h,
    )
    client.post(
        f"{BB}/revenue-streams",
        json={"data": {"name": "Setup fees", "pricing_basis": "one-off", "est_monthly": 50000}},
        headers=h,
    )


def test_projection_compounds_each_stream(client, db):
    _s, [(_f, h)] = _workspace(db)
    _streams(client, h)
    r = client.get(
        f"{BB}/revenue-streams/projection", params={"months": 3, "growth_pct": 10}, headers=h
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["growth_pct"] == 10 and len(data["months"]) == 3
    assert data["total"] == [200000.0, 220000.0, 242000.0]
    assert [s["name"] for s in data["streams"]] == ["Subscriptions", "Setup fees"]
    assert data["streams"][0]["values"] == [150000.0, 165000.0, 181500.0]


def test_projection_bounds(client, db):
    _s, [(_f, h)] = _workspace(db)
    assert (
        client.get(f"{BB}/revenue-streams/projection", params={"months": 61}, headers=h).status_code
        == 422
    )


def test_send_to_financial_model_stores_drivers(client, db):
    _s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    assert client.post(f"{BB}/revenue-streams/send-to-finance", headers=h).status_code == 422
    _streams(client, h)
    r = client.post(f"{BB}/revenue-streams/send-to-finance", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total_monthly_minor"] == 20_000_000
    assert [x["name"] for x in d["streams"]] == ["Subscriptions", "Setup fees"]
    assert d["sent_at"]
    assert client.post(f"{BB}/revenue-streams/send-to-finance", headers=h_bc).status_code == 403


def test_financial_model_uses_drivers_when_there_is_no_revenue_yet(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(f, h)] = _workspace(db)
    _streams(client, h)
    client.post(f"{BB}/revenue-streams/send-to-finance", headers=h)
    m = FinancialModel(
        startup_id=s.id, created_by=f.id, status=FinancialModelStatus.generating, horizon_months=12
    )
    db.add(m)
    db.flush()
    handle_finance_model(db, Job(type="ai.finance.model", payload={"model_id": str(m.id)}))
    db.refresh(m)
    assert m.status == FinancialModelStatus.complete
    assert m.assumptions["revenue_source"] == "business_builder"
    assert m.assumptions["revenue_0_minor"] == 20_000_000


def test_financial_model_without_drivers_keeps_actuals(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s, [(f, _h)] = _workspace(db)
    m = FinancialModel(
        startup_id=s.id, created_by=f.id, status=FinancialModelStatus.generating, horizon_months=12
    )
    db.add(m)
    db.flush()
    handle_finance_model(db, Job(type="ai.finance.model", payload={"model_id": str(m.id)}))
    db.refresh(m)
    assert m.assumptions["revenue_source"] == "actuals"


# --- value proposition fit-lines -----------------------------------------------------------------


def _vp(client, h):
    client.get(f"{BB}/canvases/value_prop", headers=h)
    client.put(
        f"{BB}/canvases/value_prop",
        json={
            "blocks": {
                "pains": ["Cash reconciliation"],
                "gains": ["Know who owes me"],
                "pain_relievers": ["Auto ledger"],
                "gain_creators": ["Debtor list"],
            },
            "version": 1,
        },
        headers=h,
    )


def test_fit_links_round_trip_and_prune(client, db):
    _s, [(_f, h)] = _workspace(db)
    _vp(client, h)
    links = [
        {
            "from_block": "pain_relievers",
            "from_item": "Auto ledger",
            "to_block": "pains",
            "to_item": "Cash reconciliation",
        },
        {
            "from_block": "gain_creators",
            "from_item": "Debtor list",
            "to_block": "gains",
            "to_item": "Know who owes me",
        },
    ]
    r = client.put(f"{BB}/canvases/value_prop/fit", json={"links": links}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["fit_links"] == links
    # Removing an item drops its links on read (never dangling lines).
    v = client.get(f"{BB}/canvases/value_prop", headers=h).json()["data"]["version"]
    client.put(
        f"{BB}/canvases/value_prop",
        json={
            "blocks": {
                "pains": [],
                "gains": ["Know who owes me"],
                "pain_relievers": ["Auto ledger"],
                "gain_creators": ["Debtor list"],
            },
            "version": v,
        },
        headers=h,
    )
    after = client.get(f"{BB}/canvases/value_prop", headers=h).json()["data"]
    assert after["fit_links"] == [links[1]]


def test_fit_links_validation(client, db):
    _s, [(_f, h), (_bc, h_bc)] = _workspace(db, MembershipRole.business_consultant)
    _vp(client, h)
    wrong_pair = [
        {
            "from_block": "pain_relievers",
            "from_item": "Auto ledger",
            "to_block": "gains",
            "to_item": "Know who owes me",
        }
    ]
    assert (
        client.put(
            f"{BB}/canvases/value_prop/fit", json={"links": wrong_pair}, headers=h
        ).status_code
        == 422
    )
    unknown = [
        {
            "from_block": "pain_relievers",
            "from_item": "Nope",
            "to_block": "pains",
            "to_item": "Cash reconciliation",
        }
    ]
    assert (
        client.put(f"{BB}/canvases/value_prop/fit", json={"links": unknown}, headers=h).status_code
        == 422
    )
    assert (
        client.put(f"{BB}/canvases/value_prop/fit", json={"links": []}, headers=h_bc).status_code
        == 403
    )
    assert "fit_links" not in client.get(f"{BB}/canvases/swot", headers=h).json()["data"]


def test_projection_growth_default_is_finance_only(client, db):
    """The runway MoM growth assumption is Finance data: only Finance roles get it as the
    projection default; other members see a 0% default (they can still pass growth_pct)."""
    from app.db.models.finance_runway import FinanceRunwaySettings

    s, [(_f, h), (_inv, h_inv)] = _workspace(db, MembershipRole.investor)
    db.add(FinanceRunwaySettings(startup_id=s.id, mom_growth_percent=7))
    db.flush()
    _streams(client, h)
    assert (
        client.get(f"{BB}/revenue-streams/projection", headers=h).json()["data"]["growth_pct"] == 7
    )
    inv = client.get(f"{BB}/revenue-streams/projection", headers=h_inv).json()["data"]
    assert inv["growth_pct"] == 0
