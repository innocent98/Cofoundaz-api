"""Live Dashboard journey: a founder onboards to stage "validation" (which
auto-generates the roadmap inline, from `app/services/onboarding/complete.py`
calling `generate_roadmap` synchronously) so a mission has roadmap tasks to
draw from, completes the kickoff assessment (deliberately weak, unconditional-
only answers -- same fixture as `e2e/test_health_score.py` -- so the Health
Score section is a real "ok" score rather than the empty `pending_assessment`
state), then walks `GET /dashboard/summary`, completes one mission task, and
walks `GET /dashboard/activity` to see that completion as the newest item.

Every response body along the way is captured to `e2e/_captures/dashboard/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-dashboard.md` (Task 7). They must be REAL bodies
from this live run, complete and untrimmed.

Weekend guard: see `e2e/test_mission.py`'s module docstring. `GET
/dashboard/summary` is the first call here that lazily generates today's
mission (via `_mission_section` -> `get_or_generate_today`), so the same
`weekend_missions` guard must run BEFORE that call, not before `/missions/
today` directly.
"""

from datetime import date

import httpx

# Deliberately weak answers for every one of the 8 unconditional assessment
# questions (same fixture as e2e/test_health_score.py) -- none trigger a
# show_if-gated follow-up, so this is also the complete answer set, and it
# reliably produces a real (non-empty) Health Score for the summary section.
_LOW_ANSWERS = {
    "product_stage": "idea",
    "market_clarity": 3,
    "market_research": "none",
    "has_revenue": "no",
    "runway_confidence": 3,
    "incorporated": "no",
    "team_size": "solo",
    "team_confidence": 3,
}

_SUMMARY_KEYS = {
    "greeting",
    "health",
    "mission",
    "upcoming",
    "kpis",
    "calibration",
    "briefing",
    "risks",
    "opportunities",
}


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


def _complete_assessment(c: httpx.Client, wh: dict) -> dict:
    """Start + answer + complete the kickoff assessment with the low-answer
    fixture (same walk as e2e/test_health_score.py). Returns the /complete
    response body."""
    start = c.post("/api/v1/assessments", headers=wh)
    assert start.status_code == 201, start.text
    aid = start.json()["data"]["assessment_id"]

    answered_keys = []
    while True:
        nq = c.get(f"/api/v1/assessments/{aid}/next-question", headers=wh).json()["data"][
            "next_question"
        ]
        if nq is None:
            break
        assert nq["key"] in _LOW_ANSWERS, f"unexpected question {nq['key']!r}"
        val = _LOW_ANSWERS[nq["key"]]
        ans = c.post(
            f"/api/v1/assessments/{aid}/answers",
            headers=wh,
            json={"question_key": nq["key"], "value": val},
        )
        assert ans.status_code == 200, ans.text
        answered_keys.append(nq["key"])
    assert set(answered_keys) == set(_LOW_ANSWERS)

    done = c.post(f"/api/v1/assessments/{aid}/complete", headers=wh)
    assert done.status_code == 200, done.text
    return done.json()["data"]


def test_dashboard_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder to stage "validation" -> roadmap generates inline,
        # so the mission (drawn from the roadmap) and upcoming milestones exist.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        founder_id = me["user"]["id"]
        founder_full_name = me["profile"]["full_name"] if me["profile"] else None
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # Sanity: the roadmap really is there (mission + upcoming draw FROM it).
        tree = c.get("/api/v1/roadmap", headers=wh)
        assert tree.status_code == 200, tree.text
        assert tree.json()["data"]["phases"], "expected the validation-stage roadmap to seed phases"

        # 1. Complete the kickoff assessment -> calibration flips + Health Score
        # becomes a real "ok" score instead of the pending_assessment empty-state.
        _complete_assessment(c, wh)

        # Weekend guard (see module docstring): only on Sat/Sun, and only
        # *before* the first call that lazily generates today's mission
        # (GET /dashboard/summary below), so the roadmap-drawn assertions hold
        # every day of the week.
        if date.today().weekday() in (5, 6):
            wk = c.patch("/api/v1/missions/settings", headers=wh, json={"weekend_missions": True})
            assert wk.status_code == 200, wk.text

        # 2. GET /dashboard/summary -- all 9 top-level cards, health real (an
        # assessment is completed), mission drawn from the roadmap (<=3 tasks),
        # calibration flipped. The kickoff assessment is already complete by this
        # point, so this same call also opens the briefing gate
        # (app/services/dashboard/service.py:get_or_generate_briefing) and creates a
        # `generating` DailyBriefing row + enqueues ai.dashboard.briefing -- see
        # e2e/test_dashboard_ai_briefing.py for the worker-drain -> `ready` journey.
        summary = c.get("/api/v1/dashboard/summary", headers=wh)
        assert summary.status_code == 200, summary.text
        summary_data = summary.json()["data"]
        assert set(summary_data) == _SUMMARY_KEYS

        assert summary_data["health"]["status"] == "ok", summary_data["health"]
        assert isinstance(summary_data["health"]["score"], int)

        mission = summary_data["mission"]
        assert mission is not None
        mission_tasks = mission["tasks"]
        assert 1 <= len(mission_tasks) <= 3, f"expected 1-3 mission tasks, got {len(mission_tasks)}"

        assert isinstance(summary_data["upcoming"], list)

        assert summary_data["calibration"]["assessment_complete"] is True
        assert summary_data["briefing"]["status"] == "generating"
        assert summary_data["risks"]["status"] == "generating"
        assert summary_data["opportunities"]["status"] == "generating"
        capture("dashboard", "summary", summary)

        # 3. Complete one mission task.
        task_id = mission_tasks[0]["id"]
        task_patch = c.patch(
            f"/api/v1/missions/tasks/{task_id}", headers=wh, json={"action": "complete"}
        )
        assert task_patch.status_code == 200, task_patch.text
        assert task_patch.json()["data"]["status"] == "done"

        # 4. GET /dashboard/activity -- the newest item is the completion just
        # made, attributed to the founder (by id, and by name when a profile
        # full_name is set -- which it is, from onboarding step 1).
        activity = c.get("/api/v1/dashboard/activity", headers=wh)
        assert activity.status_code == 200, activity.text
        activity_items = activity.json()["data"]["items"]
        assert activity_items, "expected at least one activity item"
        newest = activity_items[0]
        assert newest["action"] == "mission.task.completed"
        assert newest["actor"] is not None
        assert newest["actor"]["id"] == founder_id
        if founder_full_name:
            assert newest["actor"]["name"] == founder_full_name
        capture("dashboard", "activity", activity)
