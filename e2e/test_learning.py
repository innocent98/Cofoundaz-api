"""Live Learning Academy journey (Module 17): a founder onboards at the validation stage, reads
the recommendations shelf, browses the catalog and one course, enrols (201, then 200 on a
repeat), completes both lessons of a two-lesson course (progress 50, then 100 with a
certificate), confirms the finished course has left both the shelf and continue watching, and
lists paths, articles and certificates.

Every response body along the way is captured to `e2e/_captures/learning/*.json` -- those files
are the verbatim source for `docs/fe-integration-guide-learning.md`. They must be REAL bodies from
this live run, complete and untrimmed.

The Learning Academy has no roadmap or assessment dependency, so onboarding here is steps 1-4 +
complete -- the same shape as e2e/test_documents.py and e2e/test_journal.py.

Module 03 deferred AI upgrade: the shelf-level `recommendation_reason` starts templated
(`get_or_create_recommendation_reason`, app/services/learning/service.py) and enqueues
`ai.learning.recommendations`; draining the worker in-process (LLM_PROVIDER=stub, same pattern
as e2e/test_dashboard_ai_briefing.py) runs `handle_learning_recommendations`
(app/worker/handlers/ai.py:351), which personalizes the reason from the stub LLM client and flips
its status to ready. At the "validation" stage used throughout this journey,
`recommended_courses` always yields titles (COURSE is one of them), so the drain reliably takes
the LLM path rather than the no-signal fallback.
"""

import httpx

COURSE = "build-scope-the-mvp"


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.learning.recommendations)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_documents.py)."""
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


def test_learning_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder at the validation stage.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Front page: a stage-matched shelf, nothing to continue yet. The shelf-level
        # `recommendation_reason` (Module 03 deferred AI upgrade) is present from the very
        # first read -- templated until the worker drains.
        recs = c.get("/api/v1/learning/recommendations", headers=wh)
        assert recs.status_code == 200, recs.text
        assert COURSE in [x["id"] for x in recs.json()["data"]["recommended"]]
        assert recs.json()["data"]["continue_watching"] == []
        assert "recommendation_reason" in recs.json()["data"]
        capture("learning", "recommendations_before", recs)

        # 1b. Drain the worker in-process (LLM_PROVIDER=stub) -> ai.learning.recommendations
        # personalizes the shelf reason (app/worker/handlers/ai.py:handle_learning_recommendations)
        # and flips its status to ready. Re-fetch to capture the AI-upgraded reason.
        _drain()
        reason_recs = c.get("/api/v1/learning/recommendations", headers=wh)
        assert reason_recs.status_code == 200, reason_recs.text
        assert "[stub-llm]" in reason_recs.json()["data"]["recommendation_reason"]
        capture("learning", "recommendations_reason", reason_recs)

        # 2. The catalog grid and one course.
        courses = c.get("/api/v1/learning/courses", headers=wh)
        assert courses.status_code == 200, courses.text
        capture("learning", "courses", courses)
        detail = c.get(f"/api/v1/learning/courses/{COURSE}", headers=wh)
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["data"]["lessons"]) == 2
        capture("learning", "course_detail", detail)

        # 3. Enrol: 201 the first time, 200 on a repeat.
        enrol = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": COURSE})
        assert enrol.status_code == 201, enrol.text
        capture("learning", "enrollment_created", enrol)
        again = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": COURSE})
        assert again.status_code == 200, again.text
        capture("learning", "enrollment_repeat", again)

        # 4. Continue watching now shows the course.
        watching = c.get("/api/v1/learning/recommendations", headers=wh)
        assert [x["id"] for x in watching.json()["data"]["continue_watching"]] == [COURSE]
        capture("learning", "recommendations_in_progress", watching)

        # 5. Complete lesson 1 -> 50%, no certificate.
        first = c.patch(
            f"/api/v1/learning/lessons/{COURSE}-1/progress", headers=wh, json={"completed": True}
        )
        assert first.status_code == 200, first.text
        assert first.json()["data"]["progress"] == 50
        assert first.json()["data"]["certificate"] is None
        capture("learning", "lesson_completed", first)

        # 6. Complete lesson 2 -> 100% and a certificate.
        last = c.patch(
            f"/api/v1/learning/lessons/{COURSE}-2/progress", headers=wh, json={"completed": True}
        )
        assert last.status_code == 200, last.text
        finished = last.json()["data"]
        assert finished["progress"] == 100 and finished["certificate"] is not None
        capture("learning", "course_completed", last)

        # 7. The finished course leaves the shelf and continue watching.
        after = c.get("/api/v1/learning/recommendations", headers=wh).json()["data"]
        assert COURSE not in [x["id"] for x in after["recommended"]]
        assert after["continue_watching"] == []

        # 8. Paths (the formula live), articles and certificates.
        paths = c.get("/api/v1/learning/paths", headers=wh)
        assert paths.status_code == 200, paths.text
        by_id = {p["id"]: p for p in paths.json()["data"]["paths"]}
        assert by_id["path-validation-foundations"]["progress"] == 33  # (0 + 0 + 100) / 3
        capture("learning", "paths", paths)

        articles = c.get("/api/v1/learning/articles", headers=wh)
        assert articles.status_code == 200, articles.text
        capture("learning", "articles", articles)

        certs = c.get("/api/v1/learning/certificates", headers=wh)
        assert certs.status_code == 200, certs.text
        listed = certs.json()["data"]["certificates"]
        assert [x["id"] for x in listed] == [finished["certificate"]["id"]]
        capture("learning", "certificates", certs)
