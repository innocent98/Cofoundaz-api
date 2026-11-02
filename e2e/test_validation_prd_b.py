"""Live Validation Hub PRD slice (b) journey: "Generate script" for chosen assumptions (an AI job
that SAVES an editable script), listing and editing it, then the Insight Synthesizer over
interviews and open survey answers (an AI job that stores a report), reading the report,
applying one suggested assumption status change (and the 409 on a second click), the
"New pattern detected" notification, and deletes.

Every body is captured to `e2e/_captures/validation_prd_b/*.json`, the verbatim source for
`docs/fe-integration-guide-validation-prd-b.md`. AI jobs drain in-process with the stub LLM, so
the AI-written text in these captures reads "[stub-llm] ..."; the guide shows real-model
output separately (captured against the local stack with the real model).
"""

import httpx

G = "validation_prd_b"
V = "/api/v1/validation"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Validate demand"]}
    )
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def _drain_ai() -> None:
    """Run queued AI jobs in-process with the stub LLM, with notification subscribers wired so
    `validation.insight.detected` lands in the feed exactly as it does in the worker."""
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import validation as _v  # noqa: F401  (registers the jobs)

    register_notifications()
    db = SessionLocal()
    try:
        for _ in range(100):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_validation_prd_b_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {access}"}
        _onboard(c, auth)
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        pay = c.post(
            f"{V}/assumptions",
            headers=wh,
            json={"statement": "Traders will pay 0.5% for same-day settlement", "risk": "high"},
        ).json()["data"]
        # Already being tested, so the stub model's "untested" suggestion is a real change.
        c.patch(f"{V}/assumptions/{pay['id']}", headers=wh, json={"status": "testing"})

        # 1. Generate script: 422 without an assumption, then a job that saves a script.
        none = c.post(f"{V}/scripts/generate", headers=wh, json={"assumption_ids": []})
        assert none.status_code == 422, none.text
        capture(G, "script_generate_no_assumption_422", none)
        q = c.post(f"{V}/scripts/generate", headers=wh, json={"assumption_ids": [pay["id"]]})
        assert q.status_code == 202, q.text
        capture(G, "script_generate_queued", q)
        _drain_ai()
        job = c.get(f"/api/v1/jobs/{q.json()['data']['job_id']}", headers=wh)
        assert job.json()["data"]["result"]["status"] == "ready", job.text
        capture(G, "script_generate_job", job)
        script_id = job.json()["data"]["result"]["script_id"]

        listed = c.get(f"{V}/scripts", headers=wh)
        assert [s["id"] for s in listed.json()["data"]["scripts"]] == [script_id]
        capture(G, "scripts_list", listed)
        script = c.get(f"{V}/scripts/{script_id}", headers=wh).json()["data"]
        first = script["questions"][0]
        edited = c.patch(
            f"{V}/scripts/{script_id}",
            headers=wh,
            json={
                "title": "Payout pain interviews",
                "questions": [
                    {**first, "question": "Tell me about the last time a payout was late."},
                    {"question": "What did you do while you waited?", "tip": "Stay quiet."},
                ],
            },
        )
        assert edited.status_code == 200, edited.text
        capture(G, "script_edited", edited)
        bad = c.patch(f"{V}/scripts/{script_id}", headers=wh, json={"questions": []})
        assert bad.status_code == 422
        capture(G, "script_edit_empty_422", bad)

        # 2. Insight Synthesizer: 422 with nothing to read, then interviews + an open answer.
        empty = c.post(f"{V}/synthesize", headers=wh, json={"source": "all"})
        assert empty.status_code == 422, empty.text
        capture(G, "synthesize_nothing_422", empty)
        c.post(
            f"{V}/interviews",
            headers=wh,
            json={
                "interviewee": "Placeholder Name",
                "segment": "Market traders",
                "held_on": "2026-10-15",
                "verdict": "supports",
                "notes": "Keeps cash at home because transfers take days.",
                "key_quotes": ["I wait three days for my money"],
                "assumption_ids": [pay["id"]],
            },
        )
        survey = c.post(
            f"{V}/surveys",
            headers=wh,
            json={
                "title": "Payouts",
                "questions": [{"type": "open", "prompt": "What frustrates you about payouts?"}],
            },
        ).json()["data"]
        opened = c.patch(f"{V}/surveys/{survey['id']}", headers=wh, json={"status": "open"})
        token = opened.json()["data"]["public_token"]
        qid = opened.json()["data"]["survey"]["questions"][0]["id"]
        c.post(f"{V}/surveys/{token}/responses", json={"answers": {qid: "Payouts take forever"}})

        sq = c.post(f"{V}/synthesize", headers=wh, json={"source": "all"})
        assert sq.status_code == 202, sq.text
        capture(G, "synthesize_queued", sq)
        _drain_ai()
        sjob = c.get(f"/api/v1/jobs/{sq.json()['data']['job_id']}", headers=wh)
        assert sjob.json()["data"]["result"]["status"] == "ready", sjob.text
        capture(G, "synthesize_job", sjob)
        insight_id = sjob.json()["data"]["result"]["insight_id"]

        report = c.get(f"{V}/insights/{insight_id}", headers=wh)
        assert report.status_code == 200, report.text
        data = report.json()["data"]
        assert data["inputs"] == {"interviews": 1, "open_answers": 1, "feedback": 0}
        assert "Placeholder Name" not in report.text
        capture(G, "insight_report", report)
        capture(G, "insights_list", c.get(f"{V}/insights", headers=wh))

        # 3. Apply a suggestion: the assumption moves; a second click is 409.
        sug = data["suggestions"][0]
        assert sug["assumption_id"] == pay["id"]
        url = f"{V}/insights/{insight_id}/suggestions/{sug['id']}/apply"
        applied = c.post(url, headers=wh)
        assert applied.status_code == 200, applied.text
        assert applied.json()["data"]["assumption"]["status"] == sug["to_status"]
        capture(G, "suggestion_applied", applied)
        again = c.post(url, headers=wh)
        assert again.status_code == 409, again.text
        capture(G, "suggestion_applied_again_409", again)

        # 4. The PRD notification reached the founder.
        feed = c.get("/api/v1/notifications", headers=wh)
        hits = [
            n
            for n in feed.json()["data"]["notifications"]
            if n["type"] == "validation.insight.detected"
        ]
        assert hits and hits[0]["title"].startswith("New pattern detected in customer feedback: ")
        assert hits[0]["body"] == "See insight"
        capture(G, "founder_notifications", feed)

        # 5. Deletes.
        capture(G, "insight_deleted", c.delete(f"{V}/insights/{insight_id}", headers=wh))
        capture(G, "insight_gone_404", c.get(f"{V}/insights/{insight_id}", headers=wh))
        capture(G, "script_deleted", c.delete(f"{V}/scripts/{script_id}", headers=wh))
