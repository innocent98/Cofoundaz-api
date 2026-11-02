"""Live: Billing on Paystack, end to end against a real server (Paystack itself is the in-process
stub, enabled by PAYMENT_PROVIDER_STUB; the webhook is signed here with the same key the server holds).

Captures land in e2e/_captures/billing/ and are the verbatim source of
docs/fe-integration-guide-settings-billing.md. Nothing here touches the real Paystack: the
round trip through Paystack's hosted checkout page and Paystack's own webhook delivery are the two
things this journey cannot do (see the guide's verification table).
"""

import hashlib
import hmac
import json
import os

import httpx

# Generated per run by scripts/e2e_run.sh and shared with the server (no key literal in the repo).
# Absent on the live gate, which deselects this journey but still imports the module.
KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
B = "/api/v1/billing"
CUS = "CUS_e2e_placeholder"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> dict:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    wsid = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
    return {**auth, "X-Workspace-Id": wsid}


def _event(c: httpx.Client, event: str, data: dict, *, sig: str | None = None) -> httpx.Response:
    raw = json.dumps({"event": event, "data": data}, sort_keys=True).encode()
    good = hmac.new(KEY.encode(), raw, hashlib.sha512).hexdigest()
    return c.post(
        "/api/v1/webhooks/billing/paystack",
        content=raw,
        headers={
            "x-paystack-signature": sig if sig is not None else good,
            "content-type": "application/json",
        },
    )


def _charge(ref: str, amount: int, email: str) -> dict:
    return {
        "id": 1,
        "status": "success",
        "reference": ref,
        "amount": amount,
        "currency": "NGN",
        "paid_at": "2026-10-27T12:00:00.000Z",
        "channel": "card",
        "customer": {"email": email, "customer_code": CUS},
        "authorization": {
            "authorization_code": "AUTH_e2e_placeholder",
            "channel": "card",
            "reusable": True,
            "card_type": "visa",
            "last4": "4081",
            "exp_month": "12",
            "exp_year": "2030",
            "bank": "TEST BANK",
            "signature": "SIG_e2e_placeholder",
        },
    }


def test_billing_journey(base_url, make_verified_user, capture, capture_json):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        h = _onboard(c, _auth(access))

        r = c.get(f"{B}/plans", headers=h)
        assert r.status_code == 200
        capture("billing", "plans", r)
        r = c.get(f"{B}/subscription", headers=h)
        assert r.json()["data"]["status"] == "free"
        capture("billing", "subscription_free", r)

        # ---- checkout ------------------------------------------------------------------------
        r = c.post(
            f"{B}/subscription/checkout",
            json={"plan": "growth", "interval": "monthly"},
            headers={**h, "Idempotency-Key": "e2e-checkout-1"},
        )
        assert r.status_code == 200, r.text
        capture("billing", "checkout", r)
        ref = r.json()["data"]["reference"]
        replay = c.post(
            f"{B}/subscription/checkout",
            json={"plan": "growth", "interval": "monthly"},
            headers={**h, "Idempotency-Key": "e2e-checkout-1"},
        )
        assert replay.json()["data"] == r.json()["data"]
        r = c.post(
            f"{B}/subscription/checkout", json={"plan": "starter", "interval": "monthly"}, headers=h
        )
        assert r.status_code == 422
        capture("billing", "checkout_starter", r)

        # ---- webhook: signature, success, duplicate ------------------------------------------
        charge = _charge(ref, 2_400_000, u["email"])
        r = _event(c, "charge.success", charge, sig="not-a-valid-signature")
        assert r.status_code == 401
        capture("billing", "webhook_bad_signature", r)
        r = _event(c, "charge.success", charge)
        assert r.status_code == 200 and r.json()["data"]["outcome"] == "applied", r.text
        capture("billing", "webhook_charge_success", r)
        r = _event(c, "charge.success", charge)
        assert r.json()["data"]["outcome"] == "duplicate"
        capture("billing", "webhook_duplicate", r)
        r = _event(
            c,
            "subscription.create",
            {
                "subscription_code": "SUB_e2e_placeholder",
                "email_token": "e2e-fake",
                "customer": {"customer_code": CUS},
                "plan": {"plan_code": "PLN_e2e"},
            },
        )
        assert r.json()["data"]["outcome"] == "linked"
        r = c.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
        assert r.json()["data"]["outcome"] == "already_processed"
        capture("billing", "verify_after_webhook", r)

        r = c.get(f"{B}/subscription", headers=h)
        assert r.json()["data"]["status"] == "active"
        capture("billing", "subscription_active", r)
        capture("billing", "invoices", c.get(f"{B}/invoices", headers=h))
        r = c.get(f"{B}/payment-methods", headers=h)
        assert r.json()["data"][0]["last4"] == "4081"
        capture("billing", "payment_methods", r)
        r = c.get(f"{B}/usage", headers=h)
        assert r.status_code == 200
        capture("billing", "usage", r)

        # ---- change plan ---------------------------------------------------------------------
        r = c.post(
            f"{B}/subscription/preview-change",
            json={"plan": "scale", "interval": "monthly"},
            headers=h,
        )
        assert r.json()["data"]["kind"] == "upgrade"
        capture("billing", "preview_upgrade", r)
        r = c.post(
            f"{B}/subscription/change",
            json={"plan": "scale", "interval": "monthly"},
            headers={**h, "Idempotency-Key": "e2e-up-1"},
        )
        assert r.status_code == 200 and r.json()["data"]["result"] == "applied", r.text
        capture("billing", "change_upgrade", r)
        r = c.post(
            f"{B}/subscription/preview-change",
            json={"plan": "growth", "interval": "monthly"},
            headers=h,
        )
        assert r.json()["data"]["kind"] == "downgrade"
        capture("billing", "preview_downgrade", r)
        r = c.post(
            f"{B}/subscription/change",
            json={"plan": "growth", "interval": "monthly"},
            headers={**h, "Idempotency-Key": "e2e-chg-2"},
        )
        assert r.json()["data"]["result"] == "scheduled"
        capture("billing", "change_downgrade", r)
        r = c.get(f"{B}/subscription", headers=h)
        assert r.json()["data"]["pending_change"]["plan"]["key"] == "growth"
        capture("billing", "subscription_pending_downgrade", r)
        r = c.post(
            f"{B}/subscription/change",
            json={"plan": "scale", "interval": "monthly"},
            headers={**h, "Idempotency-Key": "e2e-chg-3"},
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "NO_CHANGE"
        capture("billing", "change_no_change", r)

        # ---- cancel / resume -----------------------------------------------------------------
        r = c.post(
            f"{B}/subscription/cancel", json={"reason": "Too expensive right now"}, headers=h
        )
        assert r.status_code == 200
        capture("billing", "cancel", r)
        r = c.get(f"{B}/subscription", headers=h)
        assert r.json()["data"]["cancel_at_period_end"] is True
        capture("billing", "subscription_canceling", r)
        r = c.post(f"{B}/subscription/resume", headers=h)
        assert r.json()["data"]["cancel_at_period_end"] is False
        capture("billing", "resume", r)

        # ---- renewal and a failed payment ----------------------------------------------------
        r = _event(
            c,
            "charge.success",
            {
                "status": "success",
                "reference": "e2e-renewal-1",
                "amount": 6_800_000,
                "currency": "NGN",
                "paid_at": "2026-11-27T10:00:00.000Z",
                "customer": {"customer_code": CUS},
                "plan": {"plan_code": "PLN_e2e"},
            },
        )
        assert r.json()["data"]["outcome"] == "renewed", r.text
        capture("billing", "webhook_renewal", r)
        r = _event(
            c,
            "invoice.payment_failed",
            {
                "amount": 6_800_000,
                "subscription": {"subscription_code": "SUB_e2e_placeholder"},
                "customer": {"customer_code": CUS},
            },
        )
        assert r.json()["data"]["outcome"] == "past_due", r.text
        capture("billing", "webhook_payment_failed", r)
        r = c.get(f"{B}/subscription", headers=h)
        assert r.json()["data"]["status"] == "past_due"
        capture("billing", "subscription_past_due", r)

        # ---- add-on, cards, details, receipts ------------------------------------------------
        r = c.post(
            f"{B}/addons/credits_2000/purchase", headers={**h, "Idempotency-Key": "e2e-addon-1"}
        )
        assert r.status_code == 200
        capture("billing", "addon_purchase", r)
        aref = r.json()["data"]["reference"]
        r = c.get(f"{B}/subscription/verify", params={"reference": aref}, headers=h)
        assert r.json()["data"]["status"] == "paid"
        capture("billing", "verify_addon", r)
        assert c.get(f"{B}/usage", headers=h).json()["data"]["ai_credits"]["from_packs"] == 2000
        r = c.post(f"{B}/payment-methods", headers=h)
        assert r.status_code == 200
        capture("billing", "add_card", r)
        cards = c.get(f"{B}/payment-methods", headers=h).json()["data"]
        assert len(cards) == 2  # the webhook's card and the one the add-on charge reported
        capture("billing", "payment_methods_two", c.get(f"{B}/payment-methods", headers=h))
        r = c.put(f"{B}/payment-methods/{cards[1]['id']}/default", headers=h)
        assert r.status_code == 200
        capture("billing", "payment_method_default", r)
        r = c.delete(f"{B}/payment-methods/{cards[0]['id']}", headers=h)
        assert r.status_code == 200
        capture("billing", "payment_method_remove", r)
        r = c.delete(f"{B}/payment-methods/{cards[1]['id']}", headers=h)
        assert r.status_code == 409
        capture("billing", "remove_last_card", r)
        r = c.put(
            f"{B}/details",
            headers=h,
            json={
                "company_name": "Cofoundaz Ltd",
                "tax_id": "TIN-PLACEHOLDER",
                "address": "1 Placeholder Street, Lagos",
            },
        )
        assert r.status_code == 200
        capture("billing", "details", r)
        invs = c.get(f"{B}/invoices", headers=h).json()["data"]
        capture_json("billing", "invoices_after", {"data": invs, "meta": None})
        pdf = c.get(f"{B}/invoices/{invs[0]['id']}/pdf", headers=h)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        capture_json(
            "billing",
            "invoice_pdf_headers",
            {
                "status": pdf.status_code,
                "content-type": pdf.headers["content-type"],
                "content-disposition": pdf.headers["content-disposition"],
                "starts_with": pdf.content[:5].decode(),
            },
        )

        # ---- trial and access ----------------------------------------------------------------
        u2 = make_verified_user(c)
        t2 = c.post("/api/v1/auth/login", json=u2).json()["data"]["access_token"]
        h2 = _onboard(c, _auth(t2))
        r = c.post(f"{B}/trial", headers=h2)
        assert r.status_code == 200 and r.json()["data"]["status"] == "trialing"
        capture("billing", "trial_started", r)
        r = c.post(f"{B}/trial", headers=h2)
        assert r.status_code == 409
        capture("billing", "trial_again", r)
        r = c.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h2)
        assert r.status_code == 404
        capture("billing", "verify_foreign_reference", r)
        r = c.post(f"{B}/subscription/cancel", json={}, headers=h2)
        assert r.status_code == 409
        capture("billing", "cancel_not_subscribed", r)
