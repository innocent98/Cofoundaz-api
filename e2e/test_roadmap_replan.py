"""Live AI Re-plan journey: a founder's roadmap drifts (a milestone slips into
the past), `POST /replan/preview` proposes a deterministic date cascade,
`POST /replan/apply` commits the accepted change (stamping the milestone's
`replanned` marker and writing one `roadmap_replans` history row), and the
tree + history surface both reflect it afterwards.

Every response body along the way is captured to
`e2e/_captures/roadmap/replan_*.json` / `get_tree_replanned.json` -- those are
the verbatim source for `docs/fe-integration-guide-roadmap.md`'s re-plan
section.
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
    """Onboard just enough to have an active workspace, then build the
    X-Workspace-Id header the way `/auth/me` reports it (same pattern as
    e2e/test_roadmap.py::_wh)."""
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def test_roadmap_replan_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard(c, auth)
        wh = _wh(c, auth)

        # 1. GET the tree -- lazily generates the roadmap. Generation stamps
        # every milestone's due_on from today, so there is no drift yet.
        tree = c.get("/api/v1/roadmap", headers=wh)
        assert tree.status_code == 200, tree.text
        tree_data = tree.json()["data"]
        assert tree_data["roadmap"]["drift"]["slipped_count"] == 0
        first_phase = tree_data["phases"][0]
        assert first_phase["milestones"], "expected at least one milestone in phase 1"
        milestone_id = first_phase["milestones"][0]["id"]
        assert first_phase["milestones"][0]["replanned"] is None

        # 2. Force a slip: push the first milestone's due date 10 days into
        # the past.
        past = (date.today() - timedelta(days=10)).isoformat()
        slip = c.patch(
            f"/api/v1/roadmap/milestones/{milestone_id}", headers=wh, json={"due_on": past}
        )
        assert slip.status_code == 200, slip.text
        assert slip.json()["data"]["due_on"] == past

        # 3. Preview -- proposes (never applies) a dependency-aware cascade of
        # date shifts for every slipped milestone.
        preview = c.post("/api/v1/roadmap/replan/preview", headers=wh)
        assert preview.status_code == 200, preview.text
        preview_data = preview.json()["data"]
        assert preview_data["drift_count"] >= 1
        change = next(ch for ch in preview_data["changes"] if ch["milestone_id"] == milestone_id)
        capture("roadmap", "replan_preview", preview)

        # 4. Apply -- founder accepts just that one proposed change.
        apply = c.post(
            "/api/v1/roadmap/replan/apply",
            headers=wh,
            json={"change_ids": [change["change_id"]]},
        )
        assert apply.status_code == 200, apply.text
        apply_data = apply.json()["data"]
        assert apply_data["applied"] == [change["change_id"]]
        assert apply_data["skipped"] == []
        assert apply_data["replan_id"]
        capture("roadmap", "replan_apply", apply)

        # 5. Re-GET the tree -- the milestone's due_on moved, its `replanned`
        # marker is set with a reason, and the roadmap-level drift count
        # dropped back to zero (the only slipped milestone was just fixed).
        after = c.get("/api/v1/roadmap", headers=wh)
        assert after.status_code == 200, after.text
        after_data = after.json()["data"]
        all_milestones = [m for p in after_data["phases"] for m in p["milestones"]]
        our_milestone = next(m for m in all_milestones if m["id"] == milestone_id)
        assert our_milestone["due_on"] == change["new_due"]
        assert our_milestone["replanned"] is not None
        assert our_milestone["replanned"]["reason"]
        assert after_data["roadmap"]["drift"]["slipped_count"] < preview_data["drift_count"]
        capture("roadmap", "get_tree_replanned", after)

        # 6. History -- one applied re-plan, with the change snapshot embedded.
        history = c.get("/api/v1/roadmap/replan/history", headers=wh)
        assert history.status_code == 200, history.text
        history_data = history.json()["data"]
        assert len(history_data) == 1
        assert history_data[0]["change_count"] == 1
        assert history_data[0]["changes"][0]["milestone_id"] == milestone_id
        capture("roadmap", "replan_history", history)
