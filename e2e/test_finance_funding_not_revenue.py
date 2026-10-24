"""Live journey for D-FIN-11: a Funding inflow is cash, never revenue, and never offsets burn.

Walk: founder onboards -> records a raise (category Funding), customer payments and costs, all
this month -> `GET /finance/cash-flow`, `GET /finance/runway`, `GET /dashboard/summary` (KPI tiles)
-> `POST /finance/model/generate`, drain the worker, `GET /finance/model/{id}`.

Before the fix the raise counted as a third of a month's revenue each month and cancelled the
burn, so runway read "not burning" right after a raise. Every response body is captured to
`e2e/_captures/finance_funding/*.json` (the verbatim source for
docs/fe-integration-guide-finance-funding.md).
"""

import uuid
from datetime import UTC, datetime

import httpx

G = "finance_funding"
API = "/api/v1"
RAISE, SALES, COSTS = 30_000_000, 1_500_000, 4_500_000


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import finance_model as _m  # noqa: F401

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _row(stmt: dict, label: str) -> list[int]:
    (vals,) = [r["values"] for r in stmt["rows"] if r["label"] == label]
    return vals


def test_funding_is_cash_not_revenue(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post(f"{API}/auth/login", json=u).json()["data"]["access_token"])
        c.get(f"{API}/onboarding/state", headers=auth)
        steps = [
            {"step": 1, "full_name": "Ada Founder"},
            {"step": 2, "name": f"Funded Co {uuid.uuid4().hex[:6]}"},
            {"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
            {"step": 4, "goals": ["Get first customers"]},
        ]
        for body in steps:
            c.patch(f"{API}/onboarding/state", headers=auth, json=body)
        assert c.post(f"{API}/onboarding/complete", headers=auth).status_code == 200
        ws = c.get(f"{API}/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        today = datetime.now(UTC).date().isoformat()
        for desc, cat, amt, d in [
            ("Seed round", "Funding", RAISE, "in"),
            ("Customer payments", "Revenue", SALES, "in"),
            ("Team salaries", "Payroll", COSTS, "out"),
        ]:
            r = c.post(
                f"{API}/finance/transactions",
                headers=wh,
                json={
                    "date": today,
                    "description": desc,
                    "category": cat,
                    "amount_minor": amt,
                    "currency": "NGN",
                    "direction": d,
                },
            )
            assert r.status_code == 200, r.text

        cash = c.get(f"{API}/finance/cash-flow", headers=wh)
        capture(G, "cash_flow", cash)
        d = cash.json()["data"]
        assert d["cash_on_hand"] == RAISE + SALES - COSTS
        assert d["monthly_revenue"] == SALES // 3  # the raise is not revenue
        assert d["monthly_burn"] == (COSTS - SALES) // 3  # nor does it cancel the burn
        assert d["runway_months"] == round((RAISE + SALES - COSTS) / ((COSTS - SALES) / 3), 1)
        assert d["by_month"][-1]["inflow"] == RAISE + SALES  # still money in on the chart

        runway = c.get(f"{API}/finance/runway", headers=wh)
        capture(G, "runway", runway)
        baseline = runway.json()["data"]["baseline"]
        assert baseline["monthly_revenue"] == SALES // 3
        assert baseline["monthly_burn"] == (COSTS - SALES) // 3

        summary = c.get(f"{API}/dashboard/summary", headers=wh)
        capture(G, "dashboard_summary", summary)
        metrics = summary.json()["data"]["kpis"]["metrics"]
        assert metrics["revenue"]["value_minor"] == SALES // 3
        assert metrics["runway"]["status"] == "ok"

        gen = c.post(f"{API}/finance/model/generate", headers=wh)
        assert gen.status_code == 202, gen.text
        _drain()
        got = c.get(f"{API}/finance/model/{gen.json()['data']['id']}", headers=wh)
        capture(G, "model_complete", got)
        model = got.json()["data"]
        assert model["status"] == "complete"
        assert _row(model["balance_sheet"], "Paid-in capital")[0] == RAISE
        assert model["balance_check"]["balanced"] is True
