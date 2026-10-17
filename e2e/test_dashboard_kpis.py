"""Live Dashboard KPI tiles: `GET /dashboard/summary` -> `kpis.metrics`.

A founder onboards and, while onboarding is still a draft, invites an investor and an accountant
(`onboarding/invites` 409s once `/complete` has run). The founder's workspace is then walked
through every KPI state, capturing the summary after each step:

  empty          -> revenue/runway/campaign_ctr `no_data`, pipeline `not_built`
  burning        -> revenue `ok`, runway `ok` (27.0 mo), campaign_ctr `ok` (2.5%)
  out_of_cash    -> one huge outflow drives cash on hand negative
  not_burning    -> one huge inflow makes the trailing window profitable

then reads the same summary as the investor (every tile `restricted`, no money at all) and as
the accountant (finance tiles real, campaign_ctr `restricted`, mirroring the module gates).

Every response body is captured to `e2e/_captures/dashboard_kpis/*.json`, the verbatim source
for `docs/fe-integration-guide-dashboard-kpis.md`. All money is in minor units (kobo).
"""

from datetime import UTC, datetime, timedelta

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _signup_verify_login(c: httpx.Client, mailbox, email: str, password: str) -> dict:
    c.post("/api/v1/auth/signup", json={"email": email, "password": password})
    token = mailbox.latest_token_for(email, subject_contains="Verify")
    c.post("/api/v1/auth/verify", json={"token": token})
    access = c.post("/api/v1/auth/login", json={"email": email, "password": password}).json()[
        "data"
    ]["access_token"]
    return _auth_header(access)


def _wh(c: httpx.Client, auth: dict) -> dict:
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _txn(c: httpx.Client, wh: dict, direction: str, amount: int, *, days_ago: int = 0) -> None:
    day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
    r = c.post(
        "/api/v1/finance/transactions",
        headers=wh,
        json={
            "date": day,
            "description": f"e2e {direction}",
            "amount_minor": amount,
            "currency": "NGN",
            "direction": direction,
        },
    )
    assert r.status_code == 200, r.text


def _metrics(c: httpx.Client, wh: dict, resp_name: str, capture) -> dict:
    r = c.get("/api/v1/dashboard/summary", headers=wh)
    assert r.status_code == 200, r.text
    capture("dashboard_kpis", resp_name, r)
    return r.json()["data"]["kpis"]["metrics"]


def test_dashboard_kpis_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        auth = _auth_header(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])

        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Revenue"]})

        investor_email = unique_email("investor")
        accountant_email = unique_email("accountant")
        inv = c.post(
            "/api/v1/onboarding/invites",
            headers=auth,
            json={
                "invites": [
                    {"email": investor_email, "role": "investor"},
                    {"email": accountant_email, "role": "accountant"},
                ]
            },
        )
        assert inv.status_code == 200, inv.text
        role_auth = {}
        for role, email in (("investor", investor_email), ("accountant", accountant_email)):
            token = mailbox.latest_token_for(email, subject_contains="invited")
            ra = _signup_verify_login(c, mailbox, email, f"Kpi-{role}-{email[:6]}-9")
            acc = c.post("/api/v1/invitations/accept", json={"token": token}, headers=ra)
            assert acc.status_code == 200, acc.text
            role_auth[role] = ra

        done = c.post("/api/v1/onboarding/complete", headers=auth)
        assert done.status_code == 200, done.text
        wh = _wh(c, auth)

        # 1. Empty workspace: honest no_data, never a fabricated 0.
        m = _metrics(c, wh, "summary_empty", capture)
        assert m["revenue"]["status"] == "no_data"
        assert m["runway"]["status"] == "no_data"
        assert m["campaign_ctr"]["status"] == "no_data"
        assert m["pipeline"]["status"] == "not_built"

        # 2. Burning: an old raise, this month's revenue and costs, plus campaign metrics.
        _txn(c, wh, "in", 60_000_000, days_ago=200)
        _txn(c, wh, "in", 3_000_000)
        _txn(c, wh, "out", 9_000_000)
        today = datetime.now(UTC).date().isoformat()
        ing = c.post(
            "/api/v1/marketing/metrics",
            headers=wh,
            json={
                "points": [
                    {"ts": today, "metric": "impressions", "value": 2000},
                    {"ts": today, "metric": "clicks", "value": 50},
                ]
            },
        )
        assert ing.status_code == 200, ing.text
        m = _metrics(c, wh, "summary_burning", capture)
        assert m["revenue"] == {
            "status": "ok",
            "value_minor": 1_000_000,
            "currency": "NGN",
            "basis": "trailing_3mo_avg",
        }
        assert m["runway"]["status"] == "ok"
        assert m["runway"]["months"] == 27.0
        assert m["runway"]["is_low"] is False
        assert m["campaign_ctr"]["status"] == "ok"
        assert m["campaign_ctr"]["ctr_percent"] == 2.5

        # 3. Investor and accountant read the same workspace (still burning).
        mi = _metrics(c, _wh(c, role_auth["investor"]), "summary_investor", capture)
        assert {k: v["status"] for k, v in mi.items()} == {
            "revenue": "restricted",
            "runway": "restricted",
            "pipeline": "not_built",
            "campaign_ctr": "restricted",
        }
        assert mi["runway"]["cash_on_hand_minor"] is None
        ma = _metrics(c, _wh(c, role_auth["accountant"]), "summary_accountant", capture)
        assert ma["revenue"]["status"] == "ok"
        assert ma["runway"]["status"] == "ok"
        assert ma["campaign_ctr"]["status"] == "restricted"

        # 4. Out of cash: burning with cash on hand below zero.
        _txn(c, wh, "out", 100_000_000)
        m = _metrics(c, wh, "summary_out_of_cash", capture)
        assert m["runway"]["status"] == "out_of_cash"
        assert m["runway"]["months"] == 0
        assert m["runway"]["is_low"] is True

        # 5. Not burning: the trailing window turns profitable.
        _txn(c, wh, "in", 200_000_000)
        m = _metrics(c, wh, "summary_not_burning", capture)
        assert m["runway"]["status"] == "not_burning"
        assert m["runway"]["months"] is None
        assert m["runway"]["monthly_burn_minor"] == 0
