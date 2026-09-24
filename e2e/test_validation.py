"""Live Validation Hub journey (Module 09): a founder onboards, records an assumption, builds and
opens a survey, **a member of the public answers it with no authentication at all**, the founder
reads the analytics, links an experiment and an interview, and marks the assumption validated.

Every response body along the way is captured to `e2e/_captures/validation/*.json` -- those files
are the verbatim source for `docs/fe-integration-guide-validation.md`. They must be REAL bodies
from this live run, complete and untrimmed.

The public submission is deliberately made with a SEPARATE client carrying no headers, so the
capture proves the endpoint needs no session and returns nothing about the workspace.
"""

import httpx

QUESTIONS = [
    {
        "type": "choice",
        "prompt": "Would you pay for this?",
        "options": ["Yes", "No"],
        "required": True,
    },
    {"type": "nps", "prompt": "How likely are you to recommend it?"},
    {"type": "open", "prompt": "What would make it a must-have?"},
]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_learning.py)."""
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


def test_validation_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        workspace_id = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": workspace_id}

        # 1. Record the assumption the whole module exists to test.
        created = c.post(
            "/api/v1/validation/assumptions",
            headers=wh,
            json={"statement": "Founders will pay for validation tooling", "risk": "high"},
        )
        assert created.status_code == 201, created.text
        assumption = created.json()["data"]
        assert assumption["status"] == "untested" and assumption["evidence_count"] == 0
        capture("validation", "assumption_created", created)

        # 2. Build a survey and open it, which mints the public token exactly once.
        survey_created = c.post(
            "/api/v1/validation/surveys",
            headers=wh,
            json={"title": "Pricing check", "questions": QUESTIONS},
        )
        assert survey_created.status_code == 201, survey_created.text
        survey_id = survey_created.json()["data"]["id"]
        capture("validation", "survey_created", survey_created)

        opened = c.patch(
            f"/api/v1/validation/surveys/{survey_id}", headers=wh, json={"status": "open"}
        )
        assert opened.status_code == 200, opened.text
        token = opened.json()["data"]["public_token"]
        assert token, "opening a survey must return the raw token once"
        capture("validation", "survey_opened", opened)

    # 3. A member of the public answers it — a brand-new client with NO headers at all.
    with httpx.Client(base_url=base_url, timeout=10.0) as public:
        form = public.get(f"/api/v1/validation/surveys/{token}")
        assert form.status_code == 200, form.text
        body = form.json()["data"]
        assert set(body) == {"title", "questions"}
        assert survey_id not in form.text, "the public form must not leak the survey id"
        assert workspace_id not in form.text, "the public form must not leak the workspace"
        capture("validation", "public_form", form)

        answers = {
            body["questions"][0]["id"]: "Yes",
            body["questions"][1]["id"]: 9,
            body["questions"][2]["id"]: "Stop me re-typing interview notes",
        }
        sent = public.post(
            f"/api/v1/validation/surveys/{token}/responses", json={"answers": answers}
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["data"] == {"received": True}
        capture("validation", "public_response", sent)

        missing = public.get("/api/v1/validation/surveys/not-a-real-token")
        assert missing.status_code == 404, missing.text
        capture("validation", "public_unknown_token", missing)

    # 4. Back as the founder: the evidence is in.
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        wh = {**_auth_header(access), "X-Workspace-Id": workspace_id}

        analytics = c.get(f"/api/v1/validation/surveys/{survey_id}/analytics", headers=wh)
        assert analytics.status_code == 200, analytics.text
        data = analytics.json()["data"]
        assert data["responses"] == 1 and data["completion_rate"] == 100
        assert data["questions"][0]["counts"] == {"Yes": 1, "No": 0}
        capture("validation", "survey_analytics", analytics)

        experiment = c.post(
            "/api/v1/validation/experiments",
            headers=wh,
            json={
                "name": "Fake door landing page",
                "type": "smoke_test",
                "metrics": {"visits": 120, "signups": 9},
                "assumption_ids": [assumption["id"]],
            },
        )
        assert experiment.status_code == 201, experiment.text
        capture("validation", "experiment_created", experiment)

        stats = c.get(
            f"/api/v1/validation/smoke-tests/{experiment.json()['data']['id']}/stats", headers=wh
        )
        assert stats.status_code == 200, stats.text
        assert stats.json()["data"]["conversion"] == 7.5
        capture("validation", "smoke_test_stats", stats)

        interview = c.post(
            "/api/v1/validation/interviews",
            headers=wh,
            json={
                "interviewee": "Ada",
                "held_on": "2026-09-20",
                "verdict": "supports",
                "segment": "fintech",
                "notes": "Asked for it unprompted.",
                "key_quotes": ["I would pay for this today"],
                "assumption_ids": [assumption["id"]],
            },
        )
        assert interview.status_code == 201, interview.text
        capture("validation", "interview_created", interview)

        # 5. Two pieces of evidence, so the assumption is validated.
        validated = c.patch(
            f"/api/v1/validation/assumptions/{assumption['id']}",
            headers=wh,
            json={"status": "validated"},
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["data"]["status"] == "validated"
        assert validated.json()["data"]["evidence_count"] == 2
        capture("validation", "assumption_validated", validated)

        listed = c.get("/api/v1/validation/assumptions?status=validated", headers=wh)
        assert [a["id"] for a in listed.json()["data"]["assumptions"]] == [assumption["id"]]
        capture("validation", "assumptions_list", listed)
