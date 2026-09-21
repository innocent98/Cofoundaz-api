"""Live Business Builder journey, all three slices:

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
- Slice 3 (Suggestions + Positioning Map) -- a founder + a `business_consultant`
  teammate in the SAME workspace: the consultant suggests all four ops
  (canvas_update, record_create, record_update, record_delete), is forbidden
  from approving their own suggestion, the founder lists/approves/rejects and
  hits both 409s (`SUGGESTION_NOT_PENDING`, `CANVAS_VERSION_CONFLICT`); then
  the founder walks the positioning-map endpoints, including the
  coordinate-on-competitor trap (coords are edited via `PUT /competitors/{id}`,
  not the map endpoint).

Every response body along the way is captured to `e2e/_captures/business/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-business-builder.md` /
`docs/fe-integration-guide-business-builder-suggestions.md` (Task 6). They
must be REAL bodies from this live run, complete and untrimmed.

Business Builder has no roadmap/assessment dependency, so onboarding here is
just steps 1-4 + complete -- no roadmap generation or assessment walk needed
(unlike e2e/test_dashboard.py / e2e/test_mission.py, which need the roadmap for
their own surfaces). Slice 3's consultant invite is the one exception: the
invite must be sent while onboarding is still a draft (same constraint
documented in e2e/test_roadmap.py::_onboard_steps), so that journey invites
BEFORE calling `/onboarding/complete`.
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _wh(c: httpx.Client, auth: dict) -> dict:
    """Build the X-Workspace-Id header from `/auth/me` (same pattern as
    e2e/test_roadmap.py::_wh)."""
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _signup_verify_login(c: httpx.Client, mailbox, email: str, password: str) -> dict:
    """Sign up + verify + log in a specific (email, password) -- used for the
    invited business_consultant, who must sign up with the email the invite
    was sent to (same helper as e2e/test_roadmap.py)."""
    c.post("/api/v1/auth/signup", json={"email": email, "password": password})
    token = mailbox.latest_token_for(email, subject_contains="Verify")
    c.post("/api/v1/auth/verify", json={"token": token})
    access = c.post("/api/v1/auth/login", json={"email": email, "password": password}).json()[
        "data"
    ]["access_token"]
    return _auth_header(access)


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


def test_business_suggestions_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    """Slice 3 (Suggestions) -- a founder + a `business_consultant` teammate in
    the SAME workspace. The consultant suggests all four ops; the founder is
    the only one who can approve/reject (role in `_editor` = founder,
    team_member -- `business_consultant` is NOT). Covers the full state
    machine, both 409s, and the `current`-vs-`payload` diff contract (`current`
    is null for record_create, non-null for canvas_update/record_update)."""
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Founder onboards through steps 1-4 (NOT /complete yet -- the
        # consultant invite must be sent while onboarding is still a draft,
        # same constraint as e2e/test_roadmap.py).
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Suggestions")

        consultant_email = unique_email("consultant")
        inv = c.post(
            "/api/v1/onboarding/invites",
            headers=auth,
            json={"invites": [{"email": consultant_email, "role": "business_consultant"}]},
        )
        assert inv.status_code == 200, inv.text
        assert set(inv.json()["data"]["created"]) == {consultant_email}
        consultant_token = mailbox.latest_token_for(consultant_email, subject_contains="invited")

        # Consultant signs up (email MUST match the invite) + verifies + accepts.
        consultant_pw = "Consult-" + consultant_email.split("@")[0] + "-9"
        consultant_auth = _signup_verify_login(c, mailbox, consultant_email, consultant_pw)
        consultant_accept = c.post(
            "/api/v1/invitations/accept", json={"token": consultant_token}, headers=consultant_auth
        )
        assert consultant_accept.status_code == 200, consultant_accept.text
        assert consultant_accept.json()["data"]["role"] == "business_consultant"

        # Founder completes onboarding now that the invite has landed.
        done = c.post("/api/v1/onboarding/complete", headers=auth)
        assert done.status_code == 200, done.text

        wh = _wh(c, auth)
        consultant_wh = _wh(c, consultant_auth)

        # 1. Founder GETs the business_model canvas -- lazy-creates it at v1
        # (same lazy-create as the Slice 1 journey above).
        canvas_get = c.get("/api/v1/business-builder/canvases/business_model", headers=wh)
        assert canvas_get.status_code == 200, canvas_get.text
        assert canvas_get.json()["data"]["version"] == 1
        capture("business", "suggestions_canvas_get", canvas_get)

        # 2. Consultant POSTs a canvas_update suggestion -- all business_model
        # blocks are list-kind (only mission_vision is text), so the payload
        # value MUST be a list, not a bare string.
        suggestion_create = c.post(
            "/api/v1/business-builder/suggestions",
            headers=consultant_wh,
            json={
                "op": "canvas_update",
                "target": {"canvas_type": "business_model"},
                "payload": {"blocks": {"key_partners": ["Acme Corp"]}},
                "note": "Add Acme as a key partner",
            },
        )
        assert suggestion_create.status_code == 201, suggestion_create.text
        suggestion_data = suggestion_create.json()["data"]
        assert suggestion_data["op"] == "canvas_update"
        assert suggestion_data["status"] == "pending"
        assert suggestion_data["base_version"] == 1
        # `current` is the canvas state AT SUGGESTION TIME -- not yet applied.
        assert suggestion_data["current"] == {
            "blocks": canvas_get.json()["data"]["blocks"],
            "version": 1,
        }
        assert suggestion_data["author"]["email"] == consultant_email
        suggestion_id = suggestion_data["id"]
        capture("business", "suggestions_create_canvas_update", suggestion_create)

        # 3. Consultant tries to approve their own suggestion -- 403. Role rule:
        # any member can suggest, only an editor (founder/team_member) approves.
        consultant_approve = c.post(
            f"/api/v1/business-builder/suggestions/{suggestion_id}/approve", headers=consultant_wh
        )
        assert consultant_approve.status_code == 403, consultant_approve.text
        assert consultant_approve.json()["error"]["code"] == "FORBIDDEN"
        capture("business", "suggestions_approve_forbidden", consultant_approve)

        # 4. Founder lists pending suggestions -- sees it.
        list_pending = c.get(
            "/api/v1/business-builder/suggestions", headers=wh, params={"status": "pending"}
        )
        assert list_pending.status_code == 200, list_pending.text
        pending_ids = [s["id"] for s in list_pending.json()["data"]["suggestions"]]
        assert suggestion_id in pending_ids
        capture("business", "suggestions_list_pending", list_pending)

        # 5. Founder approves -- applies canvas_update through the existing
        # save_canvas() write path, bumping the canvas to v2.
        approve = c.post(
            f"/api/v1/business-builder/suggestions/{suggestion_id}/approve", headers=wh
        )
        assert approve.status_code == 200, approve.text
        approve_data = approve.json()["data"]
        assert approve_data["status"] == "approved"
        assert approve_data["resolved_by"] is not None
        assert (
            approve_data["resolved_by"]["id"] != suggestion_data["author"]["id"]
        )  # founder, not consultant
        assert approve_data["resolved_at"] is not None
        capture("business", "suggestions_approve_canvas_update", approve)

        # 6. GET the canvas -- reflects the approved change: v2, key_partners
        # filled (save_canvas() full-replaces against empty_blocks(), so every
        # OTHER block resets to its empty value -- same full-replace contract
        # as the Slice 1 canvas PUT).
        canvas_after = c.get("/api/v1/business-builder/canvases/business_model", headers=wh)
        assert canvas_after.status_code == 200, canvas_after.text
        canvas_after_data = canvas_after.json()["data"]
        assert canvas_after_data["version"] == 2
        assert canvas_after_data["blocks"]["key_partners"] == ["Acme Corp"]
        assert canvas_after_data["blocks"]["key_activities"] == []
        capture("business", "suggestions_canvas_after_approve", canvas_after)

        # 7. Approving the same suggestion again -- 409 SUGGESTION_NOT_PENDING.
        # The state machine only allows approve/reject from `pending`.
        approve_again = c.post(
            f"/api/v1/business-builder/suggestions/{suggestion_id}/approve", headers=wh
        )
        assert approve_again.status_code == 409, approve_again.text
        assert approve_again.json()["error"]["code"] == "SUGGESTION_NOT_PENDING"
        capture("business", "suggestions_not_pending", approve_again)

        # 8. Consultant creates a SECOND canvas_update suggestion, pinned to the
        # canvas's current version (2) via base_version.
        suggestion2 = c.post(
            "/api/v1/business-builder/suggestions",
            headers=consultant_wh,
            json={
                "op": "canvas_update",
                "target": {"canvas_type": "business_model"},
                "payload": {"blocks": {"key_activities": ["Customer support"]}},
                "note": "Add customer support as a key activity",
            },
        )
        assert suggestion2.status_code == 201, suggestion2.text
        suggestion2_data = suggestion2.json()["data"]
        assert suggestion2_data["base_version"] == 2
        suggestion2_id = suggestion2_data["id"]

        # 9. Founder directly PUTs the canvas (moving it to v3) BEFORE
        # resolving suggestion 2 -- simulates a concurrent edit.
        canvas_direct_put = c.put(
            "/api/v1/business-builder/canvases/business_model",
            headers=wh,
            json={"blocks": {"key_partners": ["Acme Corp", "Umbrella Corp"]}, "version": 2},
        )
        assert canvas_direct_put.status_code == 200, canvas_direct_put.text
        assert canvas_direct_put.json()["data"]["version"] == 3

        # 10. Founder approves suggestion 2 -- 409 CANVAS_VERSION_CONFLICT: the
        # canvas moved (v2 -> v3) since the suggestion was made. The suggestion
        # stays pending (the apply-then-persist transaction rolls back).
        approve_conflict = c.post(
            f"/api/v1/business-builder/suggestions/{suggestion2_id}/approve", headers=wh
        )
        assert approve_conflict.status_code == 409, approve_conflict.text
        assert approve_conflict.json()["error"]["code"] == "CANVAS_VERSION_CONFLICT"
        capture("business", "suggestions_version_conflict", approve_conflict)

        still_pending = c.get(
            "/api/v1/business-builder/suggestions", headers=wh, params={"status": "pending"}
        )
        assert suggestion2_id in [s["id"] for s in still_pending.json()["data"]["suggestions"]]

        # Founder rejects suggestion 2 instead -- clears it out of `pending`.
        reject2 = c.post(
            f"/api/v1/business-builder/suggestions/{suggestion2_id}/reject", headers=wh
        )
        assert reject2.status_code == 200, reject2.text
        assert reject2.json()["data"]["status"] == "rejected"
        capture("business", "suggestions_reject", reject2)

        # 11. Consultant suggests a record_create (persona). `current` is null
        # for record_create -- there is no prior state for a not-yet-created
        # record.
        suggest_record_create = c.post(
            "/api/v1/business-builder/suggestions",
            headers=consultant_wh,
            json={
                "op": "record_create",
                "target": {"kind": "persona"},
                "payload": {"data": {"name": "Suggested Persona"}},
                "note": "Add a persona for our target user",
            },
        )
        assert suggest_record_create.status_code == 201, suggest_record_create.text
        suggest_record_create_data = suggest_record_create.json()["data"]
        assert suggest_record_create_data["current"] is None
        record_create_suggestion_id = suggest_record_create_data["id"]
        capture("business", "suggestions_create_record_create", suggest_record_create)

        # 12. Founder approves -- creates the persona record for real.
        approve_record_create = c.post(
            f"/api/v1/business-builder/suggestions/{record_create_suggestion_id}/approve",
            headers=wh,
        )
        assert approve_record_create.status_code == 200, approve_record_create.text
        capture("business", "suggestions_approve_record_create", approve_record_create)

        personas_after_create = c.get("/api/v1/business-builder/personas", headers=wh)
        assert personas_after_create.status_code == 200, personas_after_create.text
        personas_records = personas_after_create.json()["data"]["records"]
        assert [r["data"]["name"] for r in personas_records] == ["Suggested Persona"]
        persona_id = personas_records[0]["id"]
        capture("business", "suggestions_personas_after_create", personas_after_create)

        # 13. Consultant suggests a record_update against that persona.
        # `current` now reflects the EXISTING record data -- non-null, unlike
        # record_create.
        suggest_record_update = c.post(
            "/api/v1/business-builder/suggestions",
            headers=consultant_wh,
            json={
                "op": "record_update",
                "target": {"kind": "persona", "record_id": persona_id},
                "payload": {
                    "data": {
                        "name": "Suggested Persona (Updated)",
                        "quote": "I just need this to work.",
                    }
                },
                "note": "Flesh out the persona quote",
            },
        )
        assert suggest_record_update.status_code == 201, suggest_record_update.text
        suggest_record_update_data = suggest_record_update.json()["data"]
        assert suggest_record_update_data["current"] == {"data": personas_records[0]["data"]}
        record_update_suggestion_id = suggest_record_update_data["id"]
        capture("business", "suggestions_create_record_update", suggest_record_update)

        # 14. Founder approves -- applies the update.
        approve_record_update = c.post(
            f"/api/v1/business-builder/suggestions/{record_update_suggestion_id}/approve",
            headers=wh,
        )
        assert approve_record_update.status_code == 200, approve_record_update.text
        capture("business", "suggestions_approve_record_update", approve_record_update)

        personas_after_update = c.get("/api/v1/business-builder/personas", headers=wh)
        assert personas_after_update.json()["data"]["records"][0]["data"]["name"] == (
            "Suggested Persona (Updated)"
        )

        # 15. Consultant suggests a record_delete for the same persona.
        suggest_record_delete = c.post(
            "/api/v1/business-builder/suggestions",
            headers=consultant_wh,
            json={
                "op": "record_delete",
                "target": {"kind": "persona", "record_id": persona_id},
                "note": "This persona turned out to be a duplicate",
            },
        )
        assert suggest_record_delete.status_code == 201, suggest_record_delete.text
        suggest_record_delete_data = suggest_record_delete.json()["data"]
        assert suggest_record_delete_data["payload"] is None
        record_delete_suggestion_id = suggest_record_delete_data["id"]
        capture("business", "suggestions_create_record_delete", suggest_record_delete)

        # 16. Founder approves -- deletes the record for real.
        approve_record_delete = c.post(
            f"/api/v1/business-builder/suggestions/{record_delete_suggestion_id}/approve",
            headers=wh,
        )
        assert approve_record_delete.status_code == 200, approve_record_delete.text
        capture("business", "suggestions_approve_record_delete", approve_record_delete)

        personas_after_delete = c.get("/api/v1/business-builder/personas", headers=wh)
        assert personas_after_delete.json()["data"]["records"] == []
        capture("business", "suggestions_personas_after_delete", personas_after_delete)


def test_business_positioning_map_journey(base_url, make_verified_user, capture):
    """Slice 3 (Positioning Map) -- defaults, editable axes, and the
    coordinate-on-competitor trap: `map_x`/`map_y` live on the competitor
    RECORD (`PUT /competitors/{id}`), NOT on the positioning-map endpoint --
    `PUT /positioning-map` only ever touches `axes`."""
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Positioning")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text
        wh = _wh(c, auth)

        # 1. GET /positioning-map -- lazily creates the singleton axes row with
        # the default Price/Quality axes; no competitors yet.
        map_default = c.get("/api/v1/business-builder/positioning-map", headers=wh)
        assert map_default.status_code == 200, map_default.text
        map_default_data = map_default.json()["data"]
        assert map_default_data["axes"] == {
            "x": {"label": "Price", "low": "Low", "high": "High"},
            "y": {"label": "Quality", "low": "Low", "high": "High"},
        }
        assert map_default_data["competitors"] == []
        capture("business", "positioning_get_default", map_default)

        # 2. PUT new axes.
        put_axes = c.put(
            "/api/v1/business-builder/positioning-map",
            headers=wh,
            json={
                "axes": {
                    "x": {"label": "Growth Rate", "low": "Slow", "high": "Fast"},
                    "y": {"label": "Retention", "low": "Poor", "high": "Great"},
                }
            },
        )
        assert put_axes.status_code == 200, put_axes.text
        assert put_axes.json()["data"]["axes"]["x"]["label"] == "Growth Rate"
        capture("business", "positioning_put_axes", put_axes)

        # 3. POST a competitor WITH map_x/map_y -- coords live on the record.
        competitor_create = c.post(
            "/api/v1/business-builder/competitors",
            headers=wh,
            json={
                "data": {
                    "name": "BigCo Rival",
                    "positioning": "Enterprise incumbent",
                    "threat_level": "high",
                    "map_x": 0.7,
                    "map_y": 0.3,
                }
            },
        )
        assert competitor_create.status_code == 201, competitor_create.text
        competitor_id = competitor_create.json()["data"]["id"]
        assert competitor_create.json()["data"]["data"]["map_x"] == 0.7
        capture("business", "positioning_competitor_create", competitor_create)

        # 4. GET /positioning-map -- the competitor now appears with coords.
        map_with_competitor = c.get("/api/v1/business-builder/positioning-map", headers=wh)
        assert map_with_competitor.status_code == 200, map_with_competitor.text
        comp_row = map_with_competitor.json()["data"]["competitors"][0]
        assert comp_row["id"] == competitor_id
        assert comp_row["x"] == 0.7
        assert comp_row["y"] == 0.3
        assert comp_row["threat_level"] == "high"
        capture("business", "positioning_get_with_competitor", map_with_competitor)

        # 5. THE TRAP: PUT /positioning-map does NOT accept competitor coords --
        # `PositioningMapSave` only has an `axes` field, so an extraneous
        # `competitors` key is silently ignored by pydantic, not applied.
        map_ignores_coords = c.put(
            "/api/v1/business-builder/positioning-map",
            headers=wh,
            json={
                "axes": {
                    "x": {"label": "Growth Rate", "low": "Slow", "high": "Fast"},
                    "y": {"label": "Retention", "low": "Poor", "high": "Great"},
                },
                "competitors": [{"id": competitor_id, "map_x": 0.99, "map_y": 0.99}],
            },
        )
        assert map_ignores_coords.status_code == 200, map_ignores_coords.text
        capture("business", "positioning_map_ignores_coords", map_ignores_coords)

        map_unchanged = c.get("/api/v1/business-builder/positioning-map", headers=wh)
        unchanged_comp = map_unchanged.json()["data"]["competitors"][0]
        assert unchanged_comp["x"] == 0.7  # unchanged -- the PUT above was a no-op on coords
        assert unchanged_comp["y"] == 0.3
        capture("business", "positioning_map_unchanged_after_trap", map_unchanged)

        # 6. THE FIX: coords are edited via PUT /competitors/{id} -- the
        # existing record full-replace endpoint, same as any other record field.
        competitor_coords_update = c.put(
            f"/api/v1/business-builder/competitors/{competitor_id}",
            headers=wh,
            json={
                "data": {
                    "name": "BigCo Rival",
                    "positioning": "Enterprise incumbent",
                    "threat_level": "high",
                    "map_x": 0.85,
                    "map_y": 0.15,
                }
            },
        )
        assert competitor_coords_update.status_code == 200, competitor_coords_update.text
        assert competitor_coords_update.json()["data"]["data"]["map_x"] == 0.85
        capture("business", "positioning_competitor_coords_update", competitor_coords_update)

        # 7. GET /positioning-map -- reflects the NEW coords, set through the
        # competitor record, not through the map endpoint.
        map_after_fix = c.get("/api/v1/business-builder/positioning-map", headers=wh)
        assert map_after_fix.status_code == 200, map_after_fix.text
        fixed_comp = map_after_fix.json()["data"]["competitors"][0]
        assert fixed_comp["x"] == 0.85
        assert fixed_comp["y"] == 0.15
        capture("business", "positioning_get_after_coord_fix", map_after_fix)
