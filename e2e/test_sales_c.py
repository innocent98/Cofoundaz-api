"""Live Sales slice C journey: AI role-play coach (202 + generating + poll protocol, 409 guards,
scorecard), call prep (one in flight per deal, regenerate), and the analytics endpoint, plus the
mentor 403, a cross-workspace deal id, and the over-budget states.

One founder workspace. Every request/response body is captured to `e2e/_captures/sales_c/*.json`;
those files are the verbatim source of `docs/fe-integration-guide-sales-coach-analytics.md`.

The LLM is the deterministic STUB (LLM_PROVIDER=stub): buyer replies read "[stub-llm] ...",
scorecard scores are all 0 and feedback is a stub line, call prep text is stub text. The SHAPES are
real; the wording and scores are not. Real-model quality is checked in the FE walk.

The worker runs in this process (`_drain`), against the same database as the server.

Not exercised live (labelled in the guide): the consultant 403 (same role gate as the mentor),
a model that raises mid-call (unit-tested).

Keep app-internal imports LAZY (inside helpers): the remote live-e2e gate has no app env.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx

G = "sales_c"
S = "/api/v1/sales"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def _join(c, wh, ws, role, unique_email, mailbox):
    """Invite `role` to the workspace and accept as a freshly verified user."""
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Ev-{uuid.uuid4().hex[:12]}-9"
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


def _drain() -> None:
    """Run every queued job to completion, in-process (the same loop the real worker runs)."""
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import (
        sales_ai,  # noqa: F401  (registers ai.sales.*)
        scheduled,  # noqa: F401
    )

    register_notifications()
    db = SessionLocal()
    try:
        for _ in range(500):  # the jobs table is shared with every earlier journey
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _sql(stmt: str, **params) -> None:
    """Backdate rows no endpoint can backdate (created_at / closed_at), to get real-looking numbers."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text(stmt), params)
        db.commit()
    finally:
        db.close()


def _spend_budget(startup_id: str, tokens: int) -> None:
    from app.db.models.llm_usage import LlmUsageDaily
    from app.db.session import SessionLocal
    from app.platform.llm_budget import today_utc

    db = SessionLocal()
    try:
        db.add(
            LlmUsageDaily(
                startup_id=uuid.UUID(startup_id), usage_date=today_utc(), tokens_used=tokens
            )
        )
        db.commit()
    finally:
        db.close()


def _set_budget(value: int) -> None:
    from app.core.config import settings

    settings.LLM_DAILY_TOKEN_BUDGET = value


def test_sales_c_journey(base_url, make_verified_user, unique_email, mailbox, capture):
    from app.core.config import settings

    original_budget = settings.LLM_DAILY_TOKEN_BUDGET
    try:
        _journey(base_url, make_verified_user, unique_email, mailbox, capture)
    finally:
        _set_budget(original_budget)


def _journey(base_url, make_verified_user, unique_email, mailbox, capture):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        _onboard(c, auth)
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        stages = {s["name"]: s["id"] for s in c.get(f"{S}/stages", headers=wh).json()["data"]}

        # 1. A deal with an account, contact and a logged call: the coach's raw material.
        acc = c.post(
            f"{S}/accounts", headers=wh, json={"name": "Acme Logistics", "industry": "Logistics"}
        )
        assert acc.status_code == 200, acc.text
        account_id = acc.json()["data"]["id"]
        con = c.post(
            f"{S}/contacts",
            headers=wh,
            json={
                "first_name": "Tunde",
                "last_name": "Bello",
                "title": "CFO",
                "account_id": account_id,
            },
        )
        assert con.status_code == 200, con.text
        deal = c.post(
            f"{S}/deals",
            headers=wh,
            json={
                "name": "Acme fleet payments",
                "account_id": account_id,
                "contact_id": con.json()["data"]["id"],
                "value_minor": 250_000_00,
                "stage_id": stages["Proposal"],
                "next_step": "Send pricing",
            },
        )
        assert deal.status_code == 200, deal.text
        deal_id = deal.json()["data"]["id"]
        act = c.post(
            f"{S}/activities",
            headers=wh,
            json={
                "kind": "call",
                "body": "Discovery call. Worried about switching cost.",
                "deal_id": deal_id,
            },
        )
        assert act.status_code == 200, act.text

        # 2. Role-play from the deal, with an opening line (201 + a generating buyer turn).
        setup = {
            "deal_id": deal_id,
            "persona": "Skeptical CFO",
            "difficulty": "tough",
            "message": "Hi Tunde, thanks for making time.",
        }
        r = c.post(f"{S}/coach/sessions", headers=wh, json=setup)
        assert r.status_code == 201, r.text
        sess = r.json()["data"]
        assert sess["status"] == "active" and sess["messages"][1]["status"] == "generating"
        capture(G, "session_created_from_deal", r)
        sid = sess["id"]

        # 3. Poll protocol: a second turn while the buyer is still replying is a 409.
        busy = c.post(f"{S}/coach/sessions/{sid}/messages", headers=wh, json={"content": "Hello?"})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "REPLY_IN_PROGRESS"
        capture(G, "message_error_reply_in_progress", busy)
        busy_end = c.post(f"{S}/coach/sessions/{sid}/end", headers=wh)
        assert busy_end.status_code == 409, busy_end.text
        capture(G, "end_error_reply_in_progress", busy_end)
        polled = c.get(f"{S}/coach/sessions/{sid}", headers=wh)
        assert polled.json()["data"]["messages"][1]["status"] == "generating"
        capture(G, "session_polled_generating", polled)

        _drain()
        ready = c.get(f"{S}/coach/sessions/{sid}", headers=wh)
        assert ready.json()["data"]["messages"][1]["status"] == "ready"
        capture(G, "session_polled_ready", ready)

        posted = c.post(
            f"{S}/coach/sessions/{sid}/messages",
            headers=wh,
            json={"content": "What does switching cost you today?"},
        )
        assert posted.status_code == 202, posted.text
        assert posted.json()["data"]["status"] == "generating"
        capture(G, "message_posted_202", posted)
        _drain()

        # 4. End: 202 with status "scoring", then the scorecard once the job ran.
        ended = c.post(f"{S}/coach/sessions/{sid}/end", headers=wh)
        assert ended.status_code == 202, ended.text
        assert ended.json()["data"]["status"] == "scoring"
        capture(G, "end_202_scoring", ended)
        dup_end = c.post(f"{S}/coach/sessions/{sid}/end", headers=wh)
        assert (
            dup_end.status_code == 409 and dup_end.json()["error"]["code"] == "SCORING_IN_PROGRESS"
        )
        capture(G, "end_error_scoring_in_progress", dup_end)
        _drain()
        scored = c.get(f"{S}/coach/sessions/{sid}", headers=wh)
        card = scored.json()["data"]
        assert card["status"] == "ended" and card["scorecard"]["scores"].keys() == {
            "discovery",
            "objection_handling",
            "next_step_close",
        }
        capture(G, "session_scored", scored)
        again = c.post(f"{S}/coach/sessions/{sid}/end", headers=wh)
        assert again.status_code == 202 and again.json()["data"]["scorecard"] is not None
        capture(G, "end_again_after_scored", again)
        late = c.post(
            f"{S}/coach/sessions/{sid}/messages", headers=wh, json={"content": "One more"}
        )
        assert late.status_code == 409 and late.json()["error"]["code"] == "SESSION_ENDED"
        capture(G, "message_error_session_ended", late)

        # 5. "Practice this again" = a new session with the same setup (free scenario this time).
        free = c.post(
            f"{S}/coach/sessions",
            headers=wh,
            json={
                "scenario": "Cold call to a bakery chain about card readers",
                "persona": "Busy owner",
            },
        )
        assert free.status_code == 201 and free.json()["data"]["difficulty"] == "neutral"
        capture(G, "session_created_from_scenario", free)
        nothing = c.post(f"{S}/coach/sessions/{free.json()['data']['id']}/end", headers=wh)
        assert nothing.status_code == 422 and nothing.json()["error"]["code"] == "NOTHING_TO_SCORE"
        capture(G, "end_error_nothing_to_score", nothing)
        listing = c.get(f"{S}/coach/sessions", headers=wh)
        assert len(listing.json()["data"]) == 2
        capture(G, "sessions_list", listing)
        for name, body in (
            ("setup_error_no_deal_or_scenario", {"persona": "CFO"}),
            (
                "setup_error_bad_difficulty",
                {"scenario": "x", "persona": "CFO", "difficulty": "brutal"},
            ),
        ):
            bad = c.post(f"{S}/coach/sessions", headers=wh, json=body)
            assert bad.status_code == 422, bad.text
            capture(G, name, bad)

        # 6. Call prep: empty, request, one in flight, ready, regenerate.
        empty = c.get(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert empty.status_code == 200 and empty.json()["data"] is None
        capture(G, "call_prep_none_yet", empty)
        req = c.post(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert req.status_code == 202 and req.json()["data"]["status"] == "generating"
        capture(G, "call_prep_requested_202", req)
        dup = c.post(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "CALL_PREP_IN_PROGRESS"
        capture(G, "call_prep_error_in_progress", dup)
        _drain()
        prep = c.get(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert prep.json()["data"]["status"] == "ready"
        assert set(prep.json()["data"]["output"]) == {
            "context",
            "objections",
            "call_goal",
            "discovery_questions",
        }
        capture(G, "call_prep_ready", prep)
        regen = c.post(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert regen.status_code == 202 and regen.json()["data"]["id"] != prep.json()["data"]["id"]
        capture(G, "call_prep_regenerated_202", regen)
        _drain()

        # 7. Analytics: build a small history, then read it.
        won1 = c.post(
            f"{S}/deals", headers=wh, json={"name": "Bolt Foods", "value_minor": 400_000_00}
        )
        won1_id = won1.json()["data"]["id"]
        for sname in ("Qualified", "Closed Won"):
            assert (
                c.post(
                    f"{S}/deals/{won1_id}/stage", headers=wh, json={"stage_id": stages[sname]}
                ).status_code
                == 200
            )
        _sql("UPDATE deals SET created_at = now() - interval '12 days' WHERE id = :id", id=won1_id)
        won2 = c.post(
            f"{S}/deals", headers=wh, json={"name": "Kora Mart", "value_minor": 150_000_00}
        )
        won2_id = won2.json()["data"]["id"]
        assert (
            c.post(
                f"{S}/deals/{won2_id}/stage", headers=wh, json={"stage_id": stages["Closed Won"]}
            ).status_code
            == 200
        )
        _sql(
            "UPDATE deals SET created_at = now() - interval '70 days', closed_at = now() - interval '45 days' WHERE id = :id",
            id=won2_id,
        )
        lost1 = c.post(
            f"{S}/deals", headers=wh, json={"name": "Dune Ltd", "value_minor": 90_000_00}
        )
        lost1_id = lost1.json()["data"]["id"]
        assert (
            c.post(
                f"{S}/deals/{lost1_id}/stage", headers=wh, json={"stage_id": stages["Negotiation"]}
            ).status_code
            == 200
        )
        assert (
            c.post(
                f"{S}/deals/{lost1_id}/stage",
                headers=wh,
                json={"stage_id": stages["Closed Lost"], "closed_reason": "price"},
            ).status_code
            == 200
        )
        for body in ("Pricing call", "Follow-up email"):
            kind = "call" if "call" in body else "email"
            assert (
                c.post(
                    f"{S}/activities",
                    headers=wh,
                    json={"kind": kind, "body": body, "deal_id": lost1_id},
                ).status_code
                == 200
            )

        an = c.get(f"{S}/analytics", headers=wh)
        assert an.status_code == 200, an.text
        d = an.json()["data"]
        assert d["won"]["count"] == 1 and d["win_loss"]["loss_reasons"][0]["reason"] == "price"
        assert d["leaderboard"][0]["total"] == 3 and len(d["monthly_revenue"]) == 12
        capture(G, "analytics_default_range", an)
        wide = c.get(
            f"{S}/analytics",
            params={
                "from": (datetime.now(UTC) - timedelta(days=90)).date().isoformat(),
                "to": datetime.now(UTC).date().isoformat(),
            },
            headers=wh,
        )
        assert wide.status_code == 200 and wide.json()["data"]["won"]["count"] == 2
        capture(G, "analytics_wide_range", wide)
        for name, params in (
            ("analytics_error_from_after_to", {"from": "2026-10-02", "to": "2026-10-01"}),
            ("analytics_error_range_too_long", {"from": "2024-01-01", "to": "2026-10-01"}),
            ("analytics_error_bad_date", {"from": "yesterday"}),
        ):
            bad = c.get(f"{S}/analytics", params=params, headers=wh)
            assert bad.status_code == 422, bad.text
            capture(G, name, bad)

        # 8. Roles and workspaces: mentor is shut out; another workspace's deal id is rejected.
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)
        for name, method, path, body in (
            (
                "mentor_coach_session_403",
                "POST",
                "/coach/sessions",
                {"scenario": "x", "persona": "CFO"},
            ),
            ("mentor_call_prep_403", "POST", f"/deals/{deal_id}/call-prep", None),
            ("mentor_analytics_403", "GET", "/analytics", None),
        ):
            m = c.request(method, S + path, headers=mentor, json=body)
            assert m.status_code == 403, m.text
            capture(G, name, m)
        other = make_verified_user(c)
        oauth = _auth(c.post("/api/v1/auth/login", json=other).json()["data"]["access_token"])
        _onboard(c, oauth)
        ows = c.get("/api/v1/auth/me", headers=oauth).json()["data"]["active_workspace_id"]
        oh = {**oauth, "X-Workspace-Id": ows}
        foreign = c.post(
            f"{S}/coach/sessions", headers=oh, json={"deal_id": deal_id, "persona": "CFO"}
        )
        assert foreign.status_code == 422, foreign.text
        capture(G, "setup_error_foreign_deal", foreign)
        fprep = c.post(f"{S}/deals/{deal_id}/call-prep", headers=oh)
        assert fprep.status_code == 404, fprep.text
        capture(G, "call_prep_foreign_deal_404", fprep)
        peek = c.get(f"{S}/coach/sessions/{sid}", headers=oh)
        assert peek.status_code == 404, peek.text
        capture(G, "session_foreign_404", peek)

        # 9. Over the daily AI budget: the same endpoints answer 202/201, the job then marks the
        # row failed (the FE polls and shows a retry).
        _set_budget(1_000)
        _spend_budget(ws, 1_000)
        s3 = c.post(f"{S}/coach/sessions", headers=wh, json={**setup, "message": "Hello again"})
        assert s3.status_code == 201, s3.text
        p3 = c.post(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert p3.status_code == 202, p3.text
        _drain()
        failed = c.get(f"{S}/coach/sessions/{s3.json()['data']['id']}", headers=wh)
        assert failed.json()["data"]["messages"][1]["status"] == "failed"
        capture(G, "session_buyer_turn_failed_over_budget", failed)
        pfail = c.get(f"{S}/deals/{deal_id}/call-prep", headers=wh)
        assert (
            pfail.json()["data"]["status"] == "failed"
            and pfail.json()["data"]["error"] == "over_budget"
        )
        capture(G, "call_prep_failed_over_budget", pfail)
        # a session that already has a seller turn can still be ended; its scorecard then fails
        s4 = c.post(f"{S}/coach/sessions", headers=wh, json={"scenario": "x", "persona": "CFO"})
        s4id = s4.json()["data"]["id"]
        _set_budget(0)  # unlimited, so the buyer can answer
        assert (
            c.post(
                f"{S}/coach/sessions/{s4id}/messages", headers=wh, json={"content": "Hi"}
            ).status_code
            == 202
        )
        _drain()
        _set_budget(1_000)
        assert c.post(f"{S}/coach/sessions/{s4id}/end", headers=wh).status_code == 202
        _drain()
        nocard = c.get(f"{S}/coach/sessions/{s4id}", headers=wh)
        assert nocard.json()["data"]["scorecard"] is None
        assert nocard.json()["data"]["scorecard_error"] == "over_budget"
        capture(G, "session_scorecard_failed_over_budget", nocard)
        _set_budget(0)
        retry = c.post(f"{S}/coach/sessions/{s4id}/end", headers=wh)
        assert retry.status_code == 202 and retry.json()["data"]["status"] == "scoring"
        capture(G, "end_retry_after_failed_scorecard", retry)
        _drain()
        assert (
            c.get(f"{S}/coach/sessions/{s4id}", headers=wh).json()["data"]["scorecard"] is not None
        )
