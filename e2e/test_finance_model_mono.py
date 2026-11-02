"""Live journey: editable 36-month model with a checked balance sheet, and the Mono bank link.

Server runs with PAYMENT_PROVIDER_STUB=true and FAKE Mono values (scripts/e2e_run.sh), so the
Mono HTTP calls are answered by the deterministic stub in
`app/services/finance/payments/stub_transport.py`. Exercised live: our routes, RBAC, encryption
at rest, import, idempotency, the Paystack-payout tagging (D-FIN-10), the webhook secret check,
the model edit/recalculate cycle. NOT exercised: the real Mono sandbox (needs the real keys; the
lead's FE walk does that), see the verification table in
docs/fe-integration-guide-finance-model-mono.md.

Every response body is captured, untrimmed, to `e2e/_captures/finance_model_mono/*.json`.
"""

import json
import os
import re
import uuid
from datetime import UTC, date, datetime

import httpx

G = "finance_model_mono"
API = "/api/v1"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mid_month(today: date, months_back: int) -> date:
    y, m = today.year, today.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15)


def _join(c, wh, ws, role, unique_email, mailbox):
    email = unique_email(role)
    r = c.post(
        f"{API}/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Member-{uuid.uuid4().hex[:10]}-9"
    c.post(f"{API}/auth/signup", json={"email": email, "password": pw})
    c.post(
        f"{API}/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post(f"{API}/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    acc = c.post(f"{API}/invitations/accept", headers=_auth(access), json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**_auth(access), "X-Workspace-Id": ws}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import finance_model as _m  # noqa: F401
    from app.worker.handlers import finance_payments as _p  # noqa: F401

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


def test_finance_model_and_mono_journey(
    base_url, make_verified_user, capture_json, mailbox, unique_email
):
    def capture(group: str, name: str, resp: httpx.Response) -> None:
        # Webhook URLs carry an unguessable per-connection token: never commit one.
        text = re.sub(
            r"(/payments/(?:paystack|flutterwave)/)[A-Za-z0-9_-]{20,}",
            r"\1<public_id_placeholder>",
            json.dumps(resp.json()),
        )
        capture_json(group, name, json.loads(text))

    secret = os.environ["MONO_WEBHOOK_SECRET"]
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post(f"{API}/auth/login", json=u).json()["data"]["access_token"])
        c.get(f"{API}/onboarding/state", headers=auth)
        c.patch(
            f"{API}/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"}
        )
        c.patch(f"{API}/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay Model"})
        c.patch(
            f"{API}/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch(
            f"{API}/onboarding/state",
            headers=auth,
            json={"step": 4, "goals": ["Get first customers"]},
        )
        assert c.post(f"{API}/onboarding/complete", headers=auth).status_code == 200
        ws = c.get(f"{API}/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        tm = _join(c, wh, ws, "team_member", unique_email, mailbox)
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)

        today = datetime.now(UTC).date()
        for when, desc, cat, amt, d in [
            (_mid_month(today, 4).isoformat(), "Pre-seed round", "Funding", 100_000_000, "in"),
            (today.isoformat(), "Customer payments", "Revenue", 6_000_000, "in"),
            (today.isoformat(), "Team salaries", "Payroll", 20_000_000, "out"),
            (today.isoformat(), "Cloud hosting", "Infrastructure", 12_000_000, "out"),
        ]:
            r = c.post(
                f"{API}/finance/transactions",
                headers=wh,
                json={
                    "date": when,
                    "description": desc,
                    "category": cat,
                    "amount_minor": amt,
                    "currency": "NGN",
                    "direction": d,
                },
            )
            assert r.status_code == 200, r.text

        # ---- 1. Generate (the only AI step), then read the 36-month model ----------------------
        gen = c.post(f"{API}/finance/model/generate", headers=wh)
        assert gen.status_code == 202, gen.text
        model_id = gen.json()["data"]["id"]
        _drain()
        got = c.get(f"{API}/finance/model/{model_id}", headers=wh)
        capture(G, "model_complete", got)
        model = got.json()["data"]
        assert model["status"] == "complete" and model["horizon_months"] == 36
        assert model["balance_check"] == {"balanced": True, "max_abs_difference_minor": 0}
        assert _row(model["balance_sheet"], "Paid-in capital")[0] == 100_000_000
        assert [p["label"] for p in model["yearly"]["periods"]] == ["Year 1", "Year 2", "Year 3"]
        assert model["stale"] is False and model["overrides"] is None
        months = model["months"]

        # ---- 2. Edit assumptions: validation first, then a real edit ---------------------------
        bad = c.patch(
            f"{API}/finance/model/{model_id}/assumptions",
            headers=wh,
            json={"monthly_revenue_growth_pct": 500, "ar_days": -1},
        )
        assert bad.status_code == 422, bad.text
        capture(G, "assumptions_invalid", bad)
        late = c.patch(
            f"{API}/finance/model/{model_id}/assumptions",
            headers=wh,
            json={"capex": [{"label": "Servers", "month": "2099-01", "amount_minor": 1}]},
        )
        assert (
            late.status_code == 422
            and late.json()["error"]["field_errors"][0]["field"] == "capex[0].month"
        )
        capture(G, "assumptions_month_outside", late)

        edit = {
            "monthly_revenue_growth_pct": 6,
            "cogs_pct_of_revenue": 25,
            "ar_days": 30,
            "ap_days": 20,
            "hiring_plan": [
                {
                    "role": "Backend engineer",
                    "start_month": months[2],
                    "monthly_cost_minor": 1_500_000,
                }
            ],
            "one_offs": [{"label": "Legal setup", "month": months[1], "amount_minor": 2_000_000}],
            "capex": [{"label": "Laptops", "month": months[2], "amount_minor": 4_800_000}],
            "depreciation_months": 24,
        }
        saved = c.patch(f"{API}/finance/model/{model_id}/assumptions", headers=wh, json=edit)
        assert saved.status_code == 200, saved.text
        capture(G, "assumptions_saved_stale", saved)
        assert saved.json()["data"]["stale"] is True
        assert saved.json()["data"]["pnl"] == model["pnl"]  # unchanged until recalculated

        recalculated = c.post(f"{API}/finance/model/{model_id}/recalculate", headers=wh)
        assert recalculated.status_code == 200, recalculated.text
        capture(G, "recalculated", recalculated)
        after = recalculated.json()["data"]
        assert after["stale"] is False and after["balance_check"]["balanced"] is True
        assert _row(after["cash_flow"], "Capex")[2] == 4_800_000
        assert _row(after["pnl"], "Revenue")[-1] > _row(model["pnl"], "Revenue")[-1]
        assert after["months"] == months

        again = c.post(f"{API}/finance/model/{model_id}/recalculate", headers=wh)
        assert again.json()["data"]["pnl"] == after["pnl"]  # deterministic

        refreshed = c.post(
            f"{API}/finance/model/{model_id}/recalculate",
            headers=wh,
            json={"refresh_actuals": True},
        )
        assert refreshed.status_code == 200, refreshed.text
        capture(G, "recalculated_refresh_actuals", refreshed)

        cleared = c.patch(
            f"{API}/finance/model/{model_id}/assumptions",
            headers=wh,
            json={"hiring_plan": None, "one_offs": None, "capex": None},
        )
        assert cleared.status_code == 200
        capture(G, "assumptions_cleared_edits", cleared)

        assert (
            c.patch(
                f"{API}/finance/model/{model_id}/assumptions", headers=tm, json={"ar_days": 25}
            ).status_code
            == 200
        )
        denied = c.patch(
            f"{API}/finance/model/{model_id}/assumptions", headers=mentor, json={"ar_days": 25}
        )
        assert denied.status_code == 403
        capture(G, "assumptions_mentor_403", denied)
        assert (
            c.post(f"{API}/finance/model/{model_id}/recalculate", headers=mentor).status_code == 403
        )

        # ---- 3. Mono bank link -----------------------------------------------------------------
        listed = c.get(f"{API}/finance/integrations", headers=wh)
        capture(G, "integrations_before", listed)
        mono_entry = next(p for p in listed.json()["data"]["providers"] if p["provider"] == "mono")
        assert mono_entry["connection"] is None and mono_entry["connect_config"]["sandbox"] is True

        tm_connect = c.post(
            f"{API}/finance/integrations/mono/connect", headers=tm, json={"code": "code_ok"}
        )
        assert tm_connect.status_code == 403
        capture(G, "mono_connect_team_member_403", tm_connect)
        bad_code = c.post(
            f"{API}/finance/integrations/mono/connect", headers=wh, json={"code": "bad_code"}
        )
        assert bad_code.status_code == 422
        capture(G, "mono_connect_bad_code", bad_code)
        no_code = c.post(f"{API}/finance/integrations/mono/connect", headers=wh, json={})
        assert no_code.status_code == 422
        capture(G, "mono_connect_missing_code", no_code)

        # A Paystack connection is what makes a Paystack payout recognisable (D-FIN-10).
        ps = c.post(
            f"{API}/finance/integrations/paystack/connect",
            headers=wh,
            json={"secret_key": f"sk_test_{uuid.uuid4().hex}"},
        )
        assert ps.status_code == 200, ps.text
        # ...and its charges must be in the ledger for a payout to be explained.
        ps_sync = c.post(f"{API}/finance/integrations/paystack/sync", headers=wh)
        assert ps_sync.status_code == 200, ps_sync.text
        assert ps_sync.json()["data"]["summary"]["imported"] == 1

        connected = c.post(
            f"{API}/finance/integrations/mono/connect", headers=wh, json={"code": "code_ok"}
        )
        assert connected.status_code == 200, connected.text
        capture(G, "mono_connected", connected)
        conn = connected.json()["data"]
        assert conn["provider"] == "mono" and conn["mode"] == "test" and conn["status"] == "active"
        assert "stubmonoacct0001" not in connected.text

        before_cash = c.get(f"{API}/finance/cash-flow", headers=wh).json()["data"]["cash_on_hand"]
        synced = c.post(f"{API}/finance/integrations/mono/sync", headers=tm)
        assert synced.status_code == 200, synced.text
        capture(G, "mono_sync", synced)
        summary = synced.json()["data"]["summary"]
        assert (summary["credits"], summary["debits"], summary["paystack_payouts"]) == (2, 1, 1)
        assert summary["paystack_unmatched"] == 0

        ledger = c.get(f"{API}/finance/transactions", headers=wh)
        capture(G, "ledger_after_mono_sync", ledger)
        bank = [r for r in ledger.json()["data"]["transactions"] if r["source"] == "bank"]
        by_ref = {r["description"]: r for r in bank}
        assert by_ref["PAYSTACK TRANSFER SETTLEMENT"]["category"] == "Paystack payout"
        assert by_ref["NIP transfer from ACME Ltd"]["category"] is None

        cash = c.get(f"{API}/finance/cash-flow", headers=wh)
        capture(G, "cash_flow_after_mono_sync", cash)
        # +1,500,000 credit -250,000 debit; the 492,500 payout does not change cash (D-FIN-10)
        assert cash.json()["data"]["cash_on_hand"] == before_cash + 1_500_000 - 250_000

        second = c.post(f"{API}/finance/integrations/mono/sync", headers=wh)
        capture(G, "mono_sync_again_idempotent", second)
        assert second.json()["data"]["summary"]["duplicates"] == 3
        assert second.json()["data"]["summary"]["credits"] == 0

        # ---- 4. Webhooks -----------------------------------------------------------------------
        body = json.dumps(
            {
                "event": "mono.events.account_updated",
                "data": {
                    "account": {"_id": "stubmonoacct0001"},
                    "meta": {"data_status": "AVAILABLE"},
                },
            }
        )
        hook = f"{API}/webhooks/mono"
        no_sig = c.post(hook, content=body, headers={"content-type": "application/json"})
        assert no_sig.status_code == 401
        capture(G, "webhook_missing_secret_401", no_sig)
        wrong = c.post(
            hook,
            content=body,
            headers={"content-type": "application/json", "mono-webhook-secret": "nope"},
        )
        assert wrong.status_code == 401
        ok = c.post(
            hook,
            content=body,
            headers={"content-type": "application/json", "mono-webhook-secret": secret},
        )
        assert ok.status_code == 200 and ok.json()["data"] == {"received": True}
        capture(G, "webhook_account_updated", ok)
        _drain()  # the queued sync runs in the worker
        assert (
            c.get(f"{API}/finance/integrations", headers=wh).json()["data"]["providers"][2][
                "connection"
            ]["last_error"]
            is None
        )

        reauth = json.dumps(
            {
                "event": "mono.events.reauthorisation_required",
                "data": {"account": {"_id": "stubmonoacct0001"}},
            }
        )
        r = c.post(
            hook,
            content=reauth,
            headers={"content-type": "application/json", "mono-webhook-secret": secret},
        )
        assert r.status_code == 200
        flagged = c.get(f"{API}/finance/integrations", headers=wh)
        capture(G, "integrations_needs_attention", flagged)
        flagged_conn = next(
            p for p in flagged.json()["data"]["providers"] if p["provider"] == "mono"
        )["connection"]
        assert flagged_conn["status"] == "needs_attention"

        # ---- 5. Reconnect (founder), then disconnect (keeps the ledger) -------------------------
        relinked = c.post(
            f"{API}/finance/integrations/mono/connect", headers=wh, json={"code": "code_ok"}
        )
        assert relinked.status_code == 200 and relinked.json()["data"]["status"] == "active"
        capture(G, "mono_reconnected", relinked)
        assert c.post(f"{API}/finance/integrations/mono/disconnect", headers=tm).status_code == 403
        gone = c.post(f"{API}/finance/integrations/mono/disconnect", headers=wh)
        assert gone.status_code == 200 and gone.json()["data"]["status"] == "disconnected"
        capture(G, "mono_disconnected", gone)
        kept = [
            r
            for r in c.get(f"{API}/finance/transactions", headers=wh).json()["data"]["transactions"]
            if r["source"] == "bank"
        ]
        assert len(kept) == 3
        after_gone = c.post(f"{API}/finance/integrations/mono/sync", headers=wh)
        assert after_gone.status_code == 404
