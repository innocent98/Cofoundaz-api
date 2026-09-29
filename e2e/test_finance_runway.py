"""Live Finance Hub runway journey (Module 12, Slice 2 -- Runway & Scenarios).

`test_finance_runway_journey`: a founder onboards and seeds a raise, a customer payment, and two
expenses, then:

  1. reads `GET /finance/runway` with the default (all-zero) assumptions -- assumptions + baseline
     + three scenarios (base/best/worst) over a 12-month horizon in ONE call;
  2. saves assumptions via `PUT /finance/runway/assumptions` (growth / hiring / one-off), sees the
     response AND a follow-up GET reflect them, and that the scenarios actually moved;
  3. exercises the validation error shapes (negative, explicit null) and the auth boundary;
  4. drives the startup INTO low runway with a large outflow and asserts a `finance.runway.low`
     in-app notification appears (fire-once: a further outflow while still low does NOT add a
     second one), then that the informational `money.runway_live` health signal tracks it;
  5. recovers (large inflow -> cash-positive: runway null, alert re-armed), then goes out of cash
     (runway 0.0) and asserts the alert fires AGAIN (re-arm proven live).

Every response body is captured to `e2e/_captures/finance_runway/*.json` -- those files are the
verbatim source for docs/fe-integration-guide-finance-runway.md. They must be REAL bodies from
this live run, complete and untrimmed.

Amounts are minor units (kobo). The trailing burn window is "this month and the two before it";
all dates are computed relative to today (UTC, matching the server's clock) so the journey stays
valid whenever it runs.
"""

from datetime import UTC, date, datetime

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_finance.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def _mid_month(today: date, months_back: int) -> date:
    """The 15th of the month `months_back` months before `today`'s month (always in the past)."""
    y, m = today.year, today.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15)


def _post_txn(c, wh, *, day, description, category, amount, direction):
    resp = c.post(
        "/api/v1/finance/transactions",
        headers=wh,
        json={
            "date": day.isoformat(),
            "description": description,
            "category": category,
            "amount_minor": amount,
            "currency": "NGN",
            "direction": direction,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp


def _runway_low_notifications(c, wh) -> list[dict]:
    feed = c.get("/api/v1/notifications", headers=wh)
    assert feed.status_code == 200, feed.text
    return [n for n in feed.json()["data"]["notifications"] if n["type"] == "finance.runway.low"]


def _runway_signals(c, wh) -> list[dict]:
    dim = c.get("/api/v1/health-score/dimensions/money", headers=wh)
    assert dim.status_code == 200, dim.text
    return [s for s in dim.json()["data"]["signals"] if s["key"] == "money.runway_live"]


def test_finance_runway_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- finance needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Runway")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today = datetime.now(UTC).date()
        raise_date = _mid_month(today, 4)  # before the 3-month burn window, counts to cash only
        last_month = _mid_month(today, 1)  # inside the burn window

        # 1. Seed. cash_on_hand = 100M + 9M - (21M + 9M) = 79,000,000 (NGN 790,000)
        #    burn window: in 9M, out 30M -> net out 21M -> monthly_burn 7M, monthly_revenue 3M,
        #    monthly costs 10M -> runway 79M / 7M = 11.3 months (NOT low: >= 6).
        _post_txn(
            c,
            wh,
            day=raise_date,
            description="Pre-seed round",
            category="Fundraising",
            amount=100_000_000,
            direction="in",
        )
        _post_txn(
            c,
            wh,
            day=last_month,
            description="First customer invoice",
            category="Revenue",
            amount=9_000_000,
            direction="in",
        )
        _post_txn(
            c,
            wh,
            day=last_month,
            description="Team salaries",
            category="Payroll",
            amount=21_000_000,
            direction="out",
        )
        _post_txn(
            c,
            wh,
            day=today,
            description="Cloud hosting",
            category="Infrastructure",
            amount=9_000_000,
            direction="out",
        )

        # A healthy runway must not have raised the alert or created the live signal's danger.
        assert _runway_low_notifications(c, wh) == []

        # 2. GET /finance/runway with default assumptions -- everything in one call.
        first = c.get("/api/v1/finance/runway", headers=wh)
        assert first.status_code == 200, first.text
        capture("finance_runway", "runway_default", first)
        d1 = first.json()["data"]
        assert d1["assumptions"] == {
            "mom_growth_percent": 0,
            "hiring_spend_minor": 0,
            "one_off_costs_minor": 0,
        }
        assert d1["horizon_months"] == 12
        assert d1["baseline"] == {
            "cash_on_hand": 79_000_000,
            "monthly_burn": 7_000_000,
            "monthly_revenue": 3_000_000,
            "currency": "NGN",
        }
        assert set(d1["scenarios"]) == {"base", "best", "worst"}
        for scen in d1["scenarios"].values():
            assert set(scen) == {"runway_months", "cash_out_date", "avg_net_burn_minor", "by_month"}
            assert len(scen["by_month"]) == 12
            assert set(scen["by_month"][0]) == {"month", "cash_balance", "net"}
        base1, best1, worst1 = (d1["scenarios"][k] for k in ("base", "best", "worst"))
        # base: 79M / 7M-per-month burn crosses zero inside the horizon.
        assert base1["runway_months"] is not None and 0 < base1["runway_months"] < 12
        assert base1["cash_out_date"] is not None
        # worst burns faster than base; best (growth + cheaper costs) never runs out -> null.
        assert worst1["runway_months"] < base1["runway_months"]
        assert best1["runway_months"] is None and best1["cash_out_date"] is None

        # 3. PUT assumptions. hiring 2,000,000 minor = NGN 20,000 ("20" thousand in the FE),
        #    one-off 5,000,000 minor = NGN 50,000 ("50" thousand in the FE).
        put = c.put(
            "/api/v1/finance/runway/assumptions",
            headers=wh,
            json={
                "mom_growth_percent": 5,
                "hiring_spend_minor": 2_000_000,
                "one_off_costs_minor": 5_000_000,
            },
        )
        assert put.status_code == 200, put.text
        capture("finance_runway", "assumptions_put", put)
        assert put.json()["data"]["assumptions"] == {
            "mom_growth_percent": 5,
            "hiring_spend_minor": 2_000_000,
            "one_off_costs_minor": 5_000_000,
        }

        # 3b. A follow-up GET reflects the saved assumptions and the scenarios moved.
        after = c.get("/api/v1/finance/runway", headers=wh)
        assert after.status_code == 200, after.text
        capture("finance_runway", "runway_after_assumptions", after)
        d2 = after.json()["data"]
        assert d2["assumptions"] == put.json()["data"]["assumptions"]
        assert d2["baseline"] == d1["baseline"]  # assumptions never change the actuals
        assert d2["scenarios"] != d1["scenarios"]
        assert d2["scenarios"]["base"]["runway_months"] != base1["runway_months"]

        # 3c. Partial update: only the fields sent change.
        partial = c.put(
            "/api/v1/finance/runway/assumptions", headers=wh, json={"mom_growth_percent": 8}
        )
        assert partial.status_code == 200, partial.text
        capture("finance_runway", "assumptions_put_partial", partial)
        assert partial.json()["data"]["assumptions"] == {
            "mom_growth_percent": 8,
            "hiring_spend_minor": 2_000_000,
            "one_off_costs_minor": 5_000_000,
        }

        # 4. Error shapes + the auth boundary.
        negative = c.put(
            "/api/v1/finance/runway/assumptions", headers=wh, json={"hiring_spend_minor": -1}
        )
        assert negative.status_code == 422, negative.text
        capture("finance_runway", "assumptions_put_negative_422", negative)

        explicit_null = c.put(
            "/api/v1/finance/runway/assumptions", headers=wh, json={"mom_growth_percent": None}
        )
        assert explicit_null.status_code == 422, explicit_null.text
        capture("finance_runway", "assumptions_put_null_422", explicit_null)

        over_range = c.put(
            "/api/v1/finance/runway/assumptions", headers=wh, json={"mom_growth_percent": 1001}
        )
        assert over_range.status_code == 422, over_range.text
        capture("finance_runway", "assumptions_put_growth_over_1000_422", over_range)

        unauth = c.get("/api/v1/finance/runway")
        assert unauth.status_code == 401, unauth.text
        capture("finance_runway", "runway_unauthenticated_401", unauth)

        # The rejected writes changed nothing.
        unchanged = c.get("/api/v1/finance/runway", headers=wh).json()["data"]["assumptions"]
        assert unchanged == partial.json()["data"]["assumptions"]

        # 5. Drive INTO low runway: a NGN 500,000 outflow today.
        #    cash 79M - 50M = 29M ; window out 80M, in 9M -> net out 71M -> burn 23,666,667
        #    -> runway 1.2 months (< 6) -> low -> `finance.runway.low` fires.
        low_txn = _post_txn(
            c,
            wh,
            day=today,
            description="Office build-out",
            category="Capex",
            amount=50_000_000,
            direction="out",
        )
        capture("finance_runway", "transaction_pushing_low", low_txn)

        cf_low = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cf_low.status_code == 200, cf_low.text
        assert cf_low.json()["data"]["runway_low"] is True
        capture("finance_runway", "cash_flow_low", cf_low)

        feed_low = c.get("/api/v1/notifications", headers=wh)
        assert feed_low.status_code == 200, feed_low.text
        capture("finance_runway", "notifications_after_low", feed_low)
        fired = _runway_low_notifications(c, wh)
        assert len(fired) == 1, fired
        assert fired[0]["title"] == "Your runway is running low"
        assert fired[0]["read"] is False
        assert fired[0]["data"]["currency"] == "NGN"

        unread = c.get("/api/v1/notifications/unread-count", headers=wh)
        assert unread.status_code == 200, unread.text
        capture("finance_runway", "notifications_unread_count_after_low", unread)

        runway_low = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_low.status_code == 200, runway_low.text
        capture("finance_runway", "runway_low", runway_low)
        assert runway_low.json()["data"]["scenarios"]["base"]["runway_months"] is not None

        # 5b. The informational health signal tracks live runway (value = months, 1.2).
        dim_low = c.get("/api/v1/health-score/dimensions/money", headers=wh)
        assert dim_low.status_code == 200, dim_low.text
        capture("finance_runway", "health_dimension_money_low", dim_low)
        sig = _runway_signals(c, wh)
        assert len(sig) == 1, sig
        assert sig[0]["value"] == 1.2
        assert sig[0]["contribution"] == 0.0
        assert sig[0]["source_ref"] == "finance:cash-flow"

        # 5c. FIRE-ONCE: another outflow while still low must NOT add a second notification.
        _post_txn(
            c,
            wh,
            day=today,
            description="Legal fees",
            category="Legal",
            amount=1_000_000,
            direction="out",
        )
        assert len(_runway_low_notifications(c, wh)) == 1

        # 6. Recover: a NGN 3,000,000 inflow today -> cash-positive (burn 0).
        #    Runway is null everywhere, the alert re-arms, and the live signal reads 0.
        recovery = _post_txn(
            c,
            wh,
            day=today,
            description="Series A tranche",
            category="Fundraising",
            amount=300_000_000,
            direction="in",
        )
        capture("finance_runway", "transaction_recovery", recovery)

        runway_positive = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_positive.status_code == 200, runway_positive.text
        capture("finance_runway", "runway_cash_positive", runway_positive)
        pos = runway_positive.json()["data"]
        assert pos["baseline"]["monthly_burn"] == 0
        for scen in pos["scenarios"].values():
            assert scen["runway_months"] is None
            assert scen["cash_out_date"] is None

        dim_pos = c.get("/api/v1/health-score/dimensions/money", headers=wh)
        assert dim_pos.status_code == 200, dim_pos.text
        capture("finance_runway", "health_dimension_money_cash_positive", dim_pos)
        sig = _runway_signals(c, wh)
        assert len(sig) == 1 and sig[0]["value"] == 0.0, sig

        # 7. Out of cash: a NGN 4,000,000 outflow -> cash below zero, burning -> low again.
        #    The alert was re-armed by the recovery, so it fires a SECOND time.
        broke = _post_txn(
            c,
            wh,
            day=today,
            description="Data-centre migration",
            category="Infrastructure",
            amount=400_000_000,
            direction="out",
        )
        capture("finance_runway", "transaction_out_of_cash", broke)

        runway_broke = c.get("/api/v1/finance/runway", headers=wh)
        assert runway_broke.status_code == 200, runway_broke.text
        capture("finance_runway", "runway_out_of_cash", runway_broke)
        broke_data = runway_broke.json()["data"]
        assert broke_data["baseline"]["cash_on_hand"] < 0
        for scen in broke_data["scenarios"].values():
            assert scen["runway_months"] == 0.0

        feed_second = c.get("/api/v1/notifications", headers=wh)
        assert feed_second.status_code == 200, feed_second.text
        capture("finance_runway", "notifications_after_rearm", feed_second)
        assert len(_runway_low_notifications(c, wh)) == 2

        dim_broke = c.get("/api/v1/health-score/dimensions/money", headers=wh)
        assert dim_broke.status_code == 200, dim_broke.text
        capture("finance_runway", "health_dimension_money_out_of_cash", dim_broke)
        sig = _runway_signals(c, wh)
        assert len(sig) == 1 and sig[0]["value"] == 0.0, sig
