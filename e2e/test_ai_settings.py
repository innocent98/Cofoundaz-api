"""Live: Settings -> AI preferences (GET/PATCH /ai/settings) and the dashboard briefing switch.

Captures land in e2e/_captures/ai_settings/ and are the verbatim source of
docs/fe-integration-guide-ai-settings.md.
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    wsid = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
    return {**auth, "X-Workspace-Id": wsid}


def test_ai_settings_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _onboard(c, _auth(access))

        r = c.get("/api/v1/ai/settings", headers=h)
        assert r.status_code == 200 and r.json()["data"]["tone"] == "encouraging"
        capture("ai_settings", "defaults", r)

        r = c.patch("/api/v1/ai/settings", headers=h, json={"tone": "straight_shooter"})
        assert r.status_code == 200 and r.json()["data"]["tone"] == "straight_shooter"
        capture("ai_settings", "patch_tone", r)

        r = c.patch(
            "/api/v1/ai/settings",
            headers=h,
            json={"daily_briefing": False, "proactive_suggestions": False},
        )
        assert r.status_code == 200
        capture("ai_settings", "patch_toggles_off", r)
        assert c.get("/api/v1/ai/settings", headers=h).json()["data"]["tone"] == "straight_shooter"

        r = c.get("/api/v1/dashboard/summary", headers=h)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["briefing"]["status"] == "off" and d["opportunities"]["status"] == "off"
        capture("ai_settings", "dashboard_briefing_off", r)

        r = c.patch("/api/v1/ai/settings", headers=h, json={"daily_briefing": True})
        assert r.json()["data"]["daily_briefing"] is True
        d = c.get("/api/v1/dashboard/summary", headers=h).json()["data"]
        assert d["briefing"]["status"] in ("empty", "generating", "ready")
        capture_name = "dashboard_briefing_back_on"
        capture("ai_settings", capture_name, c.get("/api/v1/dashboard/summary", headers=h))

        for name, body in (
            ("error_bad_tone", {"tone": "sarcastic"}),
            ("error_unknown_field", {"memory": True}),
            ("error_empty", {}),
        ):
            r = c.patch("/api/v1/ai/settings", headers=h, json=body)
            assert r.status_code == 422, (name, r.text)
            capture("ai_settings", name, r)
