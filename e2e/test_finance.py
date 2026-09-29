"""Live Finance Hub journey (Module 12, Slice 1).

`test_finance_cash_flow_journey`: a founder onboards, records a raise (inflow), a customer
payment (inflow), and three expenses (outflows -- two categorized, one left uncategorized),
lists the transactions, categorizes the uncategorized one via PATCH, then reads the
cash-flow summary (cash on hand / burn / revenue / runway / 6-month series).

Every response body is captured to `e2e/_captures/finance/*.json` -- those files are the
verbatim source for the finance FE integration guide. They must be REAL bodies from this
live run, complete and untrimmed.

Finance is founder/team_member/accountant RBAC (app/api/v1/endpoints/finance.py), so this
journey needs nothing but a freshly onboarded founder + workspace.

Dates are computed relative to today (UTC, matching the server's cash-flow clock) so the
journey stays valid whenever it runs: the burn window is "this month and the two before it",
the by_month series is the last 6 calendar months.
"""

from datetime import UTC, date, datetime

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_marketing.py)."""
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


def test_finance_cash_flow_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- finance needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Finance")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today = datetime.now(UTC).date()
        raise_date = _mid_month(today, 4)  # outside the 3-month burn window, inside by_month
        last_month = _mid_month(today, 1)

        # 1. Record a raise (inflow). Amounts are minor units (kobo): 100,000,000 = NGN 1,000,000.
        raise_created = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": raise_date.isoformat(),
                "description": "Pre-seed round",
                "category": "Fundraising",
                "amount_minor": 100_000_000,
                "currency": "NGN",
                "direction": "in",
            },
        )
        assert raise_created.status_code == 200, raise_created.text
        raise_body = raise_created.json()["data"]
        assert raise_body["direction"] == "in"
        assert raise_body["amount_minor"] == 100_000_000
        assert raise_body["category"] == "Fundraising"
        capture("finance", "transaction_created", raise_created)

        # 2. A customer payment last month (inflow, counts toward monthly_revenue).
        revenue_created = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": last_month.isoformat(),
                "description": "First customer invoice",
                "category": "Revenue",
                "amount_minor": 6_000_000,
                "currency": "NGN",
                "direction": "in",
            },
        )
        assert revenue_created.status_code == 200, revenue_created.text

        # 3. Expenses (outflows): payroll last month, cloud hosting today, and one
        # dated today with NO category (uncategorized).
        payroll_created = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": last_month.isoformat(),
                "description": "Team salaries",
                "category": "Payroll",
                "amount_minor": 20_000_000,
                "currency": "NGN",
                "direction": "out",
            },
        )
        assert payroll_created.status_code == 200, payroll_created.text

        hosting_created = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": today.isoformat(),
                "description": "Cloud hosting",
                "category": "Infrastructure",
                "amount_minor": 12_000_000,
                "currency": "NGN",
                "direction": "out",
            },
        )
        assert hosting_created.status_code == 200, hosting_created.text

        uncategorized_created = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": today.isoformat(),
                "description": "Bank transfer - unknown vendor",
                "amount_minor": 4_000_000,
                "currency": "NGN",
                "direction": "out",
            },
        )
        assert uncategorized_created.status_code == 200, uncategorized_created.text
        uncategorized_body = uncategorized_created.json()["data"]
        assert uncategorized_body["category"] is None
        uncategorized_id = uncategorized_body["id"]

        # 4. List transactions -- all five present, the uncategorized one has category null.
        listed = c.get("/api/v1/finance/transactions", headers=wh)
        assert listed.status_code == 200, listed.text
        rows = listed.json()["data"]["transactions"]
        assert len(rows) == 5
        assert any(r["id"] == uncategorized_id and r["category"] is None for r in rows)
        capture("finance", "transactions_list", listed)

        # 4b. The uncategorized filter returns just that one.
        only_uncat = c.get(
            "/api/v1/finance/transactions", headers=wh, params={"uncategorized": "true"}
        )
        assert only_uncat.status_code == 200, only_uncat.text
        uncat_rows = only_uncat.json()["data"]["transactions"]
        assert [r["id"] for r in uncat_rows] == [uncategorized_id]
        capture("finance", "transactions_list_uncategorized", only_uncat)

        # 5. Categorize it inline via PATCH.
        categorized = c.patch(
            f"/api/v1/finance/transactions/{uncategorized_id}",
            headers=wh,
            json={"category": "Operations"},
        )
        assert categorized.status_code == 200, categorized.text
        assert categorized.json()["data"]["category"] == "Operations"
        capture("finance", "transaction_categorized", categorized)

        # 6. Cash-flow summary.
        # cash_on_hand = 100M + 6M - (20M + 12M + 4M)               = 70,000,000
        # burn window (this month + 2 prior): in 6M, out 36M -> net out 30M -> burn 10M/mo
        # monthly_revenue = 6M / 3 = 2M ; runway = 70M / 10M = 7.0 (not < 6, so not low)
        cash_flow = c.get("/api/v1/finance/cash-flow", headers=wh)
        assert cash_flow.status_code == 200, cash_flow.text
        cf = cash_flow.json()["data"]
        assert cf["cash_on_hand"] == 70_000_000
        assert cf["monthly_burn"] == 10_000_000
        assert cf["monthly_revenue"] == 2_000_000
        assert cf["runway_months"] == 7.0
        assert cf["runway_low"] is False
        assert cf["currency"] == "NGN"
        assert len(cf["by_month"]) == 6
        by_month = {row["month"]: row for row in cf["by_month"]}
        assert by_month[raise_date.strftime("%Y-%m")]["inflow"] == 100_000_000
        assert by_month[today.strftime("%Y-%m")]["outflow"] == 16_000_000
        capture("finance", "cash_flow", cash_flow)
