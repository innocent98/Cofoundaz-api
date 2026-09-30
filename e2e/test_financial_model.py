"""Live Financial Model journey (Module 12, Slice 5).

`test_financial_model_journey`: a founder onboards, records a raise, a customer payment
and some expenses (so the model is seeded from non-zero actuals), reads `GET /finance/model`
before any model exists (`status: "none"`), kicks off `POST /finance/model/generate`
(-> 202 `generating`), polls once while still `generating`, drains the in-process worker
(LLM_PROVIDER=stub, set by scripts/e2e_run.sh) so `ai.finance.model`
(app/worker/handlers/finance_model.py) runs and flips the row to `complete`, then reads the
finished model (latest + by id) and asserts the three statements and that the captured
balance sheet balances every month (Cash + Accounts receivable == Accounts payable +
Retained earnings + Paid-in capital).

Every response body is captured to `e2e/_captures/financial_model/*.json` -- those files are
the verbatim source for `docs/fe-integration-guide-finance-model.md`.

Finance is founder/team_member/accountant RBAC (app/api/v1/endpoints/finance.py).
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


def _drain() -> None:
    """Drains the in-process job queue (same pattern as e2e/test_marketing.py)."""
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import (
        finance_model as _finance_model,  # noqa: F401  (registers ai.finance.model)
    )

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _row(statement: dict, label: str) -> list[int]:
    matches = [r["values"] for r in statement["rows"] if r["label"] == label]
    assert len(matches) == 1, f"expected exactly one {label!r} row, got {len(matches)}"
    return matches[0]


def test_financial_model_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        # 0. Onboard a founder -- the model needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Model")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today_date = datetime.now(UTC).date()
        today = today_date.isoformat()
        # The raise is dated outside the trailing 3-month window: every inflow in that window
        # counts as revenue, so a fresh raise would otherwise be projected as recurring revenue.
        raise_date = _mid_month(today_date, 4).isoformat()

        # 1. Seed actuals so the model is not all-zero. Amounts are minor units (kobo).
        seeds = [
            (raise_date, "Pre-seed round", "Fundraising", 100_000_000, "in"),
            (today, "Customer payments", "Revenue", 6_000_000, "in"),
            (today, "Team salaries", "Payroll", 20_000_000, "out"),
            (today, "Cloud hosting", "Infrastructure", 12_000_000, "out"),
        ]
        for seed_date, description, category, amount, direction in seeds:
            seeded = c.post(
                "/api/v1/finance/transactions",
                headers=wh,
                json={
                    "date": seed_date,
                    "description": description,
                    "category": category,
                    "amount_minor": amount,
                    "currency": "NGN",
                    "direction": direction,
                },
            )
            assert seeded.status_code == 200, seeded.text

        # 2. No model yet -- the latest endpoint answers 200 {status: "none"}.
        before = c.get("/api/v1/finance/model", headers=wh)
        assert before.status_code == 200, before.text
        assert before.json()["data"] == {"status": "none"}
        capture("financial_model", "latest_none", before)

        # 3. Kick off generation -- 202 Accepted, {id, status: "generating"}.
        accepted = c.post("/api/v1/finance/model/generate", headers=wh)
        assert accepted.status_code == 202, accepted.text
        accepted_body = accepted.json()["data"]
        assert accepted_body["status"] == "generating"
        capture("financial_model", "generate_accepted", accepted)
        model_id = accepted_body["id"]

        # 4. Poll before the worker runs -- still generating, no statements yet.
        generating = c.get("/api/v1/finance/model", headers=wh)
        assert generating.status_code == 200, generating.text
        gen_body = generating.json()["data"]
        assert gen_body["id"] == model_id
        assert gen_body["status"] == "generating"
        assert gen_body["pnl"] is None
        capture("financial_model", "latest_generating", generating)

        # 5. Drain -> handle_finance_model runs (stub LLM) and flips status to complete.
        _drain()

        # 6. Poll the latest model -- now complete with all three statements.
        latest = c.get("/api/v1/finance/model", headers=wh)
        assert latest.status_code == 200, latest.text
        model = latest.json()["data"]
        assert model["id"] == model_id
        assert model["status"] == "complete", model
        assert model["error"] is None
        assert model["horizon_months"] == 12
        assert len(model["months"]) == 12
        assert model["assumptions"] is not None
        capture("financial_model", "latest_complete", latest)

        for key in ("pnl", "cash_flow", "balance_sheet"):
            stmt = model[key]
            assert stmt is not None, key
            # `months` is top-level only; each statement is just {rows}.
            assert list(stmt.keys()) == ["rows"], key
            assert stmt["rows"], key
            for row in stmt["rows"]:
                assert len(row["values"]) == 12
                assert all(isinstance(v, int) for v in row["values"])

        # 7. THE invariant, read from the captured balance sheet: every month,
        #    Cash + AR == AP + Retained earnings + Paid-in capital.
        bs = model["balance_sheet"]
        cash = _row(bs, "Cash")
        ar = _row(bs, "Accounts receivable")
        ap = _row(bs, "Accounts payable")
        retained = _row(bs, "Retained earnings")
        paid_in = _row(bs, "Paid-in capital")
        for i in range(12):
            assert cash[i] + ar[i] == ap[i] + retained[i] + paid_in[i], (
                f"balance sheet does not balance in month {model['months'][i]}: "
                f"{cash[i]} + {ar[i]} != {ap[i]} + {retained[i]} + {paid_in[i]}"
            )

        # Cash flow ties to the balance sheet: closing cash == balance-sheet cash.
        assert _row(model["cash_flow"], "Closing cash") == cash

        # 8. Fetch the same model by id.
        by_id = c.get(f"/api/v1/finance/model/{model_id}", headers=wh)
        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["data"] == model
        capture("financial_model", "model_by_id", by_id)
