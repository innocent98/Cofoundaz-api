"""Live Funding Hub journey (slice A): a brand-new founder opens the page (no round, empty state),
adds the first investor (a round appears by itself), sets the raise, builds a pipeline, moves cards
through the seven stages (Passed needs a reason), logs interactions, sees the header bar split
soft from committed, is offered (never forced) a Finance entry when someone commits, confirms the
money arrived, and closes the round. Then a USD round, and the guard rails.

Every response goes to e2e/_captures/funding_pipeline/ and is the verbatim source of
docs/fe-integration-guide-funding.md. Not live here: role 403s and the 402 plan gate (unit-tested;
the e2e server runs with PLAN_ENFORCEMENT off) and the committed-changed bell (a solo founder is the
actor, so nobody else is notified).
"""

from datetime import UTC, datetime, timedelta

import httpx

F = "/api/v1/funding"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "launch"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Raise a seed round"]}
    )
    c.post("/api/v1/onboarding/complete", headers=auth)


def _login(c: httpx.Client, user: dict) -> dict:
    tok = c.post("/api/v1/auth/login", json=user).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _wh(c: httpx.Client, auth: dict) -> dict:
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def test_funding_pipeline_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        auth = _login(c, u)
        _onboard(c, auth)
        wh = _wh(c, auth)
        today = datetime.now(UTC).date()

        # 1. Brand new: nothing exists. This is the empty state, not an error.
        r = c.get(f"{F}/round", headers=wh)
        assert r.status_code == 200 and r.json()["data"] is None
        capture("funding_pipeline", "round_none", r)
        r = c.get(f"{F}/overview", headers=wh)
        ov = r.json()["data"]
        assert ov["empty"] is True and ov["round"] is None and ov["totals"] is None
        assert [s["stage"] for s in ov["stages"]][0] == "research"
        capture("funding_pipeline", "overview_empty", r)
        r = c.get(f"{F}/investors", headers=wh)
        assert r.json()["data"]["items"] == [] and r.json()["data"]["round_id"] is None
        capture("funding_pipeline", "investors_empty", r)

        # 2. "Add your first investor": a planning round appears by itself.
        r = c.post(
            f"{F}/investors",
            headers=wh,
            json={
                "name": "Tolu Bello",
                "firm": "Lagos Angels",
                "type": "angel",
                "warm": True,
                "check_min_minor": 500_000_000,
                "check_max_minor": 2_000_000_000,
                "next_step": "Send the deck",
                "next_step_on": (today + timedelta(days=3)).isoformat(),
                "thesis_notes": "Backs fintech founders in Lagos.",
            },
        )
        assert r.status_code == 201, r.text
        tolu = r.json()["data"]
        assert tolu["stage"] == "research" and tolu["position"] == 0
        capture("funding_pipeline", "investor_created_first", r)
        r = c.get(f"{F}/round", headers=wh)
        rnd = r.json()["data"]
        assert (
            rnd["status"] == "planning" and rnd["currency"] == "NGN" and rnd["target_minor"] is None
        )
        capture("funding_pipeline", "round_auto_created", r)

        # 3. Set the raise. Money is minor units (kobo); the currency is the round's.
        r = c.put(
            f"{F}/round",
            headers=wh,
            json={"name": "Seed", "status": "open", "target_minor": 9_000_000_000},
        )
        assert r.status_code == 200 and r.json()["data"]["opened_at"]
        capture("funding_pipeline", "round_set", r)

        # 4. More investors.
        def add(name, firm, **kw):
            rr = c.post(f"{F}/investors", headers=wh, json={"name": name, "firm": firm, **kw})
            assert rr.status_code == 201, rr.text
            return rr.json()["data"]

        bola = add("Bola Adeyemi", "Kora Ventures", type="vc", committed_minor=3_000_000_000)
        chidi = add("Chidi Okafor", None, type="angel", committed_minor=2_000_000_000, warm=True)
        dami = add("Dami Lawal", "Sahara Syndicate", type="syndicate")

        # 5. Move cards. Passed needs a reason.
        r = c.post(f"{F}/investors/{tolu['id']}/stage", headers=wh, json={"stage": "outreach"})
        assert r.status_code == 200 and r.json()["data"]["record_in_finance"] is None
        capture("funding_pipeline", "stage_moved", r)
        r = c.post(f"{F}/investors/{dami['id']}/stage", headers=wh, json={"stage": "passed"})
        assert r.status_code == 422
        capture("funding_pipeline", "stage_passed_needs_reason", r)
        r = c.post(
            f"{F}/investors/{dami['id']}/stage",
            headers=wh,
            json={
                "stage": "passed",
                "passed_reason": "stage_mismatch",
                "note": "Too early for them",
            },
        )
        assert (
            r.status_code == 200
            and r.json()["data"]["investor"]["passed_reason"] == "stage_mismatch"
        )
        capture("funding_pipeline", "stage_passed", r)
        r = c.post(f"{F}/investors/{bola['id']}/stage", headers=wh, json={"stage": "term_sheet"})
        assert r.status_code == 200
        r = c.post(f"{F}/investors/{chidi['id']}/stage", headers=wh, json={"stage": "committed"})
        offer = r.json()["data"]["record_in_finance"]
        assert offer == {
            "amount_minor": 2_000_000_000,
            "currency": "NGN",
            "needs_ngn_amount": False,
            "fits_ledger": True,
        }
        capture("funding_pipeline", "stage_committed_with_offer", r)
        # Reorder inside a column: same stage plus a position, no history row.
        r = c.post(
            f"{F}/investors/{tolu['id']}/stage",
            headers=wh,
            json={"stage": "outreach", "position": 0},
        )
        assert r.status_code == 200
        capture("funding_pipeline", "stage_reorder_same_column", r)

        # 6. Nothing reached Finance: the offer is only an offer.
        r = c.get("/api/v1/finance/transactions?category=Funding", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["transactions"] == []
        capture("funding_pipeline", "finance_before_confirm", r)

        # 7. The board and the header bar.
        r = c.get(f"{F}/investors", headers=wh)
        board = r.json()["data"]
        assert [i["stage"] for i in board["items"]] == [
            "outreach",
            "term_sheet",
            "committed",
            "passed",
        ]
        capture("funding_pipeline", "investors_board", r)
        r = c.get(f"{F}/investors?q=lagos&warm=true", headers=wh)
        assert r.json()["data"]["total"] == 1
        capture("funding_pipeline", "investors_filtered", r)
        r = c.get(f"{F}/overview", headers=wh)
        t = r.json()["data"]["totals"]
        assert (t["soft_minor"], t["committed_minor"]) == (3_000_000_000, 2_000_000_000)
        assert (t["soft_pct"], t["committed_pct"]) == (33, 22)
        capture("funding_pipeline", "overview_with_totals", r)

        # 8. Edit a card (PATCH never moves the stage) and the interaction log.
        r = c.patch(
            f"{F}/investors/{tolu['id']}",
            headers=wh,
            json={"thesis_notes": "Backs fintech founders in Lagos and Accra.", "warm": False},
        )
        assert r.status_code == 200 and r.json()["data"]["warm"] is False
        capture("funding_pipeline", "investor_patched", r)
        r = c.post(
            f"{F}/investors/{tolu['id']}/events",
            headers=wh,
            json={
                "kind": "call",
                "body": "30 minute intro. Wants the cohort numbers.",
                "occurred_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        )
        assert r.status_code == 201
        capture("funding_pipeline", "event_created", r)
        r = c.get(f"{F}/investors/{tolu['id']}/events", headers=wh)
        assert r.json()["data"]["total"] == 3  # created, moved, call; the reorder is silent
        capture("funding_pipeline", "events_list", r)
        r = c.get(f"{F}/investors/{tolu['id']}", headers=wh)
        assert r.json()["data"]["last_touch_at"]
        capture("funding_pipeline", "investor_detail", r)

        # 9. The founder confirms the money arrived: one Funding inflow, once.
        r = c.post(
            f"{F}/investors/{chidi['id']}/record-funding",
            headers=wh,
            json={"received_on": today.isoformat()},
        )
        assert r.status_code == 201, r.text
        assert r.json()["data"]["transaction"]["category"] == "Funding"
        capture("funding_pipeline", "record_funding", r)
        r = c.post(f"{F}/investors/{chidi['id']}/record-funding", headers=wh, json={})
        assert r.status_code == 409
        capture("funding_pipeline", "record_funding_again", r)
        r = c.post(f"{F}/investors/{bola['id']}/record-funding", headers=wh, json={})
        assert r.status_code == 409
        capture("funding_pipeline", "record_funding_not_committed", r)
        r = c.get("/api/v1/finance/transactions?category=Funding", headers=wh)
        assert len(r.json()["data"]["transactions"]) == 1
        capture("funding_pipeline", "finance_after_confirm", r)

        # 10. Validation and guard rails.
        r = c.post(f"{F}/investors", headers=wh, json={"name": ""})
        assert r.status_code == 422
        capture("funding_pipeline", "error_validation", r)
        r = c.post(
            f"{F}/investors",
            headers=wh,
            json={"name": "X", "check_min_minor": 10, "check_max_minor": 5},
        )
        assert r.status_code == 422
        capture("funding_pipeline", "error_cheque_range", r)
        r = c.put(f"{F}/round", headers=wh, json={"currency": "USD"})
        assert r.status_code == 422
        capture("funding_pipeline", "error_currency_locked", r)
        r = c.get(f"{F}/investors/00000000-0000-0000-0000-000000000000", headers=wh)
        assert r.status_code == 404
        capture("funding_pipeline", "error_not_found", r)
        r = c.get(f"{F}/round")
        assert r.status_code in (401, 403)
        capture("funding_pipeline", "error_no_token", r)
        r = c.get(f"{F}/round", headers=auth)
        assert r.status_code == 422
        capture("funding_pipeline", "error_no_workspace_header", r)
        other = make_verified_user(c)
        oauth = _login(c, other)
        _onboard(c, oauth)
        owh = _wh(c, oauth)
        r = c.get(f"{F}/investors/{tolu['id']}", headers=owh)
        assert r.status_code == 404  # another workspace never sees it
        capture("funding_pipeline", "error_other_workspace_investor", r)
        r = c.get(f"{F}/investors", headers={**oauth, "X-Workspace-Id": wh["X-Workspace-Id"]})
        assert r.status_code == 403
        capture("funding_pipeline", "error_other_workspace_header", r)

        # 11. Delete a card; close the round; the closed round is history.
        r = c.delete(f"{F}/investors/{dami['id']}", headers=wh)
        assert r.status_code == 200
        capture("funding_pipeline", "investor_deleted", r)
        r = c.put(f"{F}/round", headers=wh, json={"status": "closed"})
        assert r.json()["data"]["closed_at"]
        capture("funding_pipeline", "round_closed", r)
        r = c.post(f"{F}/investors", headers=wh, json={"name": "Late"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "ROUND_CLOSED"
        capture("funding_pipeline", "error_round_closed", r)
        r = c.get(f"{F}/investors", headers=wh)
        assert r.json()["data"]["total"] == 3
        capture("funding_pipeline", "investors_closed_round_history", r)

        # 12. The next raise in USD. Finance is NGN, so recording the money needs the naira amount.
        r = c.put(
            f"{F}/round",
            headers=wh,
            json={"name": "Series A", "currency": "USD", "target_minor": 200_000_000},
        )
        assert r.status_code == 200 and r.json()["data"]["currency"] == "USD"
        capture("funding_pipeline", "round_usd_started", r)
        usd = add("Emeka Obi", "Atlas Capital", type="vc", committed_minor=50_000_000)
        r = c.post(f"{F}/investors/{usd['id']}/stage", headers=wh, json={"stage": "committed"})
        offer = r.json()["data"]["record_in_finance"]
        assert offer["needs_ngn_amount"] is True and offer["amount_minor"] is None
        capture("funding_pipeline", "usd_stage_committed_offer", r)
        r = c.post(f"{F}/investors/{usd['id']}/record-funding", headers=wh, json={})
        assert r.status_code == 422
        capture("funding_pipeline", "usd_record_needs_naira", r)
        r = c.post(
            f"{F}/investors/{usd['id']}/record-funding",
            headers=wh,
            json={"amount_minor": 75_000_000},
        )
        assert r.status_code == 201 and r.json()["data"]["transaction"]["currency"] == "NGN"
        capture("funding_pipeline", "usd_record_funding", r)
