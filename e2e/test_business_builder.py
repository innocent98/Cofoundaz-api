"""Live Business Builder journey, both slices:

- Slice 1 (Canvas Core) -- a founder onboards, then walks the full canvas
  surface -- the empty overview grid, a lazy-created canvas at v1 with its
  block scaffold, a full-replace PUT that bumps it to v2 and flips its
  overview row from "start" to "continue", and the deferred ai-fill seam
  (202 + a queued job, polled via `GET /jobs/{id}`).
- Slice 2 (Typed Artifacts) -- the same founder walks the generic `{kind}`
  records surface -- create a persona, list it back with its `fields`
  descriptors, full-replace-update it, watch the overview grow a `persona`
  record row, create a competitor and a pricing record (exercising the
  `threat_level`/`model_type` enum fields), and the same deferred ai-fill seam
  for a record kind.

Every response body along the way is captured to `e2e/_captures/business/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-business-builder.md` (Task 6, both slices). They
must be REAL bodies from this live run, complete and untrimmed.

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
# Slice 2 (Typed Artifacts) added 4 record-kind rows onto the same overview
# grid alongside the 5 canvas-type rows above -- see
# test_business_builder_records_journey for the dedicated records walk.
_RECORD_KINDS = {"persona", "revenue_stream", "competitor", "pricing"}


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

        # 1. GET /business-builder/overview -- all 5 canvas types + the 4
        # record kinds, none touched yet, so every row is the "start"
        # (0-filled/0-count) empty state.
        overview_empty = c.get("/api/v1/business-builder/overview", headers=wh)
        assert overview_empty.status_code == 200, overview_empty.text
        rows = overview_empty.json()["data"]
        assert {r["type"] for r in rows} == _CANVAS_TYPES | _RECORD_KINDS
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


def test_business_builder_records_journey(base_url, make_verified_user, capture):
    """Slice 2 (Typed Artifacts) records journey over the generic `{kind}`
    CRUD surface -- personas, competitors, pricing -- plus the record-kind
    ai-fill seam and the overview's new record rows."""
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a fresh founder (same shape as the canvas journey above).
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Records")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. POST /business-builder/personas -- create the first persona record.
        record_create = c.post(
            "/api/v1/business-builder/personas",
            headers=wh,
            json={
                "data": {
                    "name": "Busy Founder Bea",
                    "demographics": "28-40, solo/early-stage founder",
                    "goals": ["Ship an MVP", "Get first 10 customers"],
                    "frustrations": ["No time to research tools"],
                    "watering_holes": ["Indie Hackers", "Twitter/X"],
                    "quote": "I just need this to work.",
                }
            },
        )
        assert record_create.status_code == 201, record_create.text
        record_create_data = record_create.json()["data"]
        assert record_create_data["kind"] == "persona"
        assert record_create_data["data"]["name"] == "Busy Founder Bea"
        assert record_create_data["position"] == 0
        record_id = record_create_data["id"]
        capture("business", "record_create", record_create)

        # 2. GET /business-builder/personas -- ordered list + the `fields`
        # descriptors (FE renders a persona form from these, incl. `name`).
        record_list = c.get("/api/v1/business-builder/personas", headers=wh)
        assert record_list.status_code == 200, record_list.text
        record_list_data = record_list.json()["data"]
        assert [r["id"] for r in record_list_data["records"]] == [record_id]
        assert any(f["key"] == "name" for f in record_list_data["fields"])
        capture("business", "record_list", record_list)

        # 3. PUT /business-builder/personas/{id} -- full-replace update; any
        # omitted key resets to its default (same full-replace contract as
        # Slice 1's canvas PUT).
        record_update = c.put(
            f"/api/v1/business-builder/personas/{record_id}",
            headers=wh,
            json={"data": {"name": "Busy Founder Bea (Updated)", "goals": ["Ship an MVP"]}},
        )
        assert record_update.status_code == 200, record_update.text
        record_update_data = record_update.json()["data"]
        assert record_update_data["data"]["name"] == "Busy Founder Bea (Updated)"
        assert record_update_data["data"]["goals"] == ["Ship an MVP"]
        # Omitted keys reset to their schema default -- full replace, not merge.
        assert record_update_data["data"]["quote"] == ""
        assert record_update_data["data"]["demographics"] == ""
        capture("business", "record_update", record_update)

        # 4. GET /business-builder/overview -- the persona row is now present
        # and "complete" (>=1 record), count 1.
        overview_records = c.get("/api/v1/business-builder/overview", headers=wh)
        assert overview_records.status_code == 200, overview_records.text
        overview_rows = {r["type"]: r for r in overview_records.json()["data"]}
        assert overview_rows["persona"]["status"] == "complete", overview_rows["persona"]
        assert overview_rows["persona"]["count"] == 1
        assert overview_rows["persona"]["completion_pct"] == 100
        # The other 3 record kinds are still untouched.
        for kind in ("revenue_stream", "competitor", "pricing"):
            assert overview_rows[kind]["status"] == "start", overview_rows[kind]
            assert overview_rows[kind]["count"] == 0
        capture("business", "overview_records", overview_records)

        # 5. POST /business-builder/competitors -- exercises the `threat_level`
        # enum field.
        competitor_create = c.post(
            "/api/v1/business-builder/competitors",
            headers=wh,
            json={
                "data": {
                    "name": "BigCo Rival",
                    "positioning": "Enterprise incumbent",
                    "price": "$$$$",
                    "strengths": ["Brand recognition"],
                    "weaknesses": ["Slow to ship"],
                    "threat_level": "high",
                }
            },
        )
        assert competitor_create.status_code == 201, competitor_create.text
        competitor_create_data = competitor_create.json()["data"]
        assert competitor_create_data["kind"] == "competitor"
        assert competitor_create_data["data"]["threat_level"] == "high"
        capture("business", "competitor_create", competitor_create)

        # 5b. GET /business-builder/competitors -- live proof that `fields`
        # surfaces `threat_level`'s enum members as `choices` (the FE builds
        # its dropdown from this, not from a hardcoded list).
        competitor_list = c.get("/api/v1/business-builder/competitors", headers=wh)
        assert competitor_list.status_code == 200, competitor_list.text
        threat_field = next(
            f for f in competitor_list.json()["data"]["fields"] if f["key"] == "threat_level"
        )
        assert threat_field["choices"] == ["low", "medium", "high"]
        capture("business", "competitor_list", competitor_list)

        # 6. POST /business-builder/pricing -- exercises `model_type` (enum)
        # + `tiers` (nested list of PricingTier objects).
        pricing_create = c.post(
            "/api/v1/business-builder/pricing",
            headers=wh,
            json={
                "data": {
                    "model_type": "tiered",
                    "tiers": [
                        {"name": "Starter", "price": "$19/mo", "features": ["1 seat"]},
                        {
                            "name": "Pro",
                            "price": "$49/mo",
                            "features": ["5 seats", "Priority support"],
                        },
                    ],
                }
            },
        )
        assert pricing_create.status_code == 201, pricing_create.text
        pricing_create_data = pricing_create.json()["data"]
        assert pricing_create_data["kind"] == "pricing"
        assert pricing_create_data["data"]["model_type"] == "tiered"
        assert len(pricing_create_data["data"]["tiers"]) == 2
        capture("business", "pricing_create", pricing_create)

        # 6b. GET /business-builder/pricing -- live proof that `fields`
        # surfaces `model_type`'s 5 enum members as `choices`.
        pricing_list = c.get("/api/v1/business-builder/pricing", headers=wh)
        assert pricing_list.status_code == 200, pricing_list.text
        model_type_field = next(
            f for f in pricing_list.json()["data"]["fields"] if f["key"] == "model_type"
        )
        assert model_type_field["choices"] == [
            "subscription",
            "one_time",
            "usage",
            "freemium",
            "tiered",
        ]
        capture("business", "pricing_list", pricing_list)

        # 7. POST /business-builder/personas/ai-fill -- deferred job seam,
        # same contract as the Slice 1 canvas ai-fill (202 + queued job, no
        # worker consumes it yet).
        record_ai_fill = c.post("/api/v1/business-builder/personas/ai-fill", headers=wh)
        assert record_ai_fill.status_code == 202, record_ai_fill.text
        record_ai_fill_data = record_ai_fill.json()["data"]
        assert record_ai_fill_data["status"] == "queued"
        record_job_id = record_ai_fill_data["job_id"]
        capture("business", "record_ai_fill", record_ai_fill)

        # 8. GET /jobs/{id} -- the enqueued record ai-fill job, still queued.
        record_ai_fill_job = c.get(f"/api/v1/jobs/{record_job_id}", headers=auth)
        assert record_ai_fill_job.status_code == 200, record_ai_fill_job.text
        record_ai_fill_job_data = record_ai_fill_job.json()["data"]
        assert record_ai_fill_job_data["id"] == record_job_id
        assert record_ai_fill_job_data["type"] == "business.persona.ai_fill"
        assert record_ai_fill_job_data["status"] == "queued"
        capture("business", "record_ai_fill_job", record_ai_fill_job)
