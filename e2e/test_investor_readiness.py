"""Live Investor Readiness journey (slice A): a brand-new founder opens the page (low score, every
dimension present, three missing must-haves), does real work in other hubs (a pitch deck, a
one-pager, a canvas, a competitor, revenue in Finance, a sourced market size), and watches the
score, gaps, history and snapshot respond. Also: input validation, the manual-recompute limit, a
bad history range, and the guard rails for a stranger (no membership) and no token.

Every response goes to e2e/_captures/investor_readiness/ and is the verbatim source of
docs/fe-integration-guide-investor-readiness.md.
"""

from datetime import UTC, datetime, timedelta

import httpx

R = "/api/v1/readiness"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "launch"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Raise funding"]})
    c.post("/api/v1/onboarding/complete", headers=auth)


def _wh(c: httpx.Client, auth: dict) -> dict:
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _join(c: httpx.Client, wh: dict, role: str, unique_email, mailbox) -> dict:
    """Invite `role` into the founder's workspace and accept as a freshly verified user."""
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = "Member-" + email.split("@")[0][-8:] + "-9"
    c.post("/api/v1/auth/signup", json={"email": email, "password": pw, "accepted_terms": True})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    auth = {"Authorization": f"Bearer {access}"}
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": wh["X-Workspace-Id"]}


def _sig(score: dict, key: str) -> dict:
    return next(s for d in score["dimensions"] for s in d["signals"] if s["key"] == key)


def test_investor_readiness_journey(base_url, make_verified_user, capture, unique_email, mailbox):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        auth = {
            "Authorization": f"Bearer {c.post('/api/v1/auth/login', json=u).json()['data']['access_token']}"
        }
        _onboard(c, auth)
        wh = _wh(c, auth)

        # 1. First open: computed on read. Low score, every dimension, three missing must-haves.
        first = c.get(f"{R}/score", headers=wh)
        assert first.status_code == 200, first.text
        s1 = first.json()["data"]
        assert s1["band"] == "not_yet" and s1["ready"] is False and s1["capped"] is False
        assert [d["key"] for d in s1["dimensions"]] == [
            "team",
            "traction",
            "market",
            "product",
            "financials",
            "story",
        ]
        assert [m["met"] for m in s1["must_haves"]] == [False, False, False]
        assert [m["key"] for m in s1["must_haves"]] == [
            "financial_model",
            "pitch_deck_reviewed",
            "traction_proof",
        ]
        assert 1 <= len(s1["gaps"]) <= 5
        capture("investor_readiness", "score_first_open", first)

        # 2. Real work in the other hubs.
        for tmpl in ("pitch_deck", "one_pager"):
            r = c.post("/api/v1/documents", headers=wh, json={"template_key": tmpl})
            assert r.status_code == 201, r.text
        canvas = c.get("/api/v1/business-builder/canvases/lean", headers=wh).json()["data"]
        put = c.put(
            "/api/v1/business-builder/canvases/lean",
            headers=wh,
            json={
                "blocks": {"customer_segments": ["Lagos SMEs"], "solution": ["Invoice app"]},
                "version": canvas["version"],
            },
        )
        assert put.status_code == 200, put.text
        comp = c.post(
            "/api/v1/business-builder/competitors",
            headers=wh,
            json={"data": {"name": "BigCo Rival", "threat_level": "high"}},
        )
        assert comp.status_code in (200, 201), comp.text
        day = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()
        tx = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": day,
                "description": "Customer payment",
                "category": "Sales",
                "amount_minor": 90_000_000,
                "currency": "NGN",
                "direction": "in",
            },
        )
        assert tx.status_code == 200, tx.text

        # 3. Market size: validation, then a sourced value.
        bad = c.put(f"{R}/inputs", headers=wh, json={"tam_minor": 10, "sam_minor": 99})
        assert bad.status_code == 422, bad.text
        capture("investor_readiness", "inputs_invalid", bad)
        empty = c.get(f"{R}/inputs", headers=wh)
        capture("investor_readiness", "inputs_empty", empty)
        ok = c.put(
            f"{R}/inputs",
            headers=wh,
            json={
                "tam_minor": 500_000_000_000,
                "sam_minor": 50_000_000_000,
                "som_minor": 5_000_000_000,
                "currency": "NGN",
                "source_url": "https://example.com/placeholder-market-report",
            },
        )
        assert ok.status_code == 200, ok.text
        capture("investor_readiness", "inputs_saved", ok)

        # 4. The score moved because of that work, and the signals say why.
        after = c.get(f"{R}/score", headers=wh)  # inside 15 minutes: the PUT already recomputed
        s2 = after.json()["data"]
        assert s2["score"] > s1["score"]
        assert _sig(s2, "story.pitch_deck")["score"] == 100
        assert _sig(s2, "story.one_pager")["score"] == 100
        assert _sig(s2, "market.size")["score"] == 100
        assert _sig(s2, "market.competitors")["value"] == 1
        assert _sig(s2, "traction.revenue")["value"] == 300000.0
        capture("investor_readiness", "score_after_work", after)

        # 5. Manual recompute, then the rate limit.
        rc = c.post(f"{R}/score/recompute", headers=wh)
        assert rc.status_code == 200, rc.text
        capture("investor_readiness", "recompute", rc)
        again = c.post(f"{R}/score/recompute", headers=wh)
        assert again.status_code == 429, again.text
        capture("investor_readiness", "recompute_rate_limited", again)

        # 6. History, snapshot, bad range.
        hist = c.get(f"{R}/history", params={"range": 30}, headers=wh)
        assert hist.status_code == 200, hist.text
        assert len(hist.json()["data"]["points"]) >= 2
        capture("investor_readiness", "history_30", hist)
        badr = c.get(f"{R}/history", params={"range": 7}, headers=wh)
        assert badr.status_code == 422, badr.text
        capture("investor_readiness", "history_bad_range", badr)
        snap = c.get(f"{R}/snapshot", headers=wh)
        assert snap.status_code == 200 and "gaps" not in snap.json()["data"]
        capture("investor_readiness", "snapshot", snap)

        # 7. Guard rails: no token, no workspace header, a stranger's workspace.
        noauth = c.get(f"{R}/score", headers={"X-Workspace-Id": wh["X-Workspace-Id"]})
        assert noauth.status_code == 401, noauth.text
        capture("investor_readiness", "no_token", noauth)
        nows = c.get(f"{R}/score", headers=auth)
        assert nows.status_code == 422, nows.text
        capture("investor_readiness", "no_workspace_header", nows)
        stranger = make_verified_user(c)
        sauth = {
            "Authorization": f"Bearer {c.post('/api/v1/auth/login', json=stranger).json()['data']['access_token']}"
        }
        cross = c.get(f"{R}/score", headers={**sauth, "X-Workspace-Id": wh["X-Workspace-Id"]})
        assert cross.status_code == 403, cross.text
        capture("investor_readiness", "other_workspace", cross)

        # 8. Roles: a team member reads the same score but cannot recalculate; a mentor is refused.
        tm = _join(c, wh, "team_member", unique_email, mailbox)
        tm_score = c.get(f"{R}/score", headers=tm)
        assert tm_score.status_code == 200, tm_score.text
        assert (
            tm_score.json()["data"]["score"]
            == c.get(f"{R}/score", headers=wh).json()["data"]["score"]
        )
        tm_recompute = c.post(f"{R}/score/recompute", headers=tm)
        assert tm_recompute.status_code == 403, tm_recompute.text
        capture("investor_readiness", "team_member_recompute_403", tm_recompute)
        mentor = _join(c, wh, "mentor", unique_email, mailbox)
        mentor_score = c.get(f"{R}/score", headers=mentor)
        assert mentor_score.status_code == 403, mentor_score.text
        capture("investor_readiness", "mentor_score_403", mentor_score)
