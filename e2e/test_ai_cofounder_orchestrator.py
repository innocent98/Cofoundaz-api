"""Live: AI Co-Founder orchestrator (slice 2).

Four messages route to the four agents by the keyword rules; an ambiguous one goes to the router
(LLM_PROVIDER=stub answers a JSON call with the first enum value, so the router picks `finance`); Cancel stops a reply before the worker runs; roles other than founder and team member
get 403. FAKED: the model. With LLM_PROVIDER=stub every reply is the fixed string
`[stub-llm] AI-generated assessment narrative.`, so these captures prove routing, context
sources, disclaimers, status handling and access, not answer quality. Bodies are captured to
e2e/_captures/ai_orchestrator/ (the source for docs/fe-integration-guide-ai-cofounder.md).
"""

import httpx

C = "/api/v1/ai/conversations"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.cofounder.reply)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _onboard(c: httpx.Client, auth: dict) -> dict:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "build"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    me = c.get("/api/v1/auth/me", headers=auth)
    return {**auth, "X-Workspace-Id": me.json()["data"]["active_workspace_id"]}


def _join(c, wh, ws, role, unique_email, mailbox):
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = "Member-" + email.split("@")[0][-8:] + "-9"
    c.post("/api/v1/auth/signup", json={"email": email, "password": pw})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    auth = _auth(access)
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": ws}


def _ask(c, h, text):
    r = c.post(C, headers=h, json={"message": text})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_orchestrator_journey(base_url, make_verified_user, unique_email, mailbox, capture):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _onboard(c, _auth(access))
        ws = h["X-Workspace-Id"]
        # give the workspace a roadmap and today's mission so the Strategist has sources
        c.get("/api/v1/roadmap", headers=h)
        c.get("/api/v1/missions/today", headers=h)

        convs = {}
        for name, text in {
            "finance": "How long is my runway?",
            "legal": "Draft an NDA for a contractor",
            "mentor": "I feel overwhelmed and stuck this week",
            "strategist": "What should I focus on this week?",
            "router_stub": "hello there",
        }.items():
            convs[name] = _ask(c, h, text)
        capture(
            "ai_orchestrator",
            "create_generating_201",
            c.get(f"{C}/{convs['finance']['id']}", headers=h),
        )
        _drain()

        for name, conv in convs.items():
            got = c.get(f"{C}/{conv['id']}", headers=h)
            assert got.status_code == 200
            capture("ai_orchestrator", f"reply_{name}", got)
            reply = got.json()["data"]["messages"][1]
            assert reply["status"] == "ready"
            # FAKED: the stub model answers the router call with the first agent in the enum
            # (alphabetical: finance), so the ambiguous message lands on Finance here.
            expected = "finance" if name == "router_stub" else name
            assert reply["agent"]["key"] == expected, (name, reply)
            has_disclaimer = reply["disclaimer"] is not None
            assert has_disclaimer == (name in ("finance", "legal", "router_stub"))

        listed = c.get(C, headers=h, params={"topic": "legal"})
        assert [x["id"] for x in listed.json()["data"]] == [convs["legal"]["id"]]
        capture("ai_orchestrator", "list_topic_legal", listed)

        # follow-up with no keywords stays with the last specialist (carryover)
        fu = c.post(
            f"{C}/{convs['finance']['id']}/messages", headers=h, json={"content": "and next month?"}
        )
        assert fu.status_code == 202
        capture("ai_orchestrator", "post_message_202", fu)
        _drain()
        done = c.get(f"{C}/{convs['finance']['id']}", headers=h).json()["data"]["messages"][-1]
        assert done["agent"]["key"] == "finance"

        # --- cancel before the worker runs
        pending = _ask(c, h, "Can you review the terms of our contract?")
        mid = pending["messages"][1]["id"]
        cancelled = c.post(f"{C}/{pending['id']}/messages/{mid}/cancel", headers=h)
        assert cancelled.status_code == 200
        capture("ai_orchestrator", "cancel_200", cancelled)
        assert cancelled.json()["data"]["status"] == "cancelled"
        _drain()
        after = c.get(f"{C}/{pending['id']}", headers=h).json()["data"]
        assert after["messages"][1]["status"] == "cancelled"
        capture(
            "ai_orchestrator", "conversation_after_cancel", c.get(f"{C}/{pending['id']}", headers=h)
        )
        again = c.post(
            f"{C}/{pending['id']}/messages", headers=h, json={"content": "never mind, runway?"}
        )
        assert again.status_code == 202

        finished_mid = convs["legal"]["messages"][1]["id"]
        late = c.post(f"{C}/{convs['legal']['id']}/messages/{finished_mid}/cancel", headers=h)
        assert late.status_code == 409
        capture("ai_orchestrator", "cancel_after_done_409", late)

        # --- roles
        tm = _join(c, h, ws, "team_member", unique_email, mailbox)
        mentor = _join(c, h, ws, "mentor", unique_email, mailbox)
        accountant = _join(c, h, ws, "accountant", unique_email, mailbox)
        peek = c.get(f"{C}/{convs['finance']['id']}", headers=tm)
        assert peek.status_code == 404
        capture("ai_orchestrator", "other_member_404", peek)
        for name, who in (("mentor", mentor), ("accountant", accountant)):
            r = c.post(f"{C}/{convs['finance']['id']}/messages/{mid}/cancel", headers=who)
            assert r.status_code == 403, r.text
            capture("ai_orchestrator", f"cancel_{name}_403", r)
            assert c.get("/api/v1/ai/status", headers=who).status_code == 403
        mine = _ask(c, tm, "How long is my runway?")
        _drain()
        tm_reply = c.get(f"{C}/{mine['id']}", headers=tm)
        capture("ai_orchestrator", "reply_team_member", tm_reply)
        assert tm_reply.json()["data"]["messages"][1]["agent"]["key"] == "finance"
