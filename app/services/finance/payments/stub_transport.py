"""Deterministic canned Paystack and Flutterwave responses for live e2e runs.

Only reachable when PAYMENT_PROVIDER_STUB is true outside production (see http.py).
Any secret key containing "bad" is answered with the provider's 401 body.
"""

from __future__ import annotations

import json

import httpx

_PAYSTACK_HOST = "api.paystack.co"
_FLUTTERWAVE_HOST = "api.flutterwave.com"

_STUB_WHEN = "2026-10-01T09:30:00.000Z"


def _json(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


def _bearer(request: httpx.Request) -> str:
    auth: str = request.headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else auth


def _paystack(request: httpx.Request) -> httpx.Response:
    if "bad" in _bearer(request):
        return _json(401, {"status": False, "message": "Invalid key"})
    path = request.url.path
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
    if path == "/refund":
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
        return _json(
            200,
            {
                "status": "success",
                "message": "Hosted Link",
                "data": {"link": f"https://checkout.flutterwave.com/v3/hosted/pay/stub-{ref}"},
            },
        )
    return _json(404, {"status": "error", "message": "Not found", "data": None})


def _handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == _PAYSTACK_HOST:
        return _paystack(request)
    if host == _FLUTTERWAVE_HOST:
        return _flutterwave(request)
    return _json(404, {"message": "Unknown stub host"})


def stub_transport() -> httpx.BaseTransport:
    return httpx.MockTransport(_handler)
