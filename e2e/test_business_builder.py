"""Live Business Builder (Module 08, Slice 1 — Canvas Core) journey: a founder
onboards, then walks the full canvas surface -- the empty overview grid, a
lazy-created canvas at v1 with its block scaffold, a full-replace PUT that
bumps it to v2 and flips its overview row from "start" to "continue", and the
deferred ai-fill seam (202 + a queued job, polled via `GET /jobs/{id}`).

Every response body along the way is captured to `e2e/_captures/business/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-business-builder.md` (Task 6). They must be REAL
bodies from this live run, complete and untrimmed.

Business Builder has no roadmap/assessment dependency, so onboarding here is
just steps 1-4 + complete -- no roadmap generation or assessment walk needed
(unlike e2e/test_dashboard.py / e2e/test_mission.py, which need the roadmap for
their own surfaces).
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_roadmap.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


_CANVAS_TYPES = {"business_model", "lean", "value_prop", "mission_vision", "swot"}


def test_business_builder_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- no roadmap/assessment dependency for this module.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. GET /business-builder/overview -- all 5 canvas types, none touched
        # yet, so every row is the "start" (0-filled) empty state.
        overview_empty = c.get("/api/v1/business-builder/overview", headers=wh)
        assert overview_empty.status_code == 200, overview_empty.text
        rows = overview_empty.json()["data"]
        assert {r["type"] for r in rows} == _CANVAS_TYPES
        assert all(r["status"] == "start" for r in rows), rows
        capture("business", "overview_empty", overview_empty)

        # 2. GET /business-builder/canvases/business_model -- lazy-creates the
        # row on first read: v1, the 9-block scaffold, all blocks empty.
        canvas_get = c.get("/api/v1/business-builder/canvases/business_model", headers=wh)
        assert canvas_get.status_code == 200, canvas_get.text
        canvas_get_data = canvas_get.json()["data"]
        assert canvas_get_data["type"] == "business_model"
        assert canvas_get_data["version"] == 1
        assert canvas_get_data["blocks"] == {
            "key_partners": [],
            "key_activities": [],
            "key_resources": [],
            "value_propositions": [],
            "customer_relationships": [],
            "channels": [],
            "customer_segments": [],
            "cost_structure": [],
            "revenue_streams": [],
        }
        block_def_keys = {b["key"] for b in canvas_get_data["block_defs"]}
        assert block_def_keys == set(canvas_get_data["blocks"])
        capture("business", "canvas_get", canvas_get)

        # 3. PUT /business-builder/canvases/business_model -- full-replace save
        # of one block, versioned off the v1 just fetched -> bumps to v2.
        canvas_put = c.put(
            "/api/v1/business-builder/canvases/business_model",
            headers=wh,
            json={"blocks": {"key_partners": ["Stripe", "AWS"]}, "version": 1},
        )
        assert canvas_put.status_code == 200, canvas_put.text
        canvas_put_data = canvas_put.json()["data"]
        assert canvas_put_data["version"] == 2
        assert canvas_put_data["blocks"]["key_partners"] == ["Stripe", "AWS"]
        capture("business", "canvas_put", canvas_put)

        # 4. GET /business-builder/overview again -- business_model's row now
        # reflects the one filled block (1 of 9 -> "continue"); the other 4
        # canvas types are untouched and still "start".
        overview_after = c.get("/api/v1/business-builder/overview", headers=wh)
        assert overview_after.status_code == 200, overview_after.text
        rows_after = {r["type"]: r for r in overview_after.json()["data"]}
        assert rows_after["business_model"]["status"] == "continue", rows_after["business_model"]
        assert rows_after["business_model"]["filled_blocks"] == 1
        for t in _CANVAS_TYPES - {"business_model"}:
            assert rows_after[t]["status"] == "start", rows_after[t]
        capture("business", "overview_after", overview_after)

        # 5. POST /business-builder/canvases/business_model/ai-fill -- deferred
        # job seam: 202 + a queued job (no worker consumes it yet).
        ai_fill = c.post("/api/v1/business-builder/canvases/business_model/ai-fill", headers=wh)
        assert ai_fill.status_code == 202, ai_fill.text
        ai_fill_data = ai_fill.json()["data"]
        assert ai_fill_data["status"] == "queued"
        job_id = ai_fill_data["job_id"]
        capture("business", "ai_fill", ai_fill)

        # 6. GET /jobs/{id} -- the enqueued job, still queued (no worker yet).
        ai_fill_job = c.get(f"/api/v1/jobs/{job_id}", headers=auth)
        assert ai_fill_job.status_code == 200, ai_fill_job.text
        ai_fill_job_data = ai_fill_job.json()["data"]
        assert ai_fill_job_data["id"] == job_id
        assert ai_fill_job_data["type"] == "business.canvas.ai_fill"
        assert ai_fill_job_data["status"] == "queued"
        capture("business", "ai_fill_job", ai_fill_job)
