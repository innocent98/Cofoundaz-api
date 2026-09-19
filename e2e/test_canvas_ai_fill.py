"""Live Module 03 Slice 2: structured output drafts a business canvas.

Founder triggers POST /business-builder/canvases/business_model/ai-fill (202, already shipped),
we drain the worker in-process (LLM_PROVIDER=stub, set by scripts/e2e_run.sh), then GET the
canvas and assert its blocks were filled with the stub's structured output.
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    """Mirrors e2e/test_assessment.py: walk the wizard, complete, then read the workspace id
    off /auth/me and return headers scoped to it (X-Workspace-Id)."""
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
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers business.canvas.ai_fill)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_canvas_ai_fill(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)

        fill = c.post("/api/v1/business-builder/canvases/business_model/ai-fill", headers=wh)
        assert fill.status_code == 202, fill.text
        capture("canvas_ai_fill", "ai_fill_enqueued", fill)

        _drain()

        got = c.get("/api/v1/business-builder/canvases/business_model", headers=wh)
        assert got.status_code == 200, got.text
        data = got.json()["data"]
        blocks = data["blocks"]
        assert data["version"] == 2  # bumped once by the worker's fill-empties-only merge
        assert data["completion"]["status"] == "complete"
        # business_model's blocks are all kind="list" -- the stub returns ["[stub-llm] <key>"] per key.
        assert all(
            isinstance(v, list) and v and "[stub-llm]" in v[0] for v in blocks.values()
        ), blocks
        capture("canvas_ai_fill", "canvas_after_fill", got)
