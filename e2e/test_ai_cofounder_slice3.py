"""Live: AI Co-Founder slice 3 (actions, feedback, regenerate, suggestions, bench, pinning).

FAKED: the model. The API server runs with LLM_PROVIDER=stub, but replies are produced by the
in-process worker (this test drains it), and this test swaps that process's LLM client for a
scripted one that returns a fixed reply containing an `<actions>` block. So the proposals are
scripted by the test; everything the API does with them (parsing, validation, storing, confirm,
undo, the roadmap and Documents writes, feedback, regenerate, suggestions) is the real server.
Bodies are captured to e2e/_captures/ai_slice3/ (the source for docs/fe-integration-guide-ai-cofounder.md §6).
"""

import json

import httpx

C = "/api/v1/ai/conversations"
AI = "/api/v1/ai"
G = "ai_slice3"
_LOW = {
    "product_stage": "idea",
    "market_clarity": 3,
    "market_research": "none",
    "has_revenue": "no",
    "runway_confidence": 3,
    "incorporated": "no",
    "team_size": "solo",
    "team_confidence": 3,
}


class _Scripted:
    last_usage_tokens = 25

    def __init__(self):
        self.reply = "Here is my take."
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.reply

    def complete_json(self, messages, *, schema, max_tokens):
        return {"agent": "strategist", "confidence": 0.7}


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401

    register_notifications()
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
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]}
    )
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    me = c.get("/api/v1/auth/me", headers=auth)
    return {**auth, "X-Workspace-Id": me.json()["data"]["active_workspace_id"]}


def _complete_assessment(c, h) -> None:
    aid = c.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    while True:
        nq = c.get(f"/api/v1/assessments/{aid}/next-question", headers=h).json()["data"][
            "next_question"
        ]
        if nq is None:
            break
        r = c.post(
            f"/api/v1/assessments/{aid}/answers",
            headers=h,
            json={"question_key": nq["key"], "value": _LOW[nq["key"]]},
        )
        assert r.status_code == 200, r.text
    assert c.post(f"/api/v1/assessments/{aid}/complete", headers=h).status_code == 200


def _ask(c, h, text, **extra):
    r = c.post(C, headers=h, json={"message": text, **extra})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _reply(c, h, cid):
    return c.get(f"{C}/{cid}", headers=h).json()["data"]["messages"][-1]


def test_slice3_journey(base_url, make_verified_user, capture, monkeypatch):
    from app.platform import llm_budget

    llm = _Scripted()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: llm)
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _onboard(c, _auth(access))
        road = c.get("/api/v1/roadmap", headers=h)
        assert road.status_code == 200, road.text
        c.get("/api/v1/missions/today", headers=h)

        # --- bench
        bench = c.get(f"{AI}/agents", headers=h)
        assert bench.status_code == 200 and len(bench.json()["data"]) == 11
        capture(G, "agents", bench)

        # --- a reply that proposes three changes
        llm.reply = (
            "Here is my take.\n<actions>"
            + json.dumps(
                [
                    {
                        "kind": "add_task",
                        "title": "Call five customers",
                        "milestone": "M1",
                        "effort": "small",
                    },
                    {
                        "kind": "update_roadmap",
                        "milestone": "M2",
                        "due_on": "2027-03-01",
                        "status": "in_progress",
                    },
                    {
                        "kind": "draft_document",
                        "doc_kind": "nda",
                        "title": "Contractor NDA",
                        "sections": [{"heading": "Parties", "body": "NovaPay and the contractor."}],
                    },
                ]
            )
            + "</actions>"
        )
        conv = _ask(c, h, "What should I focus on this week?")
        _drain()
        got = c.get(f"{C}/{conv['id']}", headers=h)
        capture(G, "reply_with_actions", got)
        reply = got.json()["data"]["messages"][1]
        assert [a["kind"] for a in reply["actions"]] == [
            "add_task",
            "update_roadmap",
            "draft_document",
        ]
        mid, (a_task, a_road, a_doc) = reply["id"], reply["actions"]
        u_ = lambda a, op: f"{AI}/messages/{mid}/actions/{a['id']}/{op}"  # noqa: E731

        # --- confirm / undo / dismiss
        r = c.post(u_(a_task, "confirm"), headers=h)
        assert r.status_code == 200, r.text
        capture(G, "confirm_add_task", r)
        tasks_after = c.get("/api/v1/roadmap", headers=h).text
        assert "Call five customers" in tasks_after
        r = c.post(u_(a_task, "undo"), headers=h)
        assert r.status_code == 200
        capture(G, "undo_add_task", r)
        assert "Call five customers" not in c.get("/api/v1/roadmap", headers=h).text
        r = c.post(u_(a_task, "confirm"), headers=h)
        assert r.status_code == 409
        capture(G, "confirm_after_undo_409", r)

        r = c.post(u_(a_road, "confirm"), headers=h)
        assert r.status_code == 200, r.text
        capture(G, "confirm_update_roadmap", r)
        r = c.post(u_(a_doc, "confirm"), headers=h)
        assert r.status_code == 200, r.text
        capture(G, "confirm_draft_document", r)
        docs = c.get("/api/v1/documents", headers=h)
        assert "Contractor NDA" in docs.text

        bad = c.post(
            f"{AI}/messages/{mid}/actions/00000000-0000-0000-0000-000000000009/confirm", headers=h
        )
        assert bad.status_code == 404
        capture(G, "confirm_unknown_404", bad)
        thread = c.get(f"{C}/{conv['id']}", headers=h)
        capture(G, "reply_after_confirms", thread)

        regen_blocked = c.post(f"{C}/{conv['id']}/messages/{mid}/regenerate", headers=h)
        assert regen_blocked.status_code == 409
        capture(G, "regenerate_after_action_409", regen_blocked)

        # --- dismiss on a second proposal
        llm.reply = (
            "Another idea.\n<actions>"
            + json.dumps([{"kind": "add_task", "title": "Write the pitch", "milestone": "M1"}])
            + "</actions>"
        )
        conv2 = _ask(c, h, "What should I focus on next?")
        _drain()
        r2 = _reply(c, h, conv2["id"])
        dis = c.post(
            f"{AI}/messages/{r2['id']}/actions/{r2['actions'][0]['id']}/dismiss", headers=h
        )
        assert dis.status_code == 200
        capture(G, "dismiss_200", dis)

        # --- feedback
        fb = c.post(
            f"{AI}/messages/{r2['id']}/feedback",
            headers=h,
            json={"rating": "down", "note": "Too generic"},
        )
        assert fb.status_code == 200
        capture(G, "feedback_down", fb)
        bad_fb = c.post(f"{AI}/messages/{r2['id']}/feedback", headers=h, json={"rating": "meh"})
        assert bad_fb.status_code == 422
        capture(G, "feedback_invalid_422", bad_fb)

        # --- regenerate (the latest reply, now without proposals)
        llm.reply = "A second answer."
        regen = c.post(f"{C}/{conv2['id']}/messages/{r2['id']}/regenerate", headers=h)
        assert regen.status_code == 202, regen.text
        capture(G, "regenerate_202", regen)
        _drain()
        after = c.get(f"{C}/{conv2['id']}", headers=h)
        capture(G, "reply_after_regenerate", after)
        assert after.json()["data"]["messages"][-1]["content"] == "A second answer."
        old = c.post(f"{C}/{conv['id']}/messages/{mid}/regenerate", headers=h)
        assert old.status_code == 409

        # --- pinned agent
        llm.reply = "Pinned answer."
        pinned = _ask(c, h, "hello there", agent_key="mentor")
        _drain()
        capture(G, "pinned_reply", c.get(f"{C}/{pinned['id']}", headers=h))
        assert _reply(c, h, pinned["id"])["agent"]["key"] == "mentor"
        bad_pin = c.post(C, headers=h, json={"message": "hi", "agent_key": "marketing"})
        assert bad_pin.status_code == 422
        capture(G, "pinned_unavailable_422", bad_pin)

        # --- suggestions (health recommendations exist once the assessment is done)
        empty = c.get(f"{AI}/suggestions", headers=h)
        capture(G, "suggestions_before_assessment", empty)
        _complete_assessment(c, h)
        _drain()
        sugg = c.get(f"{AI}/suggestions", headers=h)
        assert sugg.status_code == 200, sugg.text
        capture(G, "suggestions", sugg)
        items = sugg.json()["data"]["items"]
        assert items, "expected at least one card from the health recommendations"
        first, second = items[0]["id"], items[-1]["id"]
        acc = c.post(f"{AI}/suggestions/{first}/accept", headers=h)
        assert acc.status_code == 200
        capture(G, "suggestion_accept", acc)
        if second != first:
            snz = c.post(f"{AI}/suggestions/{second}/snooze", headers=h, json={"days": 3})
            assert snz.status_code == 200
            capture(G, "suggestion_snooze", snz)
        capture(G, "suggestions_after", c.get(f"{AI}/suggestions", headers=h))
        notif = c.get("/api/v1/notifications", headers=h)
        capture(G, "notifications_after_suggestions", notif)
