"""Live Module 03: ai.health.recommendations personalizes recommendation bodies (stub).

Onboard + complete the kickoff assessment with deliberately weak answers (same
`_LOW_ANSWERS` set as e2e/test_health_score.py / tests/services/test_health_recompute.py's
`_MINIMAL_ANSWERS`) so every dimension lands below 60 and recommendations are generated.

IMPORTANT timing wrinkle, found by reproducing this live against the running app (not
assumed from reading the code): `complete_assessment` (app/services/assessment/service.py)
calls `recompute_health_score(db, startup, trigger="assessment_complete")` in the SAME
request, but *before* the just-`db.add()`-ed `AssessmentResult` row is flushed -- the app's
`SessionLocal` is `autoflush=False` (app/db/session.py), and nothing flushes between the
`db.add(AssessmentResult(...))` and the recompute call. `recompute_health_score` starts with
`latest_completed_result(db, startup.id)`, a `db.execute(select(...))` that does NOT see the
unflushed row, so it returns `None` and `recompute_health_score` bails out immediately --
creating no `HealthScore`, no `HealthRecommendation` rows, and enqueuing no
`ai.health.recommendations` job. Confirmed empirically: POSTing .../complete and immediately
GETting .../health-score/recommendations returns an empty list every time.

`get_overview` (same service module) has a *lazy-on-read* fallback for exactly this gap: if
no `HealthScore` row exists yet, it calls `recompute_health_score(db, startup,
trigger="lazy_read")` itself -- and by the time THIS GET runs (a fresh request, its own DB
session), the completing request has already committed, so `latest_completed_result` now sees
the `AssessmentResult` and recompute succeeds: it creates the pending `HealthRecommendation`
rows from the catalog AND enqueues `ai.health.recommendations`. This is exactly why
e2e/test_health_score.py's journey (which passes) calls `GET /api/v1/health-score` before it
ever looks at recommendations -- this test reproduces that same call for the same reason.

Draining the worker in-process (LLM_PROVIDER=stub, set by scripts/e2e_run.sh) runs the
enqueued job. `handle_health_recommendations` (app/worker/handlers/ai.py) only rewrites rows
whose body is still the catalog default, and StubLLMClient.complete_json (app/platform/llm.py)
returns exactly one array item -- {"key": <first pending key>, "body": "[stub-llm] body"} --
so after the drain exactly one pending recommendation's body carries the `[stub-llm]` marker.

GET /api/v1/health-score/recommendations returns `data` as a flat LIST of recommendations
(confirmed against e2e/test_health_score.py step 6 and app/services/health_score/service.py's
`_serialize_rec`, which puts `id`, `dimension`, `key`, `title`, `body`, `estimated_lift`,
`effort`, `status`, `priority` all at the top level of each entry) -- not a
`data.recommendations` envelope.
"""

import httpx

# Deliberately weak answers for every one of the 8 unconditional questions -- none trigger a
# show_if-gated follow-up, so this is also the complete answer set. Identical to
# e2e/test_health_score.py's `_LOW_ANSWERS`.
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


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.health.recommendations)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _complete_assessment_and_workspace(c: httpx.Client, auth: dict) -> dict:
    """Walk onboarding (stage 'idea'), complete it, then take + complete the kickoff
    assessment with weak answers so recommendations are generated. Mirrors
    e2e/test_health_score.py's `_onboard` + assessment walk. Returns the
    X-Workspace-Id-scoped headers read off /auth/me."""
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
    dim_scores = done.json()["data"]["dimension_scores"]
    assert any(v < 60 for v in dim_scores.values()), dim_scores

    return wh


def test_health_recommendations_ai(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _complete_assessment_and_workspace(c, auth)  # weak scores -> recommendations exist

        # Trigger the lazy-on-read recompute (see module docstring): the assessment-complete
        # request's own recompute call is a same-transaction no-op, so nothing is generated or
        # enqueued until a fresh request -- this one -- sees the now-committed AssessmentResult.
        overview = c.get("/api/v1/health-score", headers=wh)
        assert overview.status_code == 200, overview.text
        ov = overview.json()["data"]
        assert ov["status"] == "ok", ov
        assert ov["top_recommendations"], "expected recommendations for a weak assessment"
        capture("health_recommendations_ai", "overview_after_complete", overview)

        _drain()  # runs ai.health.recommendations, enqueued by the lazy recompute above

        got = c.get("/api/v1/health-score/recommendations", headers=wh)
        assert got.status_code == 200, got.text
        recs = got.json()["data"]  # flat list -- see module docstring
        assert recs, "expected recommendations for a weak assessment"
        bodies = [r["body"] for r in recs]
        assert bodies and any("[stub-llm]" in b for b in bodies)
        capture("health_recommendations_ai", "recommendations_after_drain", got)
