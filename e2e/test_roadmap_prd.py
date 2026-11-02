"""Live Roadmap PRD journey (Module 05 + the 23.2 thread): AI "break this down" through the
worker, the members list for pickers, and a milestone comment thread with a mention.

A founder onboards to stage "validation" (roadmap generated inline) and invites a team member
during onboarding. Captures go to `e2e/_captures/roadmap_prd/*.json`, the source for
docs/fe-integration-guide-roadmap-prd.md. The worker is drained in-process (LLM_PROVIDER=stub).
"""

import httpx


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.roadmap.breakdown)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _signup(c: httpx.Client, mailbox, email: str, password: str) -> dict:
    c.post("/api/v1/auth/signup", json={"email": email, "password": password})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    login = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    return _auth(login.json()["data"]["access_token"])


def test_roadmap_prd_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={
                "step": 3,
                "industry": "Fintech",
                "business_model": "b2b",
                "stage": "validation",
            },
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        mate_email = unique_email("mate")
        inv = c.post(
            "/api/v1/onboarding/invites",
            headers=auth,
            json={"invites": [{"email": mate_email, "role": "team_member"}]},
        )
        assert inv.status_code == 200, inv.text
        token = mailbox.latest_token_for(mate_email, subject_contains="invited")
        mate = _signup(c, mailbox, mate_email, "Mate-" + mate_email[:6] + "-9")
        assert (
            c.post("/api/v1/invitations/accept", headers=mate, json={"token": token}).status_code
            == 200
        )
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh, mh = {**auth, "X-Workspace-Id": ws}, {**mate, "X-Workspace-Id": ws}

        tree = c.get("/api/v1/roadmap", headers=wh).json()["data"]
        milestone = tree["phases"][0]["milestones"][0]
        before = len(milestone["tasks"])

        # 1. AI breakdown: 202 + job, a second request while it runs is 409, then poll.
        start = c.post(f"/api/v1/roadmap/milestones/{milestone['id']}/breakdown", headers=wh)
        assert start.status_code == 202, start.text
        capture("roadmap_prd", "breakdown_started", start)
        again = c.post(f"/api/v1/roadmap/milestones/{milestone['id']}/breakdown", headers=wh)
        assert again.status_code == 409
        capture("roadmap_prd", "breakdown_in_progress_409", again)
        _drain()
        job = c.get(f"/api/v1/jobs/{start.json()['data']['job_id']}", headers=wh)
        assert job.json()["data"]["status"] == "succeeded", job.text
        assert job.json()["data"]["result"]["status"] == "ready"
        capture("roadmap_prd", "breakdown_job_done", job)
        after = c.get("/api/v1/roadmap", headers=wh).json()["data"]
        tasks_after = after["phases"][0]["milestones"][0]["tasks"]
        assert len(tasks_after) > before

        # 2. Members for the owner/assignee pickers.
        members = c.get("/api/v1/team/members", headers=wh)
        assert members.status_code == 200, members.text
        roles = {m["role"] for m in members.json()["data"]}
        assert roles == {"founder", "team_member"}
        capture("roadmap_prd", "members", members)
        mate_id = next(m["user_id"] for m in members.json()["data"] if m["role"] == "team_member")

        # 3. Comment thread on the milestone, mentioning the teammate.
        posted = c.post(
            "/api/v1/team/comments",
            headers=wh,
            json={
                "entity_type": "roadmap_milestone",
                "entity_id": milestone["id"],
                "body": "Can you own the interviews?",
                "mention_user_ids": [mate_id],
            },
        )
        assert posted.status_code == 201, posted.text
        capture("roadmap_prd", "comment_created", posted)
        cid = posted.json()["data"]["id"]
        thread = c.get(
            "/api/v1/team/comments",
            headers=mh,
            params={"entity_type": "roadmap_milestone", "entity_id": milestone["id"]},
        )
        assert [x["body"] for x in thread.json()["data"]] == ["Can you own the interviews?"]
        capture("roadmap_prd", "comment_thread", thread)
        feed = c.get("/api/v1/notifications", headers=mh).json()["data"]["notifications"]
        assert any(n["type"] == "team.comment.mentioned" for n in feed)
        hijack = c.patch(f"/api/v1/team/comments/{cid}", headers=mh, json={"body": "x"})
        assert hijack.status_code == 403
        capture("roadmap_prd", "comment_edit_not_author_403", hijack)
        resolved = c.post(f"/api/v1/team/comments/{cid}/resolve", headers=wh, json={})
        assert resolved.status_code == 200 and resolved.json()["data"]["resolved_at"]
        capture("roadmap_prd", "comment_resolved", resolved)
