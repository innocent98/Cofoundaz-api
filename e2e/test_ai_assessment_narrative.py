"""Live Module 03 Slice 1: the LLM seam rewrites the assessment narrative.

Drives a real assessment to completion via the API (mirrors e2e/test_assessment.py's
onboard -> start -> next-question/answers loop -> complete walk), captures the
completion response's TEMPLATED narrative, then drains the worker in-process (mirrors
e2e/test_notifications_email.py::_drain). With LLM_PROVIDER=stub (set by
scripts/e2e_run.sh for both the server process and this pytest process) the
`ai.assessment.narrative` job deterministically rewrites the narrative to the stub's
fixed "[stub-llm] ..." text, proving enqueue -> job -> LLM -> persist end to end with
no network call and no API key.
"""

import httpx

# NOTE: app-internal imports (SessionLocal, worker) are done LAZILY inside `_drain`,
# not at module level -- see e2e/test_notifications_email.py's module-header note for
# why (keeps Settings instantiation out of collection for the E2E_REMOTE gate).


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.assessment.narrative)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap, same as test_notifications_email.py::_drain
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_assessment_narrative_is_ai_rewritten(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)

        # Onboard just enough to complete (mirrors e2e/test_assessment.py).
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
        c.post("/api/v1/onboarding/complete", headers=auth)

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wsid = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": wsid}

        start = c.post("/api/v1/assessments", headers=wh)
        assert start.status_code == 201, start.text
        aid = start.json()["data"]["assessment_id"]

        vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
        while True:
            nq = c.get(f"/api/v1/assessments/{aid}/next-question", headers=wh).json()["data"][
                "next_question"
            ]
            if nq is None:
                break
            if nq["qtype"] == "single_choice":
                val = nq["options"][-1]["value"]
            elif nq["qtype"] == "multi_choice":
                val = [nq["options"][-1]["value"]]
            else:
                val = vals[nq["qtype"]]
            ans = c.post(
                f"/api/v1/assessments/{aid}/answers",
                headers=wh,
                json={"question_key": nq["key"], "value": val},
            )
            assert ans.status_code == 200, ans.text

        # Complete -- the stored narrative at THIS point is the templated fallback
        # (app/services/assessment/scoring.py::score). `POST .../complete` returns it
        # FLAT under `data.narrative` (see app/services/assessment/service.py::_result_dict)
        # -- a different nesting than the GET below returns it (data.result.narrative).
        complete_resp = c.post(f"/api/v1/assessments/{aid}/complete", headers=wh)
        assert complete_resp.status_code == 200, complete_resp.text
        templated = complete_resp.json()["data"]["narrative"]
        assert templated and not templated.startswith("[stub-llm]")
        capture("ai_assessment_narrative", "complete_templated", complete_resp)

        # Drain: the ai.assessment.narrative job enqueued by complete_assessment runs
        # and overwrites AssessmentResult.narrative with the LLM (stub) output.
        _drain()

        got = c.get(f"/api/v1/assessments/{aid}", headers=wh)
        assert got.status_code == 200, got.text
        # Confirmed live: GET /assessments/{id} nests the result under `data.result`,
        # NOT flat like the complete response above -- `data.result.narrative`.
        narrative = got.json()["data"]["result"]["narrative"]
        assert narrative.startswith("[stub-llm]"), narrative
        capture("ai_assessment_narrative", "result_ai_narrative", got)
