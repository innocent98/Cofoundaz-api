"""Live "Today's Mission" journey: a founder onboards to stage "validation"
(which auto-generates the roadmap inline, from `app/services/onboarding/
complete.py` calling `generate_roadmap` synchronously), then walks the whole
mission surface -- lazy on-read generation from the roadmap, the settings
defaults, completing every task until the mission flips to `complete`, adding a
custom (roadmap-less) task, and the history rollup.

Every response body along the way is captured to `e2e/_captures/mission/*.json`
-- those files are the verbatim source for `docs/fe-integration-guide-mission.md`
(Task 7). They must be REAL bodies from this live run, complete and untrimmed.

Note on the weekend guard: `get_or_generate_today` materialises an *empty*
mission on Sat/Sun when `weekend_missions` is off (the product's "weekends off"
behaviour). Because generation is lazy-on-read and cached for the day, this test
enables `weekend_missions` BEFORE the first `/today` call *only when the live
server clock says it is a weekend* -- so the roadmap-drawn assertions below hold
every day of the week without ever distorting the captured settings defaults
(which are read and asserted before any such patch).
"""

from datetime import date

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


def _wh(c: httpx.Client, auth: dict) -> dict:
    """Build the X-Workspace-Id header the way `/auth/me` reports it (same pattern
    as e2e/test_roadmap.py::_wh)."""
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def test_mission_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder to stage "validation" -> roadmap generates inline
        # (generate_roadmap is called synchronously from complete_onboarding), so
        # incomplete roadmap tasks exist for the mission to draw from.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        done = c.post("/api/v1/onboarding/complete", headers=auth)
        assert done.status_code == 200, done.text

        wh = _wh(c, auth)

        # Sanity: the roadmap really is there (mission draws FROM it).
        tree = c.get("/api/v1/roadmap", headers=wh)
        assert tree.status_code == 200, tree.text
        roadmap_task_ids = {
            t["id"]
            for p in tree.json()["data"]["phases"]
            for m in p["milestones"]
            for t in m["tasks"]
        }
        assert roadmap_task_ids, "expected the validation-stage roadmap to seed tasks"

        # 1. GET /missions/settings -- defaults are lazily created on first read.
        # Captured BEFORE any weekend guard so the defaults are the honest ones.
        settings = c.get("/api/v1/missions/settings", headers=wh)
        assert settings.status_code == 200, settings.text
        settings_data = settings.json()["data"]
        assert settings_data["mission_size"] == 3
        assert settings_data["weekend_missions"] is False
        capture("mission", "settings", settings)

        # Weekend guard (see module docstring): only on Sat/Sun, and only *before*
        # today's mission is generated, so the roadmap-drawn assertions hold.
        if date.today().weekday() in (5, 6):
            wk = c.patch("/api/v1/missions/settings", headers=wh, json={"weekend_missions": True})
            assert wk.status_code == 200, wk.text

        # 2. GET /missions/today -- lazy generation from the roadmap. Returns 1-3
        # tasks, each linking back to a roadmap task via `roadmap_task_id` and
        # carrying the templated `reason`.
        today = c.get("/api/v1/missions/today", headers=wh)
        assert today.status_code == 200, today.text
        today_data = today.json()["data"]
        assert today_data["status"] == "pending"
        assert today_data["streak"] == 0
        tasks = today_data["tasks"]
        assert 1 <= len(tasks) <= 3, f"expected 1-3 mission tasks, got {len(tasks)}"
        for t in tasks:
            assert t["roadmap_task_id"] in roadmap_task_ids, "task must link back to the roadmap"
            assert t["reason"] and t["reason"].startswith("From your '"), t["reason"]
            assert t["status"] == "todo"
        capture("mission", "today", today)

        # 2b. Idempotent: a second read returns the SAME mission (same tasks), not
        # a freshly-generated one -- generation is once-per-day.
        today_again = c.get("/api/v1/missions/today", headers=wh)
        assert today_again.status_code == 200, today_again.text
        assert [t["id"] for t in today_again.json()["data"]["tasks"]] == [t["id"] for t in tasks]

        # 3. Complete every roadmap-drawn task. The FINAL completion flips the
        # mission to `complete` (mission.completed event path) and, since it is the
        # first complete day, sets the streak to 1.
        last_complete = None
        for t in tasks:
            r = c.patch(
                f"/api/v1/missions/tasks/{t['id']}", headers=wh, json={"action": "complete"}
            )
            assert r.status_code == 200, r.text
            body = r.json()["data"]
            assert body["status"] == "done"
            assert body["completed_at"] is not None
            last_complete = r
        assert last_complete is not None
        capture("mission", "task_complete", last_complete)

        # 3b. Re-GET today -> mission is now `complete` with streak 1.
        today_after = c.get("/api/v1/missions/today", headers=wh)
        assert today_after.status_code == 200, today_after.text
        after_data = today_after.json()["data"]
        assert after_data["status"] == "complete"
        assert after_data["streak"] == 1
        assert all(t["status"] == "done" for t in after_data["tasks"])
        capture("mission", "today_complete", today_after)

        # 4. POST /missions/tasks -- add a custom, roadmap-less task. It appends to
        # today's mission with `roadmap_task_id: null`.
        custom = c.post(
            "/api/v1/missions/tasks",
            headers=wh,
            json={"title": "Call three design partners", "effort": "small"},
        )
        assert custom.status_code == 200, custom.text
        custom_data = custom.json()["data"]
        assert custom_data["roadmap_task_id"] is None
        assert custom_data["title"] == "Call three design partners"
        assert custom_data["status"] == "todo"
        capture("mission", "task_create", custom)

        # 5. GET /missions/history -- reflects the completed mission with
        # completed/total counts (the custom task lifts `total`) + the rolling
        # weekly completion %.
        history = c.get("/api/v1/missions/history", headers=wh)
        assert history.status_code == 200, history.text
        history_data = history.json()["data"]
        assert history_data["missions"], "expected at least today's mission in history"
        today_iso = date.today().isoformat()
        row = next(m for m in history_data["missions"] if m["mission_date"] == today_iso)
        assert row["status"] == "complete"
        assert row["completed"] == len(tasks)  # roadmap tasks done
        assert row["total"] == len(tasks) + 1  # + the still-todo custom task
        assert isinstance(history_data["weekly_completion_pct"], int)
        assert 0 <= history_data["weekly_completion_pct"] <= 100
        capture("mission", "history", history)
