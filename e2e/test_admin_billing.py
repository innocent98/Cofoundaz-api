"""Live: Admin slice C. Super Admin billing overrides (refund, credit, extend trial, comp) against
a real server, with Paystack as the in-process stub (the charge webhook is signed here with the
per-run key the server holds).

Captures land in e2e/_captures/admin_billing/ and are the verbatim source of
docs/fe-integration-guide-admin-billing.md. The e2e database is shared by every journey, so
nothing here assumes it is the only staff or the only workspace: staff are found by email, and
every workspace, invoice and customer code is created by this journey.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pyotp

KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
S = "/api/v1/super-admin"
B = "/api/v1/billing"
S = "/api/v1/super-admin"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    """A phone's authenticator app: a code not yet spent on a step-up (3 fit the ~90 s window,
    each works once), waiting for the next one when all are used."""

    def __init__(self, secret: str) -> None:
        self.totp = pyotp.TOTP(secret)
        self.used: set[str] = set()

    def fresh(self) -> str:
        for _ in range(80):
            now = datetime.now(UTC)
            for k in (0, -1, 1):
                c = self.totp.at(now + timedelta(seconds=30 * k))
                if c not in self.used:
                    self.used.add(c)
                    return c
            time.sleep(1)
        raise AssertionError("no unused authenticator code became available")


def _enroll(http: httpx.Client, u: dict) -> Authenticator:
    login = http.post("/api/v1/auth/login", json=u).json()["data"]
    auth = _auth(login["access_token"])
    secret = http.post("/api/v1/auth/mfa/totp/setup", headers=auth).json()["data"]["secret"]
    r = http.post(
        "/api/v1/auth/mfa/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=auth
    )
    assert r.status_code == 200, r.text
    return Authenticator(secret)


def _sign_in(http: httpx.Client, u: dict, app: Authenticator) -> dict:
    gate = http.post("/api/v1/auth/login", json=u).json()["data"]
    r = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": app.totp.now()},
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["data"]["access_token"])


def _onboard(c: httpx.Client, auth: dict, name: str) -> str:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    return c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]


def _staff_by_email(http: httpx.Client, headers: dict, email: str) -> dict:
    """The e2e database is shared by every journey, so never pick staff by role alone."""
    rows = http.get(f"{S}/staff", headers=headers).json()["data"]
    return next(s for s in rows if s["email"] == email)


def _event(c: httpx.Client, event: str, data: dict) -> httpx.Response:
    raw = json.dumps({"event": event, "data": data}, sort_keys=True).encode()
    return c.post(
        "/api/v1/webhooks/billing/paystack",
        content=raw,
        headers={
            "x-paystack-signature": hmac.new(KEY.encode(), raw, hashlib.sha512).hexdigest(),
            "content-type": "application/json",
        },
    )


def _charge(ref: str, amount: int, email: str, customer: str) -> dict:
    return {
        "id": 1,
        "status": "success",
        "reference": ref,
        "amount": amount,
        "currency": "NGN",
        "paid_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "channel": "card",
        "customer": {"email": email, "customer_code": customer},
        "authorization": {
            "authorization_code": f"AUTH_{uuid.uuid4().hex[:10]}",
            "channel": "card",
            "reusable": True,
            "card_type": "visa",
            "last4": "4081",
            "exp_month": "12",
            "exp_year": "2030",
            "bank": "TEST BANK",
            "signature": f"SIG_{uuid.uuid4().hex[:10]}",
        },
    }


def _founder(base_url, make_verified_user, http, name):
    c = httpx.Client(base_url=base_url, timeout=20.0)
    u = make_verified_user(http)
    token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
    auth = _auth(token)
    ws = _onboard(c, auth, name)
    return c, u, {**auth, "X-Workspace-Id": ws}, ws


def _post(http, who, app, ws_id, action, body, reason="Goodwill after a failed renewal"):
    code = app.fresh() if len(reason) >= 10 else "123456"  # a short reason never reaches step-up
    return http.post(
        f"{S}/billing/{ws_id}/{action}",
        json={"request_id": str(uuid.uuid4()), "reason": reason, "totp_code": code, **body},
        headers=who,
    )


def test_admin_billing_journey(http: httpx.Client, base_url, make_verified_user, capture):
    # ---- Super Admins by command; the e2e database may already hold other staff. A step-up
    # code works once per person and a new one arrives every 30 s, so the journey rotates
    # through four of them rather than waiting on one authenticator ------------------------
    team = []
    for _ in range(4):
        user = make_verified_user(http)
        app = _enroll(http, user)
        run = subprocess.run(
            [sys.executable, "-m", "app.scripts.create_super_admin", user["email"]],
            capture_output=True,
            text=True,
            env=os.environ,
        )
        assert run.returncode == 0, run.stderr
        team.append((_sign_in(http, user, app), app))
    sa, sa_app = team[0]
    turn = iter(range(10**6))

    def act(ws_id, action, body, reason="Goodwill after a failed renewal", staff=None):
        # a replay must come from the same staff member that made the first call
        who, app = team[next(turn) % len(team) if staff is None else staff]
        return _post(http, who, app, ws_id, action, body, reason)

    # ---- three founders: one that pays, one on a trial, one on the free plan ----------------
    payer, payer_u, ph, pay_ws = _founder(base_url, make_verified_user, http, "PayCo")
    trial, _, th, trial_ws = _founder(base_url, make_verified_user, http, "TrialCo")
    free, _, fh, free_ws = _founder(base_url, make_verified_user, http, "FreeCo")

    # ---- hidden from non-staff ---------------------------------------------------------------
    r = http.get(f"{S}/billing/{pay_ws}", headers=ph)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    capture("admin_billing", "overview_non_staff_404", r)

    # ---- the payer buys Growth (signed Paystack webhook), so there is an invoice to refund --
    customer = f"CUS_e2e_{uuid.uuid4().hex[:10]}"
    r = payer.post(
        f"{B}/subscription/checkout",
        json={"plan": "growth", "interval": "monthly"},
        headers={**ph, "Idempotency-Key": f"e2e-ab-{uuid.uuid4().hex[:8]}"},
    )
    assert r.status_code == 200, r.text
    ref = r.json()["data"]["reference"]
    r = _event(payer, "charge.success", _charge(ref, 2_400_000, payer_u["email"], customer))
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "applied", r.text
    invoices = payer.get(f"{B}/invoices", headers=ph).json()["data"]
    capture("admin_billing", "founder_invoices_before", payer.get(f"{B}/invoices", headers=ph))
    inv = invoices[0]
    assert inv["amount_minor"] == 2_400_000 and inv["refunded_minor"] == 0

    # ---- overview ----------------------------------------------------------------------------
    r = http.get(f"{S}/billing/{pay_ws}", headers=sa)
    assert r.status_code == 200, r.text
    capture("admin_billing", "overview", r)
    r = http.get(f"{S}/billing/{uuid.uuid4()}", headers=sa)
    assert r.status_code == 404 and r.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"
    capture("admin_billing", "overview_unknown_workspace", r)

    # ---- validation and step-up --------------------------------------------------------------
    body = {"invoice_id": inv["id"], "amount_minor": 1_000_000}
    r = act(pay_ws, "refund", body, reason="short")
    assert r.status_code == 422
    capture("admin_billing", "refund_reason_too_short", r)
    r = http.post(
        f"{S}/billing/{pay_ws}/refund",
        json={
            "request_id": str(uuid.uuid4()),
            "reason": "Goodwill refund",
            "totp_code": "000000",
            **body,
        },
        headers=sa,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture("admin_billing", "refund_wrong_code", r)

    # ---- refund: partial, replay, over-refund, full ------------------------------------------
    rid = str(uuid.uuid4())
    one = {"request_id": rid, **body}
    r = act(pay_ws, "refund", one, staff=0)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["replayed"] is False
    capture("admin_billing", "refund_partial_ok", r)
    first_id = r.json()["data"]["override"]["id"]
    r = act(pay_ws, "refund", one, staff=0)
    assert r.status_code == 200 and r.json()["data"]["replayed"] is True
    assert r.json()["data"]["override"]["id"] == first_id
    capture("admin_billing", "refund_replay", r)
    r = act(pay_ws, "refund", {"invoice_id": inv["id"], "amount_minor": 1_400_001})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REFUND_EXCEEDS_INVOICE"
    capture("admin_billing", "refund_over_invoice", r)
    r = act(pay_ws, "refund", {"invoice_id": inv["id"], "amount_minor": 1_400_000})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["invoice"]["status"] == "paid" and d["invoice"]["refund_pending_minor"] == 2_400_000
    capture("admin_billing", "refund_full_recorded", r)
    second_id = d["override"]["id"]
    r = act(pay_ws, "refund", {"invoice_id": inv["id"], "amount_minor": 100})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REFUND_EXCEEDS_INVOICE"
    capture("admin_billing", "refund_nothing_left", r)
    r = act(free_ws, "refund", {"invoice_id": inv["id"], "amount_minor": 100})
    assert r.status_code == 404 and r.json()["error"]["code"] == "INVOICE_NOT_FOUND"
    capture("admin_billing", "refund_invoice_of_another_workspace", r)
    # nothing the founder can see has changed: a recorded refund is not a refund until it is done
    r = payer.get(f"{B}/invoices", headers=ph)
    assert r.json()["data"][0]["status"] == "paid" and r.json()["data"][0]["refunded_minor"] == 0
    capture("admin_billing", "founder_invoices_refund_pending", r)

    # ---- mark the refunds done (after making them in Paystack) --------------------------------
    def complete(ws_id, override_id, reason="Refunded in the Paystack dashboard", code=None):
        who, app = team[next(turn) % len(team)]
        return http.post(
            f"{S}/billing/{ws_id}/refunds/{override_id}/complete",
            json={"reason": reason, "totp_code": code or app.fresh()},
            headers=who,
        )

    r = complete(free_ws, first_id)
    assert r.status_code == 404 and r.json()["error"]["code"] == "REFUND_NOT_FOUND"
    capture("admin_billing", "refund_complete_other_workspace", r)
    r = complete(pay_ws, first_id)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["override"]["provider_refund"] == "done"
    capture("admin_billing", "refund_complete_partial", r)
    r = payer.get(f"{B}/invoices", headers=ph)
    assert r.json()["data"][0]["status"] == "paid"
    assert r.json()["data"][0]["refunded_minor"] == 1_000_000
    capture("admin_billing", "founder_invoices_partial_done", r)
    r = complete(pay_ws, first_id)
    assert r.status_code == 200 and r.json()["data"]["replayed"] is True
    capture("admin_billing", "refund_complete_replay", r)
    r = complete(pay_ws, second_id)
    assert r.status_code == 200
    assert r.json()["data"]["invoice"]["status"] == "refunded"
    capture("admin_billing", "refund_complete_full", r)
    r = payer.get(f"{B}/invoices", headers=ph)
    assert r.json()["data"][0]["status"] == "refunded"
    assert r.json()["data"][0]["refunded_minor"] == 2_400_000
    capture("admin_billing", "founder_invoices_after", r)
    r = act(pay_ws, "refund", {"invoice_id": inv["id"], "amount_minor": 100})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVOICE_NOT_REFUNDABLE"
    capture("admin_billing", "refund_already_refunded", r)
    r = payer.get(f"{B}/subscription", headers=ph)
    assert r.json()["data"]["status"] == "active" and r.json()["data"]["plan"]["key"] == "growth"
    capture("admin_billing", "founder_subscription_after_refund", r)

    # ---- credit -------------------------------------------------------------------------------
    before = payer.get(f"{B}/usage", headers=ph).json()["data"]["ai_credits"]
    capture("admin_billing", "founder_usage_before_credit", payer.get(f"{B}/usage", headers=ph))
    r = act(pay_ws, "credit", {"credits": 500})
    assert r.status_code == 200, r.text
    capture("admin_billing", "credit_ok", r)
    r = payer.get(f"{B}/usage", headers=ph)
    assert r.json()["data"]["ai_credits"]["limit"] == before["limit"] + 500
    capture("admin_billing", "founder_usage_after_credit", r)
    r = act(pay_ws, "credit", {"credits": 100_001})
    assert r.status_code == 422
    capture("admin_billing", "credit_too_many", r)

    # ---- extend trial -------------------------------------------------------------------------
    r = act(trial_ws, "extend-trial", {"days": 7})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_TRIALING"
    capture("admin_billing", "extend_trial_not_trialing", r)
    r = trial.post(f"{B}/trial", headers=th)
    assert r.status_code == 200 and r.json()["data"]["status"] == "trialing"
    ends_before = r.json()["data"]["trial_ends_at"]
    r = act(trial_ws, "extend-trial", {"days": 7})
    assert r.status_code == 200, r.text
    capture("admin_billing", "extend_trial_ok", r)
    r = trial.get(f"{B}/subscription", headers=th)
    assert datetime.fromisoformat(r.json()["data"]["trial_ends_at"]) == datetime.fromisoformat(
        ends_before
    ) + timedelta(days=7)
    capture("admin_billing", "founder_subscription_after_trial_extension", r)
    assert act(trial_ws, "extend-trial", {"days": 60}).status_code == 200  # day 81 of 90
    r = act(trial_ws, "extend-trial", {"days": 30})
    assert r.status_code == 422 and r.json()["error"]["code"] == "TRIAL_TOO_LONG"
    capture("admin_billing", "extend_trial_too_long", r)

    # ---- comp ---------------------------------------------------------------------------------
    r = act(pay_ws, "comp", {"plan_key": "scale", "days": 30})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAID_SUBSCRIPTION"
    capture("admin_billing", "comp_paying_workspace", r)
    r = act(free_ws, "comp", {"plan_key": "starter", "days": 30})
    assert r.status_code == 422 and r.json()["error"]["code"] == "PLAN_NOT_COMPABLE"
    capture("admin_billing", "comp_starter", r)
    r = act(free_ws, "comp", {"plan_key": "growth", "days": 30})
    assert r.status_code == 200, r.text
    capture("admin_billing", "comp_ok", r)
    r = free.get(f"{B}/subscription", headers=fh)
    d = r.json()["data"]
    assert d["plan"]["key"] == "growth" and d["status"] == "active" and d["comped"] is True
    capture("admin_billing", "founder_subscription_comped", r)
    r = free.post(f"{B}/subscription/cancel", json={}, headers=fh)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PLAN_COMPLIMENTARY"
    capture("admin_billing", "founder_cancel_while_comped", r)
    r = free.post(
        f"{B}/subscription/checkout",
        json={"plan": "scale", "interval": "monthly"},
        headers={**fh, "Idempotency-Key": f"e2e-ab-{uuid.uuid4().hex[:8]}"},
    )
    assert r.status_code == 200
    capture("admin_billing", "founder_checkout_while_comped", r)

    # ---- the audit log has every override, with the reason ------------------------------------
    r = http.get(
        f"{S}/audit",
        params={"action": "billing.", "limit": 100, "workspace_id": pay_ws},
        headers=sa,
    )
    assert r.status_code == 200, r.text
    actions = {e["action"] for e in r.json()["data"]["entries"]}
    assert {
        "billing.refund.issued",
        "billing.refund.completed",
        "billing.credit.applied",
    } <= actions
    capture("admin_billing", "audit_for_workspace", r)
    r = http.get(f"{S}/billing/{pay_ws}", headers=sa)
    assert [o["kind"] for o in r.json()["data"]["overrides"]].count("refund") == 2
    capture("admin_billing", "overview_after", r)
