"""Live Module 03: ai.onboarding.panel AI-authors the onboarding calibration panel.

Walk onboarding through the goals step (industry + stage + goals set) -> GET
/onboarding/state shows a templated `ai_panel` + a job is enqueued -> drain the worker
(LLM_PROVIDER=stub, set by scripts/e2e_run.sh) -> GET /onboarding/state shows the
`[stub-llm]` panel.

Wizard walk (step patch bodies) mirrors e2e/test_onboarding.py exactly: step 1 full_name,
step 2 name, step 3 industry/business_model/stage, step 4 goals -- the step-4 PATCH is what
flips industry+stage+goals all-set and triggers `_maybe_generate_ai_panel` (see
app/services/onboarding/steps.py), which writes a templated `ai_panel` instantly and enqueues
`ai.onboarding.panel`. `_drain()` copied verbatim from e2e/test_records_ai_fill.py.
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.onboarding.panel)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_onboarding_ai_panel(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _auth_header(access)

        c.patch("/api/v1/onboarding/state", headers=h, json={"step": 1, "full_name": "Ada Founder"})
        c.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=h,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
        )
        c.patch(
            "/api/v1/onboarding/state",
            headers=h,
            json={"step": 4, "goals": ["Get first customers"]},
        )

        state1 = c.get("/api/v1/onboarding/state", headers=h)
        assert state1.status_code == 200, state1.text
        panel1 = state1.json()["data"]["ai_panel"]
        assert panel1  # templated (non-stub) present after step 4
        assert "[stub-llm]" not in panel1
        capture("onboarding_ai_panel", "state_templated", state1)

        _drain()

        state2 = c.get("/api/v1/onboarding/state", headers=h)
        assert state2.status_code == 200, state2.text
        assert "[stub-llm]" in state2.json()["data"]["ai_panel"]
        capture("onboarding_ai_panel", "state_after_drain", state2)
