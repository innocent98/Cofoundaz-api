"""Live onboarding journey: founder walks the wizard, invites a teammate who
signs up + accepts, then completes onboarding and gets the two queued jobs.
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_onboarding_journey(base_url, make_verified_user, mailbox, unique_email):
    with httpx.Client(base_url=base_url, timeout=10.0) as founder:
        u = make_verified_user(founder)
        access = founder.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _auth_header(access)

        st = founder.get("/api/v1/onboarding/state", headers=h)
        assert st.status_code == 200 and st.json()["data"]["step"] == 1

        founder.patch(
            "/api/v1/onboarding/state", headers=h, json={"step": 1, "full_name": "Ada Founder"}
        )
        founder.patch("/api/v1/onboarding/state", headers=h, json={"step": 2, "name": "Cofoundaz"})
        founder.patch(
            "/api/v1/onboarding/state",
            headers=h,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
        )
        founder.patch(
            "/api/v1/onboarding/state",
            headers=h,
            json={"step": 4, "goals": ["Get first customers"]},
        )

        teammate_email = unique_email("mate")
        inv = founder.post(
            "/api/v1/onboarding/invites",
            headers=h,
            json={"invites": [{"email": teammate_email, "role": "team_member"}]},
        )
        assert inv.status_code == 200 and teammate_email in inv.json()["data"]["created"]
        invite_token = mailbox.latest_token_for(teammate_email, subject_contains="invited")

    # Teammate signs up + verifies + accepts (separate client / user). Their email
    # MUST equal the invited email, because acceptance is email-bound — so sign up
    # explicitly with teammate_email rather than a random one.
    with httpx.Client(base_url=base_url, timeout=10.0) as mate:
        pw = "Mate-" + teammate_email.split("@")[0] + "-9"
        mate.post("/api/v1/auth/signup", json={"email": teammate_email, "password": pw})
        vtok = mailbox.latest_token_for(teammate_email, subject_contains="Verify")
        mate.post("/api/v1/auth/verify", json={"token": vtok})
        maccess = mate.post(
            "/api/v1/auth/login", json={"email": teammate_email, "password": pw}
        ).json()["data"]["access_token"]
        acc = mate.post(
            "/api/v1/invitations/accept",
            json={"token": invite_token},
            headers=_auth_header(maccess),
        )
        assert acc.status_code == 200, acc.text
        me = mate.get("/api/v1/auth/me", headers=_auth_header(maccess)).json()["data"]
        assert any(ms["name"] == "Cofoundaz" for ms in me["memberships"])

    # Founder completes onboarding -> two queued jobs.
    with httpx.Client(base_url=base_url, timeout=10.0) as founder:
        access = founder.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        done = founder.post("/api/v1/onboarding/complete", headers=_auth_header(access))
        assert done.status_code == 200, done.text
        data = done.json()["data"]
        assert len(data["job_ids"]) == 2 and data["assessment_pending"] is True
        for jid in data["job_ids"]:
            job = founder.get(f"/api/v1/jobs/{jid}")
            assert job.status_code == 200 and job.json()["data"]["status"] == "queued"
