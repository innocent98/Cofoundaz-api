"""Live Module 03: ai.dashboard.briefing fills the dashboard's briefing/risks/opportunities.

Onboards a founder to stage "validation" (auto-generates the roadmap inline, same as
e2e/test_dashboard.py) and completes the kickoff assessment with the deliberately-weak-answer
fixture (so the briefing gate -- `latest_completed_result(db, startup.id) is not None`, see
`app/services/dashboard/service.py:get_or_generate_briefing`) is open. The first `GET
/dashboard/summary` then creates a `generating` `DailyBriefing` row and enqueues
`ai.dashboard.briefing` (app/services/dashboard/service.py:208); draining the worker in-process
(LLM_PROVIDER=stub, set by scripts/e2e_run.sh) runs `handle_dashboard_briefing`
(app/worker/handlers/ai.py:222), which fills `briefing`/`risks`/`opportunities` from the stub LLM
client and flips the row to `ready`. A second `GET /dashboard/summary` then reads the ready row.

Weekend guard: see e2e/test_dashboard.py's module docstring / e2e/test_mission.py. `GET
/dashboard/summary` is the first call here that lazily generates today's mission (via
`_mission_section` -> `get_or_generate_today`), so the same `weekend_missions` guard must run
BEFORE that call, not before `/missions/today` directly.

Both summary bodies are captured to `e2e/_captures/dashboard_ai_briefing/*.json` -- the verbatim
source for Task 6's FE integration guide addendum on the briefing/risks/opportunities sections.
"""

from datetime import date

import httpx

# Deliberately weak answers for every one of the 8 unconditional assessment questions (same
# fixture as e2e/test_dashboard.py / e2e/test_health_score.py) -- none trigger a show_if-gated
# follow-up, so this is also the complete answer set, and it reliably produces a completed
# assessment result (the briefing gate only checks for a completed result, not its score).
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
    """Start + answer + complete the kickoff assessment with the low-answer fixture (same walk
    as e2e/test_dashboard.py / e2e/test_health_score.py). Returns the /complete response body."""
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


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.dashboard.briefing)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_dashboard_ai_briefing(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder to stage "validation" -> roadmap generates inline. Not required
        # by the briefing gate itself, but keeps this journey aligned with e2e/test_dashboard.py
        # so /dashboard/summary's other sections (mission, upcoming) are non-empty too.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Complete the kickoff assessment -> opens the briefing gate
        # (`latest_completed_result(db, startup.id) is not None`).
        _complete_assessment(c, wh)

        # Weekend guard (see module docstring): only on Sat/Sun, and only *before* the first
        # call that lazily generates today's mission (GET /dashboard/summary below).
        if date.today().weekday() in (5, 6):
            wk = c.patch("/api/v1/missions/settings", headers=wh, json={"weekend_missions": True})
            assert wk.status_code == 200, wk.text

        # 2. First GET /dashboard/summary creates a `generating` DailyBriefing row and enqueues
        # ai.dashboard.briefing (app/services/dashboard/service.py:get_or_generate_briefing).
        first = c.get("/api/v1/dashboard/summary", headers=wh)
        assert first.status_code == 200, first.text
        first_data = first.json()["data"]
        assert first_data["briefing"]["status"] == "generating"
        assert first_data["risks"]["status"] == "generating"
        assert first_data["opportunities"]["status"] == "generating"
        capture("dashboard_ai_briefing", "summary_generating", first)

        # 3. Drain the worker in-process (LLM_PROVIDER=stub) -> handle_dashboard_briefing runs,
        # fills briefing/risks/opportunities from the stub LLM client, flips status to ready.
        _drain()

        # 4. Second GET /dashboard/summary reads the now-ready row.
        after = c.get("/api/v1/dashboard/summary", headers=wh)
        assert after.status_code == 200, after.text
        after_data = after.json()["data"]
        briefing = after_data["briefing"]
        assert briefing["status"] == "ready"
        assert "[stub-llm]" in briefing["message"]
        assert after_data["risks"]["status"] == "ready"
        assert "[stub-llm]" in after_data["risks"]["message"]
        assert after_data["opportunities"]["status"] == "ready"
        assert "[stub-llm]" in after_data["opportunities"]["message"]
        capture("dashboard_ai_briefing", "summary_ready", after)
