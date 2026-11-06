"""Live: AI Co-Founder conversation management (slice 1).

Validation (blank message -> 422), `query` / `topic` / `archived` list params, bulk archive and
unarchive, "Delete forever", the per-role gate on /ai/status, and archived chats leaving ⌘K
search. The reply is filled by the in-process worker with LLM_PROVIDER=stub, so the reply text
is the stub's fixed string, not a model answer. Bodies are captured to
e2e/_captures/ai_conversations/ (the source for docs/fe-integration-guide-ai-conversations.md).
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
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
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


def _ask_new(c, h) -> str:
    r = c.post(C, headers=h, json={"message": "Anything on my mind"})
    assert r.status_code == 201
    return r.json()["data"]["id"]


def test_conversation_management(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _onboard(c, _auth(access))

        # --- validation (rows 12, 13)
        r = c.post(C, headers=h, json={})
        assert r.status_code == 422
        capture("ai_conversations", "create_missing_message_422", r)
        r = c.post(C, headers=h, json={"message": "   "})
        assert r.status_code == 422
        capture("ai_conversations", "create_blank_message_422", r)
        assert c.get(C, headers=h).json()["data"] == []

        # --- create two, let the worker answer them
        a = c.post(C, headers=h, json={"message": "Draft an NDA for a contractor"})
        assert a.status_code == 201, a.text
        capture("ai_conversations", "create_201", a)
        a_id = a.json()["data"]["id"]
        _drain()
        b = c.post(C, headers=h, json={"message": "How long is my runway?"})
        assert b.status_code == 201
        b_id = b.json()["data"]["id"]
        _drain()

        blank = c.post(f"{C}/{a_id}/messages", headers=h, json={"content": "  "})
        assert blank.status_code == 422
        capture("ai_conversations", "post_blank_content_422", blank)

        # --- list / query / topic
        listed = c.get(C, headers=h)
        assert listed.status_code == 200
        capture("ai_conversations", "list", listed)
        assert {x["id"] for x in listed.json()["data"]} == {a_id, b_id}

        hit = c.get(C, headers=h, params={"query": "narrative"})  # only in the stub reply text
        assert hit.status_code == 200
        capture("ai_conversations", "list_query_reply_match", hit)
        assert hit.json()["data"] and "match_snippet" in hit.json()["data"][0]

        title_hit = c.get(C, headers=h, params={"query": "runway"})
        assert [x["id"] for x in title_hit.json()["data"]] == [b_id]
        capture("ai_conversations", "list_query_title_match", title_hit)

        short = c.get(C, headers=h, params={"query": " a "})
        assert short.status_code == 422
        capture("ai_conversations", "list_query_too_short_422", short)

        bad_topic = c.get(C, headers=h, params={"topic": "nope"})
        assert bad_topic.status_code == 422
        capture("ai_conversations", "list_bad_topic_422", bad_topic)
        by_topic = c.get(C, headers=h, params={"topic": "finance"})
        # the orchestrator tagged the runway question as finance when the worker answered it
        assert by_topic.status_code == 200
        assert [x["id"] for x in by_topic.json()["data"]] == [b_id]
        capture("ai_conversations", "list_topic_filter", by_topic)

        # --- archive
        arch = c.post(f"{C}/archive", headers=h, json={"ids": [a_id]})
        assert arch.status_code == 200
        capture("ai_conversations", "archive_200", arch)
        assert [x["id"] for x in c.get(C, headers=h).json()["data"]] == [b_id]
        only_arch = c.get(C, headers=h, params={"archived": "true"})
        assert [x["id"] for x in only_arch.json()["data"]] == [a_id]
        capture("ai_conversations", "list_archived", only_arch)
        assert c.get(C, headers=h, params={"query": "narrative"}).json()["data"][0]["id"] == b_id
        both = c.get(C, headers=h, params={"archived": "true", "query": "nda"})
        assert both.status_code == 422
        capture("ai_conversations", "list_archived_with_query_422", both)
        sent = c.post(f"{C}/{a_id}/messages", headers=h, json={"content": "hello again"})
        assert sent.status_code == 409
        capture("ai_conversations", "post_to_archived_409", sent)
        missing = c.post(
            f"{C}/archive", headers=h, json={"ids": [a_id, "00000000-0000-0000-0000-000000000001"]}
        )
        assert missing.status_code == 404
        capture("ai_conversations", "archive_unknown_id_404", missing)
        un = c.post(f"{C}/unarchive", headers=h, json={"ids": [a_id]})
        assert un.status_code == 200
        capture("ai_conversations", "unarchive_200", un)

        # --- delete is refused while a reply is generating (no worker run in between)
        busy = _ask_new(c, h)
        refused = c.delete(f"{C}/{busy}", headers=h)
        assert refused.status_code == 409
        capture("ai_conversations", "delete_while_generating_409", refused)
        blank_q = c.get(C, headers=h, params={"query": "   "})
        assert blank_q.status_code == 200 and "match_snippet" not in blank_q.text
        capture("ai_conversations", "list_blank_query_is_no_query", blank_q)
        _drain()

        # --- delete forever
        gone = c.delete(f"{C}/{a_id}", headers=h)
        assert gone.status_code == 200
        capture("ai_conversations", "delete_200", gone)
        after = c.get(f"{C}/{a_id}", headers=h)
        assert after.status_code == 404
        capture("ai_conversations", "get_deleted_404", after)

        # --- status is F + TM only; a founder still gets it
        assert c.get("/api/v1/ai/status", headers=h).status_code == 200
