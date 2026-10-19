"""Live Validation Hub PRD slice (a) journey: overview + riskiest nudge, "Design experiment" (AI
draft, never saved), a smoke test published at /p/{slug} (D-VAL-1), anonymous visitors visiting,
clicking and joining the waitlist with NO authentication, the server-side funnel and verdict,
the waitlist, a slug clash, and deletes.

Every body is captured to `e2e/_captures/validation_prd_a/*.json`, the verbatim source for
`docs/fe-integration-guide-validation-prd-a.md`. Public calls use a separate client with no
headers, so the captures prove those routes need no session and leak nothing.
"""

import httpx

G = "validation_prd_a"
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
    """Run queued AI jobs in-process with the stub LLM (as e2e/test_business_builder.py)."""
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import validation as _v  # noqa: F401  (registers the draft job)

    db = SessionLocal()
    try:
        for _ in range(100):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_validation_prd_a_journey(base_url, make_verified_user, capture):
    with (
        httpx.Client(base_url=base_url, timeout=10.0) as c,
        httpx.Client(base_url=base_url, timeout=10.0) as public,
    ):
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {access}"}
        _onboard(c, auth)
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        # 1. Assumptions + overview with the riskiest untested one.
        c.post(
            f"{V}/assumptions",
            headers=wh,
            json={"statement": "Traders will pay 0.5% for same-day settlement", "risk": "medium"},
        )
        risky = c.post(
            f"{V}/assumptions",
            headers=wh,
            json={"statement": "Market associations will promote us to members", "risk": "high"},
        ).json()["data"]
        ov = c.get(f"{V}/overview", headers=wh)
        assert ov.status_code == 200, ov.text
        assert ov.json()["data"]["riskiest_untested"]["id"] == risky["id"]
        capture(G, "overview", ov)

        # 2. "Design experiment": a draft in the job result, nothing saved.
        q = c.post(f"{V}/assumptions/{risky['id']}/experiment-draft", headers=wh)
        assert q.status_code == 202, q.text
        capture(G, "experiment_draft_queued", q)
        _drain_ai()
        job = c.get(f"/api/v1/jobs/{q.json()['data']['job_id']}", headers=wh)
        assert job.json()["data"]["result"]["status"] == "ready"
        capture(G, "experiment_draft_job", job)
        assert c.get(f"{V}/experiments", headers=wh).json()["data"]["experiments"] == []

        # 3. Build and publish the smoke test.
        created = c.post(
            f"{V}/experiments",
            headers=wh,
            json={
                "name": "Same-day settlement",
                "type": "smoke_test",
                "assumption_ids": [risky["id"]],
                "config": {
                    "headline": "Get paid the same day",
                    "subheadline": "Instant settlement for market traders",
                    "cta_text": "Join the waitlist",
                    "require_email": True,
                    "show_pricing": False,
                    "metric_type": "email_signup",
                    "target_conversion": 10,
                    "slug": "novapay-same-day",
                },
            },
        )
        assert created.status_code == 201, created.text
        capture(G, "smoke_created_draft", created)
        exp = created.json()["data"]
        assert public.get(f"{V}/pages/novapay-same-day").status_code == 404  # draft
        live = c.patch(f"{V}/experiments/{exp['id']}", headers=wh, json={"status": "live"})
        assert live.status_code == 200 and live.json()["data"]["published_at"]
        capture(G, "smoke_published", live)

        # 4. Anonymous visitors.
        page = public.get(f"{V}/pages/novapay-same-day")
        assert page.status_code == 200, page.text
        capture(G, "public_page", page)
        for visitor in ("visitor-a", "visitor-a", "visitor-b", "visitor-c"):
            ev = public.post(
                f"{V}/pages/novapay-same-day/events", json={"kind": "visit", "visitor_id": visitor}
            )
            assert ev.status_code == 201, ev.text
        capture(G, "public_event_visit", ev)
        public.post(
            f"{V}/pages/novapay-same-day/events",
            json={"kind": "cta_click", "visitor_id": "visitor-b"},
        )
        signup = public.post(
            f"{V}/pages/novapay-same-day/signups",
            json={"email": "Funke@Example.com", "visitor_id": "visitor-b"},
        )
        assert signup.status_code == 201, signup.text
        capture(G, "public_signup", signup)
        repeat = public.post(
            f"{V}/pages/novapay-same-day/signups", json={"email": "funke@example.com"}
        )
        capture(G, "public_signup_repeat", repeat)
        capture(
            G,
            "public_signup_bad_email_422",
            public.post(f"{V}/pages/novapay-same-day/signups", json={"email": "not-an-email"}),
        )
        capture(
            G,
            "public_event_bad_kind_422",
            public.post(f"{V}/pages/novapay-same-day/events", json={"kind": "purchase"}),
        )
        capture(G, "public_unknown_slug_404", public.get(f"{V}/pages/no-such-page"))

        # 5. Founder reads the funnel and the waitlist.
        stats = c.get(f"{V}/smoke-tests/{exp['id']}/stats", headers=wh)
        assert stats.json()["data"]["visits"] == 3 and stats.json()["data"]["signups"] == 1
        capture(G, "smoke_stats", stats)
        capture(G, "smoke_waitlist", c.get(f"{V}/smoke-tests/{exp['id']}/signups", headers=wh))
        capture(G, "overview_after_signup", c.get(f"{V}/overview", headers=wh))

        # 6. Slug rules.
        capture(
            G,
            "slug_invalid_422",
            c.post(
                f"{V}/experiments",
                headers=wh,
                json={"name": "Bad", "type": "smoke_test", "config": {"slug": "Not A Slug"}},
            ),
        )
        clash = c.post(
            f"{V}/experiments",
            headers=wh,
            json={"name": "Copy", "type": "smoke_test", "config": {"slug": "novapay-same-day"}},
        )
        assert clash.status_code == 409, clash.text
        capture(G, "slug_taken_409", clash)

        # 7. Deletes.
        deleted = c.delete(f"{V}/experiments/{exp['id']}", headers=wh)
        assert deleted.status_code == 200, deleted.text
        capture(G, "experiment_deleted", deleted)
        assert public.get(f"{V}/pages/novapay-same-day").status_code == 404
        capture(G, "assumption_deleted", c.delete(f"{V}/assumptions/{risky['id']}", headers=wh))
        capture(
            G, "assumption_delete_again_404", c.delete(f"{V}/assumptions/{risky['id']}", headers=wh)
        )
