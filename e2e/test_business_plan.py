"""Live §08.11: AI Business Plan Generator (Module 08, final slice; stub LLM).

Founder onboards, triggers plan generation (`POST /business-builder/plan/generate`, 202,
already shipped), we drain the worker in-process (LLM_PROVIDER=stub, set by
scripts/e2e_run.sh), then GET the plan (`complete` + `document_id`) and fetch the generated
Document (Module 18) to prove the section-by-section plan actually landed.
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    """Mirrors e2e/test_assessment.py / e2e/test_canvas_ai_fill.py: walk the wizard,
    complete, then read the workspace id off /auth/me and return headers scoped to it
    (X-Workspace-Id). Business Builder has no roadmap/assessment dependency, so steps
    1-4 + complete is enough."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )
    completed = c.post("/api/v1/onboarding/complete", headers=auth)
    assert completed.status_code == 200, completed.text

    me = c.get("/api/v1/auth/me", headers=auth)
    assert me.status_code == 200, me.text
    wsid = me.json()["data"]["active_workspace_id"]
    return {**auth, "X-Workspace-Id": wsid}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import plan as _plan  # noqa: F401  (registers business.plan.generate)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_business_plan_generation(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)

        gen = c.post("/api/v1/business-builder/plan/generate", headers=wh)
        assert gen.status_code == 202, gen.text
        assert gen.json()["data"]["status"] == "generating"
        capture("business_plan", "generate_enqueued", gen)

        _drain()

        got = c.get("/api/v1/business-builder/plan", headers=wh)
        assert got.status_code == 200, got.text
        plan_data = got.json()["data"]
        assert plan_data["status"] == "complete", plan_data
        doc_id = plan_data["document_id"]
        assert doc_id
        capture("business_plan", "plan_complete", got)

        doc = c.get(f"/api/v1/documents/{doc_id}", headers=wh)
        assert doc.status_code == 200, doc.text
        doc_data = doc.json()["data"]
        assert doc_data["kind"] == "business_plan"
        assert doc_data["ai_generated"] is True
        sections = doc_data["sections"]
        assert len(sections) == 10
        # The stub LLM returns the SAME fixed string for every section -- proves the
        # worker called the LLM seam once per PLAN_SECTIONS entry, not that the
        # content is section-specific (that needs a real LLM_PROVIDER=openai run).
        assert all("[stub-llm]" in s["body"] for s in sections), sections
        assert sections[0]["heading"] == "Executive Summary"
        capture("business_plan", "plan_document", doc)
