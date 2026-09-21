"""Live: GET /ai/status returns the workspace's token usage vs budget (stub accrues 0).

Founder onboards (wizard steps 1-4 + complete, mirroring e2e/test_records_ai_fill.py's
`_onboard` helper) to get a workspace + X-Workspace-Id header, then reads
GET /api/v1/ai/status. Under LLM_PROVIDER=stub (set by scripts/e2e_run.sh) no AI job has
run for this fresh workspace, so token usage is 0 and nothing is ever over budget.

Response body captured to e2e/_captures/ai_status/status.json -- the verbatim source for
docs/fe-integration-guide-ai.md (Task 8).
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    """Mirrors e2e/test_records_ai_fill.py: walk the wizard, complete, then read the
    workspace id off /auth/me and return headers scoped to it (X-Workspace-Id)."""
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


def test_ai_status(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)

        resp = c.get("/api/v1/ai/status", headers=wh)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["tokens_used_today"] == 0  # stub debits 0 -- nothing has run yet
        assert data["over_budget"] is False
        assert "daily_budget" in data  # int, or null when unbudgeted
        assert "resets_at" in data and data["resets_at"]
        assert data["recent_enrichment_failures"] == []
        capture("ai_status", "status", resp)
