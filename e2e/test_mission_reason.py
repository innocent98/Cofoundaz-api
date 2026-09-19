"""Live Module 03: ai.mission.reason rewrites mission task reasons.

Onboard + complete the kickoff assessment so a roadmap exists -- `generate_roadmap` runs
SYNCHRONOUSLY from `app/services/onboarding/complete.py::complete_onboarding` (not a job), so
the roadmap (and its tasks) already exist the moment onboarding completes; the assessment walk
here only exists to flip `assessment_pending` off the same way a real founder would, mirroring
e2e/test_assessment.py / e2e/test_health_score.py.

GET /api/v1/missions/today lazily generates the mission from the roadmap with templated
reasons ("From your '<milestone>' milestone.") and enqueues `ai.mission.reason`
(app/services/mission/service.py::_enqueue_mission_reason, only when the mission is non-empty).
Draining the worker in-process (LLM_PROVIDER=stub, set by scripts/e2e_run.sh) runs that job;
StubLLMClient.complete_json returns exactly one array item (order=0, reason="[stub-llm]
reason") per app/platform/llm.py, so after the drain the order-0 task's reason is rewritten --
a second GET must show it.

Weekend guard (mirrors e2e/test_mission.py): `get_or_generate_today` materializes an EMPTY
mission on Sat/Sun when `weekend_missions` is off (product's "weekends off" behaviour), and an
empty mission enqueues no `ai.mission.reason` job at all. Generation is lazy-on-read and cached
for the day, so this test flips `weekend_missions` on BEFORE the first `/today` call, but only
when the live server clock says it's actually a weekend -- so the test holds any day of the
week without distorting the settings defaults on weekdays.
"""

from datetime import date

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.mission.reason)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


# Deliberately weak-but-complete answer set -- skips every conditional follow-up (same set as
# e2e/test_health_score.py's `_LOW_ANSWERS` / tests/services/test_health_recompute.py's
# `_MINIMAL_ANSWERS`). The mission itself is drawn from the roadmap, which is generated
# synchronously on onboarding-complete regardless of assessment scores -- this set is used here
# only to get a *complete* assessment quickly, not because the scores matter to this test.
_ANSWERS = {
    "product_stage": "idea",
    "market_clarity": 3,
    "market_research": "none",
    "has_revenue": "no",
    "runway_confidence": 3,
    "incorporated": "no",
    "team_size": "solo",
    "team_confidence": 3,
}


def _complete_assessment_and_workspace(c: httpx.Client, auth: dict) -> dict:
    """Walk onboarding (stage 'idea', same shape as e2e/test_records_ai_fill.py::_onboard),
    complete it, then take + complete the kickoff assessment (same walk as
    e2e/test_assessment.py / e2e/test_health_score.py). Returns the X-Workspace-Id-scoped
    headers read off /auth/me."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )
    completed = c.post("/api/v1/onboarding/complete", headers=auth)
    assert completed.status_code == 200, completed.text

    me = c.get("/api/v1/auth/me", headers=auth)
    assert me.status_code == 200, me.text
    wsid = me.json()["data"]["active_workspace_id"]
    wh = {**auth, "X-Workspace-Id": wsid}

    start = c.post("/api/v1/assessments", headers=wh)
    assert start.status_code == 201, start.text
    aid = start.json()["data"]["assessment_id"]

    while True:
        nq = c.get(f"/api/v1/assessments/{aid}/next-question", headers=wh).json()["data"][
            "next_question"
        ]
        if nq is None:
            break
        assert nq["key"] in _ANSWERS, f"unexpected question {nq['key']!r}"
        val = _ANSWERS[nq["key"]]
        ans = c.post(
            f"/api/v1/assessments/{aid}/answers",
            headers=wh,
            json={"question_key": nq["key"], "value": val},
        )
        assert ans.status_code == 200, ans.text

    done = c.post(f"/api/v1/assessments/{aid}/complete", headers=wh)
    assert done.status_code == 200, done.text

    return wh


def test_mission_reason_ai(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _complete_assessment_and_workspace(c, auth)

        # Weekend guard (see module docstring): only on Sat/Sun, and only *before* today's
        # mission is generated, so a non-empty mission -- and its ai.mission.reason job --
        # actually exists to drain.
        if date.today().weekday() in (5, 6):
            wk = c.patch("/api/v1/missions/settings", headers=wh, json={"weekend_missions": True})
            assert wk.status_code == 200, wk.text

        # GET /missions/today lazily generates today's mission from the roadmap (templated
        # reasons) and enqueues ai.mission.reason.
        first = c.get("/api/v1/missions/today", headers=wh)
        assert first.status_code == 200, first.text
        first_tasks = first.json()["data"]["tasks"]
        assert first_tasks, "expected a non-empty mission (roadmap has tasks)"
        assert all(t["reason"] and t["reason"].startswith("From your '") for t in first_tasks)
        capture("mission_reason", "today_before_drain", first)

        _drain()

        after = c.get("/api/v1/missions/today", headers=wh)
        assert after.status_code == 200, after.text
        tasks = after.json()["data"]["tasks"]
        assert tasks, "expected a non-empty mission (roadmap has tasks)"
        assert any(t["reason"] and "[stub-llm]" in t["reason"] for t in tasks)
        capture("mission_reason", "today_after_drain", after)
