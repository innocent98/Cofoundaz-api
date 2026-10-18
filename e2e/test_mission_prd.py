"""Live Today's Mission PRD journey (Module 04): today with milestone chips + empty reason + my
assigned tasks, the 7-day Upcoming projection, reordering it, completing a task, the Completed
log, and history with current/best streak.

A founder onboards to stage "validation" (roadmap generated inline). Weekend missions are switched
on first so the journey is the same on any day of the week (see e2e/test_mission.py). Every body is
captured to `e2e/_captures/mission_prd/*.json`, the source for docs/fe-integration-guide-mission-prd.md.
"""

import httpx


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_mission_prd_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={
                "step": 3,
                "industry": "Fintech",
                "business_model": "b2b",
                "stage": "validation",
            },
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        s = c.patch("/api/v1/missions/settings", headers=wh, json={"weekend_missions": True})
        assert s.status_code == 200, s.text

        today = c.get("/api/v1/missions/today", headers=wh)
        assert today.status_code == 200, today.text
        t = today.json()["data"]
        assert t["tasks"] and t["tasks"][0]["milestone"]["title"]
        assert t["empty_reason"] is None and t["assigned_to_me"] == []
        capture("mission_prd", "today", today)

        up = c.get("/api/v1/missions/upcoming", headers=wh)
        assert up.status_code == 200, up.text
        days = up.json()["data"]["days"]
        assert len(days) == 7 and days[0]["tasks"]
        capture("mission_prd", "upcoming", up)

        queue = [x["roadmap_task_id"] for d in days for x in d["tasks"]]
        reordered = [queue[1], queue[0], *queue[2:]]
        put = c.put(
            "/api/v1/missions/upcoming/order", headers=wh, json={"roadmap_task_ids": reordered}
        )
        assert put.status_code == 200, put.text
        first = put.json()["data"]["days"][0]["tasks"][0]["roadmap_task_id"]
        assert first == queue[1]
        capture("mission_prd", "upcoming_after_reorder", put)

        bad = c.put(
            "/api/v1/missions/upcoming/order",
            headers=wh,
            json={"roadmap_task_ids": [queue[0], queue[0]]},
        )
        assert bad.status_code == 422
        capture("mission_prd", "upcoming_order_duplicate_422", bad)

        task_id = t["tasks"][0]["id"]
        done = c.patch(f"/api/v1/missions/tasks/{task_id}", headers=wh, json={"action": "complete"})
        assert done.status_code == 200, done.text
        assert done.json()["data"]["milestone"] is not None
        capture("mission_prd", "task_completed", done)

        log = c.get("/api/v1/missions/completed", headers=wh)
        assert log.status_code == 200, log.text
        assert log.json()["data"]["items"][0]["id"] == task_id
        capture("mission_prd", "completed", log)

        hist = c.get("/api/v1/missions/history", headers=wh)
        assert {"current_streak", "best_streak"} <= set(hist.json()["data"])
        capture("mission_prd", "history", hist)
