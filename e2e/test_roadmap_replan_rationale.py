"""Live Module 03: ai.roadmap.rationale AI-authors a re-plan's rationale.

Onboard -> force a milestone slip -> POST /replan/preview -> POST /replan/apply (the applied
`RoadmapReplan` gets a templated fallback `rationale` + enqueues `ai.roadmap.rationale`) -> drain
the worker in-process (LLM_PROVIDER=stub, set by scripts/e2e_run.sh) -> GET /replan/history and
assert the newest re-plan's `rationale` now carries the stub marker.

Journey mirrors e2e/test_roadmap_replan.py (onboarding, slip-forcing, preview/apply shapes) --
_drain() is copied verbatim from e2e/test_records_ai_fill.py (it imports
app.worker.handlers.ai, which registers ai.roadmap.rationale alongside the other AI job types).

Every response body along the way is captured to
`e2e/_captures/roadmap_replan_rationale/*.json` -- the verbatim source for the FE guide's
re-plan-rationale section (Task 7).
"""

from datetime import date, timedelta

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text


def _wh(c: httpx.Client, auth: dict) -> dict:
    """Onboard just enough to have an active workspace, then build the X-Workspace-Id header
    the way `/auth/me` reports it (same pattern as e2e/test_roadmap.py::_wh)."""
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.roadmap.rationale)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_roadmap_replan_rationale(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard(c, auth)
        wh = _wh(c, auth)

        # 1. GET the tree -- lazily generates the roadmap.
        tree = c.get("/api/v1/roadmap", headers=wh)
        assert tree.status_code == 200, tree.text
        tree_data = tree.json()["data"]
        first_phase = tree_data["phases"][0]
        assert first_phase["milestones"], "expected at least one milestone in phase 1"
        milestone_id = first_phase["milestones"][0]["id"]

        # 2. Force a slip: push the first milestone's due date 10 days into the past.
        past = (date.today() - timedelta(days=10)).isoformat()
        slip = c.patch(
            f"/api/v1/roadmap/milestones/{milestone_id}", headers=wh, json={"due_on": past}
        )
        assert slip.status_code == 200, slip.text

        # 3. Preview -- proposes the dependency-aware cascade for the slipped milestone.
        preview = c.post("/api/v1/roadmap/replan/preview", headers=wh)
        assert preview.status_code == 200, preview.text
        preview_data = preview.json()["data"]
        assert preview_data["drift_count"] >= 1
        change = next(ch for ch in preview_data["changes"] if ch["milestone_id"] == milestone_id)

        # 4. Apply -- founder accepts that one proposed change. The new RoadmapReplan row
        # gets a non-null templated `rationale` immediately (the AI job hasn't run yet) and
        # enqueues `ai.roadmap.rationale` to overwrite it.
        apply_resp = c.post(
            "/api/v1/roadmap/replan/apply",
            headers=wh,
            json={"change_ids": [change["change_id"]]},
        )
        assert apply_resp.status_code == 200, apply_resp.text
        apply_data = apply_resp.json()["data"]
        assert apply_data["applied"] == [change["change_id"]]
        assert apply_data["rationale"], "expected the templated fallback rationale on apply"
        assert "[stub-llm]" not in apply_data["rationale"], (
            "the AI job hasn't run yet at apply time -- this must still be the templated "
            "fallback, not the stub-llm output"
        )
        capture("roadmap_replan_rationale", "apply", apply_resp)

        # 5. Drain the worker in-process -- runs `ai.roadmap.rationale` (LLM_PROVIDER=stub),
        # which overwrites the replan's rationale with the stub's prose output. (Onboarding
        # completion above also enqueued an unrelated `email.notification` job; the broad
        # drain processes that too, which is fine and not asserted on here.)
        _drain()

        # 6. History -- the newest re-plan (data[0]) now carries the stub-authored rationale.
        hist = c.get("/api/v1/roadmap/replan/history", headers=wh)
        assert hist.status_code == 200, hist.text
        newest = hist.json()["data"][0]
        assert newest["id"] == apply_data["replan_id"]
        assert "[stub-llm]" in newest["rationale"]
        capture("roadmap_replan_rationale", "history_after_drain", hist)
