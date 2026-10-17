"""Live "+ Invite" after onboarding: `POST /team/invitations` (PRD 23, founder only).

A founder finishes onboarding (so `POST /onboarding/invites` would now 409), then invites a
mentor from the top bar. The emailed link is `/invite/{token}` (the FE landing), which previews
via `GET /invitations/{token}`. The mentor signs up, accepts, and is a member with the mentor's
restrictions. Bodies are captured to `e2e/_captures/team_invitations/*.json`, the source for
`docs/fe-integration-guide-team-invitations.md`.
"""

import httpx


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_team_invite_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        mentor_email = unique_email("mentor")
        r = c.post(
            "/api/v1/team/invitations",
            headers=wh,
            json={
                "invites": [
                    {"email": mentor_email, "role": "mentor"},
                    {"email": u["email"], "role": "team_member"},  # already a member
                ]
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {"created": [mentor_email], "skipped": [u["email"]]}
        capture("team_invitations", "invite_created_and_skipped", r)

        bad = c.post(
            "/api/v1/team/invitations",
            headers=wh,
            json={"invites": [{"email": "nope", "role": "investor"}]},
        )
        assert bad.status_code == 422
        capture("team_invitations", "invite_validation_error", bad)

        token = mailbox.latest_token_for(mentor_email, subject_contains="invited")
        preview = c.get(f"/api/v1/invitations/{token}")
        assert preview.status_code == 200, preview.text
        assert preview.json()["data"]["role"] == "mentor"
        capture("team_invitations", "invite_preview", preview)

        pw = "Mentor-" + mentor_email.split("@")[0][-8:] + "-9"
        c.post("/api/v1/auth/signup", json={"email": mentor_email, "password": pw})
        vt = mailbox.latest_token_for(mentor_email, subject_contains="Verify")
        c.post("/api/v1/auth/verify", json={"token": vt})
        mentor = _auth(
            c.post("/api/v1/auth/login", json={"email": mentor_email, "password": pw}).json()[
                "data"
            ]["access_token"]
        )
        acc = c.post("/api/v1/invitations/accept", headers=mentor, json={"token": token})
        assert acc.status_code == 200, acc.text
        capture("team_invitations", "invite_accepted", acc)

        mh = {**mentor, "X-Workspace-Id": ws}
        forbidden = c.post(
            "/api/v1/team/invitations",
            headers=mh,
            json={"invites": [{"email": unique_email("x"), "role": "mentor"}]},
        )
        assert forbidden.status_code == 403
        capture("team_invitations", "invite_by_mentor_forbidden", forbidden)
