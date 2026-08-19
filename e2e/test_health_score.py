"""Live Health Score journey: a founder onboards, sees the honest
`pending_assessment` empty-state, completes the kickoff assessment with
deliberately weak answers (so every dimension lands below 60 and
recommendations are generated), then walks the full read + accept/dismiss
surface -- including the 409 conflict on a resolved recommendation and the
uniform-404 cross-tenant enumeration guard.

Every response body along the way is captured to
`e2e/_captures/health_score/*.json` -- those files are the verbatim source
for `docs/fe-integration-guide-health-score.md`.
"""

import httpx

# Deliberately weak answers for every one of the 8 unconditional questions.
# None of these trigger a show_if-gated follow-up (product_stage != mvp/live,
# has_revenue == no, incorporated == no), so this is also the complete answer
# set for the assessment -- same fixture used by the module's unit tests
# (tests/api/test_health_recommendations.py, tests/services/test_health_recompute.py)
# because it reliably tanks every dimension below 60 and generates
# recommendations across the board.
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


def _onboard(c: httpx.Client, auth: dict) -> None:
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


def _wh(c: httpx.Client, auth: dict) -> dict:
    """Onboard just enough to have an active workspace, then build the
    X-Workspace-Id header the way `/auth/me` reports it."""
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    wsid = me["active_workspace_id"]
    return {**auth, "X-Workspace-Id": wsid}


def test_health_score_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {access}"}

        _onboard(c, auth)
        wh = _wh(c, auth)

        # 1. Before the kickoff assessment is completed: honest pending empty-state.
        pending = c.get("/api/v1/health-score", headers=wh)
        assert pending.status_code == 200, pending.text
        pending_data = pending.json()["data"]
        assert pending_data["status"] == "pending_assessment"
        assert pending_data["score"] is None
        capture("health_score", "overview_pending", pending)

        # 2. Take and complete the kickoff assessment with deliberately weak answers.
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

        # 3. Overview flips to "ok" -- a real score, 5 dimensions, top recommendations.
        overview = c.get("/api/v1/health-score", headers=wh)
        assert overview.status_code == 200, overview.text
        ov = overview.json()["data"]
        assert ov["status"] == "ok"
        assert isinstance(ov["score"], int)
        assert len(ov["dimensions"]) == 5
        assert ov["top_recommendations"]
        capture("health_score", "overview_ok", overview)

        # 4. Dimension drill-down.
        dim = c.get("/api/v1/health-score/dimensions/money", headers=wh)
        assert dim.status_code == 200, dim.text
        dim_data = dim.json()["data"]
        assert dim_data["label"] == "Financial"
        assert dim_data["signals"]
        capture("health_score", "dimension_money", dim)

        # 5. History -- at least the one point just recomputed.
        history = c.get("/api/v1/health-score/history", params={"range": "all"}, headers=wh)
        assert history.status_code == 200, history.text
        history_data = history.json()["data"]
        assert len(history_data) >= 1
        capture("health_score", "history", history)

        # 6. Recommendations were actually generated (weak answers => non-empty),
        # and every freshly generated recommendation starts pending.
        recs = c.get("/api/v1/health-score/recommendations", headers=wh)
        assert recs.status_code == 200, recs.text
        recs_data = recs.json()["data"]
        assert recs_data, "expected recommendations for a weak assessment"
        assert all(r["status"] == "pending" for r in recs_data)
        capture("health_score", "recommendations", recs)

        # 7. Accept one, then the same one 409s on dismiss (already resolved).
        rec_id = recs_data[0]["id"]
        accept = c.post(f"/api/v1/health-score/recommendations/{rec_id}/accept", headers=wh)
        assert accept.status_code == 200, accept.text
        assert accept.json()["data"]["status"] == "accepted"
        capture("health_score", "accept", accept)

        conflict = c.post(f"/api/v1/health-score/recommendations/{rec_id}/dismiss", headers=wh)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "RECOMMENDATION_RESOLVED"
        capture("health_score", "conflict", conflict)

        # 8. Benchmarks -- cohort too small in a fresh e2e DB, honest empty-state.
        benchmarks = c.get("/api/v1/health-score/benchmarks", headers=wh)
        assert benchmarks.status_code == 200, benchmarks.text
        assert benchmarks.json()["data"]["status"] == "insufficient_data"
        capture("health_score", "benchmarks", benchmarks)

        # 9. A second founder, in their own workspace, cannot enumerate the first
        # founder's recommendation -- uniform 404, not a leak of "exists but not
        # yours".
        u2 = make_verified_user(c)
        access2 = c.post("/api/v1/auth/login", json=u2).json()["data"]["access_token"]
        auth2 = {"Authorization": f"Bearer {access2}"}
        c.get("/api/v1/onboarding/state", headers=auth2)  # lazy-creates their workspace
        wh2 = _wh(c, auth2)

        cross = c.post(f"/api/v1/health-score/recommendations/{rec_id}/accept", headers=wh2)
        assert cross.status_code == 404, cross.text
        assert cross.json()["error"]["code"] == "NOT_FOUND"
        capture("health_score", "cross_tenant_404", cross)
