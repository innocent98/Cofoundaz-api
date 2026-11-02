"""Live journey: Finance final re-check fixes D10 (imported rows) and D9(a) (stuck AI reply).

D10: every imported ledger source (here Mono bank and Paystack, both synced through the stubbed
provider HTTP) is category-only; amount/direction edits and DELETE answer 422 with the same
"Imported transactions are read-only except the category." shape, while a manual row stays fully
editable and deletable.

D9(a): an assistant reply whose LLM call keeps failing with an empty completion is retried by the
in-process worker (same database as the server) and, after the last attempt, ends `failed` with a
founder-visible message instead of staying `generating`. A reply whose job never runs is failed
once it is older than AI_REPLY_STALE_SECONDS (aged here by editing its created_at in the shared
database, since the real window is minutes).

NOT exercised live: the real model (the server runs LLM_PROVIDER=stub); the empty-output cause is
unit tested against the OpenAI client's wire shape.
Every response body is captured, untrimmed, to `e2e/_captures/finance_d9_d10/*.json`.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx

G = "finance_d9_d10"
API = "/api/v1"
F = f"{API}/finance"
READ_ONLY = "Imported transactions are read-only except the category."


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _EmptyLLM:
    last_usage_tokens = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        raise RuntimeError("LLM API returned an empty completion (finish_reason=length).")


def _run_reply_jobs(message_id: str, rounds: int) -> list[tuple[str, int]]:
    """Run the in-process worker up to `rounds` times (jobs left by earlier journeys share each
    batch of 10, so our job may need extra rounds), making the (backed-off) reply job due each time.
    Returns the job's (status, attempts) after each round."""
    from app.db.models.job import Job
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _a  # noqa: F401

    trail: list[tuple[str, int]] = []
    for _ in range(rounds):
        db = SessionLocal()
        try:
            for job in db.query(Job).filter(Job.type == "ai.cofounder.reply"):
                if job.payload.get("message_id") == message_id and job.run_after is not None:
                    job.run_after = datetime.now(UTC)
            db.commit()
            runner.run_once(db)
            for job in db.query(Job).filter(Job.type == "ai.cofounder.reply"):
                if job.payload.get("message_id") == message_id:
                    trail.append((str(job.status), job.attempts))
            if trail and trail[-1][0] in ("succeeded", "failed"):
                break
        finally:
            db.close()
    return trail


def test_finance_d9_d10_journey(base_url, make_verified_user, capture, monkeypatch):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post(f"{API}/auth/login", json=u).json()["data"]["access_token"])
        c.get(f"{API}/onboarding/state", headers=auth)
        c.patch(
            f"{API}/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"}
        )
        c.patch(f"{API}/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            f"{API}/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch(f"{API}/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        resp = c.post(f"{API}/onboarding/complete", headers=auth)
        assert resp.status_code == 200
        ws = c.get(f"{API}/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        # ---- D10: sync a Paystack and a Mono bank row into the ledger ---------------------------
        ps = c.post(
            f"{F}/integrations/paystack/connect",
            headers=wh,
            json={"secret_key": f"sk_test_{uuid.uuid4().hex}"},
        )
        assert ps.status_code == 200, ps.text
        resp = c.post(f"{F}/integrations/paystack/sync", headers=wh)
        assert resp.status_code == 200
        mono = c.post(f"{F}/integrations/mono/connect", headers=wh, json={"code": "code_ok"})
        assert mono.status_code == 200, mono.text
        resp = c.post(f"{F}/integrations/mono/sync", headers=wh)
        assert resp.status_code == 200

        ledger = c.get(f"{F}/transactions", headers=wh).json()["data"]["transactions"]
        by_source: dict[str, dict] = {}
        for row in ledger:
            by_source.setdefault(row["source"], row)
        assert {"bank", "paystack"} <= set(by_source), sorted(by_source)

        for source in ("bank", "paystack"):
            row = by_source[source]
            url = f"{F}/transactions/{row['id']}"
            patched = c.patch(url, headers=wh, json={"amount_minor": 100, "direction": "out"})
            assert patched.status_code == 422, patched.text
            assert patched.json()["error"]["field_errors"][0]["message"] == READ_ONLY
            capture(G, f"{source}_patch_amount_422", patched)
            deleted = c.delete(url, headers=wh)
            assert deleted.status_code == 422, deleted.text
            assert deleted.json()["error"]["field_errors"][0]["message"] == READ_ONLY
            capture(G, f"{source}_delete_422", deleted)
            recat = c.patch(url, headers=wh, json={"category": "Sales"})
            assert recat.status_code == 200, recat.text
            assert recat.json()["data"]["category"] == "Sales"
            assert recat.json()["data"]["amount_minor"] == row["amount_minor"]
            capture(G, f"{source}_patch_category_200", recat)

        after = c.get(f"{F}/transactions", headers=wh).json()["data"]["transactions"]
        assert {r["id"] for r in ledger} == {r["id"] for r in after}  # nothing was deleted

        manual = c.post(
            f"{F}/transactions",
            headers=wh,
            json={
                "date": datetime.now(UTC).date().isoformat(),
                "description": "Office snacks",
                "amount_minor": 5000,
                "currency": "NGN",
                "direction": "out",
            },
        )
        assert manual.status_code == 200, manual.text
        mid = manual.json()["data"]["id"]
        edited = c.patch(f"{F}/transactions/{mid}", headers=wh, json={"amount_minor": 7000})
        assert edited.status_code == 200 and edited.json()["data"]["amount_minor"] == 7000
        resp = c.delete(f"{F}/transactions/{mid}", headers=wh)
        assert resp.status_code == 200

        # Release the stubbed bank account so other journeys can link it.
        resp = c.post(f"{F}/integrations/mono/disconnect", headers=wh)
        assert resp.status_code == 200

        # ---- D9(a): an empty-completion reply ends failed, never stuck generating ---------------
        monkeypatch.setattr("app.platform.llm_budget.get_llm_client", lambda: _EmptyLLM())
        started = c.post(
            f"{API}/ai/conversations",
            headers=wh,
            json={"message": "What levers should I look at first?"},
        )
        assert started.status_code == 201, started.text
        conv = started.json()["data"]
        capture(G, "chat_started", started)
        assistant = next(m for m in conv["messages"] if m["role"] == "assistant")
        assert assistant["status"] == "generating"

        trail = _run_reply_jobs(assistant["id"], rounds=20)  # WORKER_MAX_ATTEMPTS defaults to 5
        assert trail[-1] == ("succeeded", 5), trail  # 4 requeues, then the turn is settled
        got = c.get(f"{API}/ai/conversations/{conv['id']}", headers=wh)
        capture(G, "chat_after_last_retry", got)
        reply = next(m for m in got.json()["data"]["messages"] if m["role"] == "assistant")
        assert reply["status"] == "failed"
        assert reply["content"]
        followup = c.post(
            f"{API}/ai/conversations/{conv['id']}/messages",
            headers=wh,
            json={"content": "Give me three ways to extend my runway."},
        )
        assert followup.status_code == 202, followup.text  # the founder can ask again

        # ---- D9(a): a reply whose job never ran is failed once stale ----------------------------
        from app.db.models.ai_chat import AiMessage
        from app.db.session import SessionLocal

        pending_id = followup.json()["data"]["id"]
        db = SessionLocal()
        try:
            row = db.get(AiMessage, uuid.UUID(pending_id))
            row.created_at = datetime.now(UTC) - timedelta(hours=2)
            db.commit()
        finally:
            db.close()
        stale = c.get(f"{API}/ai/conversations/{conv['id']}", headers=wh)
        capture(G, "chat_stale_failed", stale)
        msgs = [m for m in stale.json()["data"]["messages"] if m["role"] == "assistant"]
        assert [m["status"] for m in msgs] == ["failed", "failed"]
