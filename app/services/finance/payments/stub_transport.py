"""Deterministic canned Paystack and Flutterwave responses for live e2e runs.

Only reachable when PAYMENT_PROVIDER_STUB is true outside production (see http.py).
Any secret key containing "bad" is answered with the provider's 401 body.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

_PAYSTACK_HOST = "api.paystack.co"
_FLUTTERWAVE_HOST = "api.flutterwave.com"
_MONO_HOST = "api.withmono.com"

_STUB_WHEN = "2026-10-01T09:30:00.000Z"

# Flutterwave payment links created through this stub, newest last. The verify endpoint answers
# any transaction id with the most recent link as a successful charge, so a webhook that matches
# the link the journey just created is confirmed (the stub cannot map ids to references).
_FLW_LINKS: list[dict[str, object]] = []


def _json(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


def _bearer(request: httpx.Request) -> str:
    auth: str = request.headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else auth


# Cofoundaz Billing's own Paystack account (services/billing/paystack.py) shares this stub. State
# lives in these module-level dicts so verify can echo what initialize was asked for; tests and
# journeys reset or poke them directly.
PS_TX: dict[str, dict[str, object]] = {}
PS_ABANDONED: set[str] = set()
PS_SUBS: dict[str, list[dict[str, Any]]] = {}


def _customer_code(email: str) -> str:
    return "CUS_stub_" + format(abs(hash(email)) % 10**8, "08d")


def _paystack_billing(request: httpx.Request, path: str) -> httpx.Response | None:
    body = json.loads(request.content or b"{}") if request.content else {}
    if path == "/plan" and request.method == "POST":
        name = str(body.get("name"))
        return _json(
            200,
            {
                "status": True,
                "message": "Plan created",
                "data": {
                    "plan_code": "PLN_stub_" + format(abs(hash(name)) % 10**8, "08d"),
                    "name": name,
                    "amount": body.get("amount"),
                },
            },
        )
    if path.startswith("/transaction/verify/"):
        ref = path.rsplit("/", 1)[1]
        tx = PS_TX.get(ref)
        if tx is None:
            return _json(404, {"status": False, "message": "Transaction reference not found"})
        if ref in PS_ABANDONED:
            return _json(
                200,
                {
                    "status": True,
                    "message": "Verification successful",
                    "data": {"status": "abandoned", "reference": ref, "amount": tx["amount"]},
                },
            )
        data = _tx_data(ref, tx)
        if tx.get("plan"):  # Paystack subscribes the customer when a plan-bound payment succeeds
            cust = str(data["customer"]["customer_code"])  # type: ignore[index]
            if not any(r["plan"]["plan_code"] == tx["plan"] for r in PS_SUBS.get(cust, [])):
                n = len(PS_SUBS.get(cust, [])) + 1
                PS_SUBS.setdefault(cust, []).append(
                    {
                        "subscription_code": f"SUB_stub_{n:04d}",
                        "email_token": f"tok_stub_{n:04d}",
                        "status": "active",
                        "plan": {"plan_code": tx["plan"]},
                    }
                )
        return _json(200, {"status": True, "message": "Verification successful", "data": data})
    if path == "/transaction/charge_authorization":
        ref = str(body["reference"])
        if "decline" in str(body.get("authorization_code")):
            return _json(
                200,
                {
                    "status": True,
                    "message": "Charge attempted",
                    "data": {"status": "failed", "reference": ref, "amount": body["amount"]},
                },
            )
        tx = {
            "amount": body["amount"],
            "email": body["email"],
            "plan": None,
            "authorization_code": body["authorization_code"],
        }
        PS_TX[ref] = tx
        return _json(
            200, {"status": True, "message": "Charge attempted", "data": _tx_data(ref, tx)}
        )
    if path == "/refund" and request.method == "POST":
        return _json(
            200,
            {
                "status": True,
                "message": "Refund has been queued for processing",
                "data": {
                    "transaction": {"reference": body.get("transaction")},
                    "status": "pending",
                },
            },
        )
    if path == "/subscription" and request.method == "POST":
        code = "SUB_stub_" + format(len(PS_SUBS.get(str(body["customer"]), [])) + 1, "04d")
        row = {
            "subscription_code": code,
            "email_token": "tok_stub_" + code[-4:],
            "status": "active",
            "plan": {"plan_code": body["plan"]},
        }
        PS_SUBS.setdefault(str(body["customer"]), []).append(row)
        return _json(
            200, {"status": True, "message": "Subscription successfully created", "data": row}
        )
    if path == "/subscription" and request.method == "GET":
        rows = PS_SUBS.get(str(request.url.params.get("customer")), [])
        return _json(200, {"status": True, "message": "Subscriptions retrieved", "data": rows})
    if path == "/subscription/disable":
        return _json(200, {"status": True, "message": "Subscription disabled successfully"})
    return None


def _tx_data(ref: str, tx: dict[str, object]) -> dict[str, object]:
    email = str(tx["email"])
    data: dict[str, object] = {
        "id": 7000 + len(PS_TX),
        "status": "success",
        "reference": ref,
        "amount": tx["amount"],
        "currency": "NGN",
        "paid_at": _STUB_WHEN,
        "channel": "card",
        "customer": {"email": email, "customer_code": _customer_code(email)},
        "authorization": {
            "authorization_code": tx.get("authorization_code") or "AUTH_stub_" + ref[-8:],
            "channel": "card",
            "reusable": True,
            "card_type": "visa",
            "last4": "4081",
            "exp_month": "12",
            "exp_year": "2030",
            "bank": "TEST BANK",
            "signature": "SIG_stub_visa_4081",
        },
    }
    if tx.get("plan"):
        data["plan_object"] = {"plan_code": tx["plan"]}
    return data


def _paystack(request: httpx.Request) -> httpx.Response:
    if "bad" in _bearer(request):
        return _json(401, {"status": False, "message": "Invalid key"})
    path = request.url.path
    billing = _paystack_billing(request, path)
    if billing is not None:
        return billing
    if path == "/balance":
        return _json(
            200,
            {
                "status": True,
                "message": "Balances retrieved",
                "data": [{"currency": "NGN", "balance": 1250000}],
            },
        )
    if path == "/transaction":
        return _json(
            200,
            {
                "status": True,
                "message": "Transactions retrieved",
                "meta": {"page": 1, "pageCount": 1},
                "data": [
                    {
                        "id": 9001,
                        "reference": "stub_ps_charge_1",
                        "status": "success",
                        "amount": 500000,
                        "fees": 7500,
                        "currency": "NGN",
                        "paid_at": _STUB_WHEN,
                        "created_at": _STUB_WHEN,
                    }
                ],
            },
        )
    if path == "/refund":  # GET list (Finance sync); the billing POST is answered above
        return _json(
            200,
            {
                "status": True,
                "message": "Refunds retrieved",
                "meta": {"page": 1, "pageCount": 1},
                "data": [],
            },
        )
    if path == "/transaction/initialize":
        body = json.loads(request.content or b"{}")
        ref = str(body.get("reference") or "stub_ps_ref")
        PS_TX[ref] = {
            "amount": body.get("amount"),
            "email": body.get("email"),
            "plan": body.get("plan"),
        }
        return _json(
            200,
            {
                "status": True,
                "message": "Authorization URL created",
                "data": {
                    "authorization_url": f"https://checkout.paystack.com/stub-{ref}",
                    "access_code": "stubaccess",
                    "reference": ref,
                },
            },
        )
    return _json(404, {"status": False, "message": "Not found"})


def _flutterwave(request: httpx.Request) -> httpx.Response:
    if "bad" in _bearer(request):
        return _json(401, {"status": "error", "message": "Invalid authorization key", "data": None})
    path = request.url.path
    if path == "/v3/balances":
        return _json(
            200,
            {
                "status": "success",
                "message": "Balances fetched",
                "data": [{"currency": "NGN", "available_balance": 12500.5}],
            },
        )
    if path == "/v3/transactions":
        return _json(
            200,
            {
                "status": "success",
                "message": "Transactions fetched successfully",
                "meta": {"page_info": {"total": 1, "current_page": 1, "total_pages": 1}},
                "data": [
                    {
                        "id": 9101,
                        "tx_ref": "stub_flw_charge_1",
                        "amount": 5000,
                        "app_fee": 75.5,
                        "currency": "NGN",
                        "status": "successful",
                        "created_at": _STUB_WHEN,
                    }
                ],
            },
        )
    if path == "/v3/refunds":
        return _json(
            200,
            {
                "status": "success",
                "message": "Refunds fetched",
                "meta": {"page_info": {"total": 0, "current_page": 1, "total_pages": 1}},
                "data": [],
            },
        )
    if path == "/v3/payments":
        body = json.loads(request.content or b"{}")
        ref = str(body.get("tx_ref") or "stub_flw_ref")
        _FLW_LINKS.append(
            {"tx_ref": ref, "amount": body.get("amount"), "currency": body.get("currency")}
        )
        del _FLW_LINKS[:-50]
        return _json(
            200,
            {
                "status": "success",
                "message": "Hosted Link",
                "data": {"link": f"https://checkout.flutterwave.com/v3/hosted/pay/stub-{ref}"},
            },
        )
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["v3", "transactions"] and parts[3] == "verify":
        if not _FLW_LINKS:
            return _json(
                404,
                {
                    "status": "error",
                    "message": "No transaction was found for this id",
                    "data": None,
                },
            )
        link = _FLW_LINKS[-1]
        return _json(
            200,
            {
                "status": "success",
                "message": "Transaction fetched successfully",
                "data": {
                    "id": int(parts[2]) if parts[2].isdigit() else parts[2],
                    "tx_ref": link["tx_ref"],
                    "amount": json.loads(str(link["amount"] or "0")),
                    "currency": link["currency"] or "NGN",
                    "app_fee": 24.2,
                    "status": "successful",
                    "created_at": _STUB_WHEN,
                },
            },
        )
    return _json(404, {"status": "error", "message": "Not found", "data": None})


def _mono(request: httpx.Request) -> httpx.Response:
    if "bad" in request.headers.get("mono-sec-key", ""):
        return _json(401, {"status": "failed", "message": "Invalid secret key"})
    path = request.url.path
    if path == "/v2/accounts/auth":
        body = json.loads(request.content or b"{}")
        if "bad" in str(body.get("code") or ""):
            return _json(400, {"status": "failed", "message": "Invalid code"})
        return _json(
            200, {"status": "successful", "message": "Success", "data": {"id": "stubmonoacct0001"}}
        )
    parts = path.strip("/").split("/")
    if parts[:2] == ["v2", "accounts"] and len(parts) == 3:
        return _json(
            200,
            {
                "status": "successful",
                "data": {
                    "account": {
                        "id": parts[2],
                        "name": "NovaPay Ltd",
                        "accountNumber": "0123456789",
                        "currency": "NGN",
                        "institution": {"name": "Stub Bank", "bankCode": "000", "type": "BUSINESS"},
                    }
                },
            },
        )
    if parts[:2] == ["v2", "accounts"] and len(parts) == 4 and parts[3] == "transactions":
        rows = [
            ("stub_mono_tx_1", "PAYSTACK TRANSFER SETTLEMENT", 492500, "credit"),
            ("stub_mono_tx_2", "NIP transfer from ACME Ltd", 1500000, "credit"),
            ("stub_mono_tx_3", "POS purchase office supplies", 250000, "debit"),
        ]
        return _json(
            200,
            {
                "status": "successful",
                "data": [
                    {"id": i, "narration": n, "amount": a, "type": t, "date": _STUB_WHEN}
                    for i, n, a, t in rows
                ],
                "meta": {"total": 3, "page": 1, "previous": None, "next": None},
            },
        )
    return _json(404, {"status": "failed", "message": "Not found"})


def _handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == _PAYSTACK_HOST:
        return _paystack(request)
    if host == _FLUTTERWAVE_HOST:
        return _flutterwave(request)
    if host == _MONO_HOST:
        return _mono(request)
    return _json(404, {"message": "Unknown stub host"})


def stub_transport() -> httpx.BaseTransport:
    return httpx.MockTransport(_handler)
