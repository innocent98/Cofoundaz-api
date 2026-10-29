"""Paystack client for Cofoundaz's own billing account (not the per-founder keys in Finance).

Test mode only outside production: a live key is refused unless ENVIRONMENT is production. The
secret never leaves this module: it is read per call, sent as a bearer header and never logged,
returned or put in an exception message.
"""

import hashlib
import hmac
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.services.finance.payments.http import http_client

BASE_URL = "https://api.paystack.co"


class BillingNotConfigured(AppError):  # noqa: N818
    code, http_status = "BILLING_NOT_CONFIGURED", 503
    message = "Billing isn't set up on this server yet."


class PaystackUnavailable(AppError):  # noqa: N818
    code, http_status = "PAYSTACK_UNAVAILABLE", 502
    message = "Our payment provider isn't answering. Try again in a moment."


class PaystackRejected(AppError):  # noqa: N818
    code, http_status = "PAYSTACK_REJECTED", 502
    message = "Our payment provider couldn't complete that. Nothing was charged."


def secret_key() -> str:
    key = settings.PAYSTACK_SECRET_KEY or settings.PAYSTACK_TEST_SECRET_KEY
    if not key:
        raise BillingNotConfigured()
    if key.startswith("sk_live_") and settings.ENVIRONMENT != "production":
        raise BillingNotConfigured(message="A live Paystack key is only allowed in production.")
    return key


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """Paystack signs the raw body with HMAC-SHA512 under the secret key (x-paystack-signature).
    Constant-time compare, and unsigned or unconfigured requests fail closed."""
    if not signature:
        return False
    try:
        key = secret_key()
    except BillingNotConfigured:
        return False
    expected = hmac.new(key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = secret_key()
    client = http_client()
    try:
        resp = client.request(
            method,
            BASE_URL + path,
            json=json_body,
            params=params,
            headers={"Authorization": f"Bearer {key}"},
        )
    except httpx.HTTPError:
        raise PaystackUnavailable() from None
    finally:
        client.close()
    if resp.status_code >= 500:
        raise PaystackUnavailable()
    try:
        body = resp.json()
    except ValueError:
        raise PaystackRejected() from None
    if resp.status_code >= 400 or not isinstance(body, dict) or body.get("status") is not True:
        raise PaystackRejected()
    return body


def _data(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    if not isinstance(data, dict):
        raise PaystackRejected()
    return data


def create_plan(*, name: str, amount_minor: int, interval: str) -> str:
    body = _request(
        "POST",
        "/plan",
        json_body={
            "name": name,
            "amount": amount_minor,
            "interval": "monthly" if interval == "monthly" else "annually",
            "currency": "NGN",
        },
    )
    code = _data(body).get("plan_code")
    if not isinstance(code, str):
        raise PaystackRejected()
    return code


def initialize(
    *,
    email: str,
    amount_minor: int,
    reference: str,
    callback_url: str,
    plan_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": email,
        "amount": amount_minor,
        "currency": "NGN",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": metadata or {},
    }
    if plan_code:
        payload["plan"] = plan_code  # Paystack then charges the plan's amount and subscribes
    return _data(_request("POST", "/transaction/initialize", json_body=payload))


def verify(reference: str) -> dict[str, Any]:
    return _data(_request("GET", f"/transaction/verify/{reference}"))


def charge_authorization(
    *,
    authorization_code: str,
    email: str,
    amount_minor: int,
    reference: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _data(
        _request(
            "POST",
            "/transaction/charge_authorization",
            json_body={
                "authorization_code": authorization_code,
                "email": email,
                "amount": amount_minor,
                "currency": "NGN",
                "reference": reference,
                "metadata": metadata or {},
            },
        )
    )


def refund(transaction_reference: str) -> None:
    _request("POST", "/refund", json_body={"transaction": transaction_reference})


def list_subscriptions(customer_code: str) -> list[dict[str, Any]]:
    body = _request("GET", "/subscription", params={"customer": customer_code})
    data = body.get("data")
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def create_subscription(
    *, customer_code: str, plan_code: str, authorization_code: str, start_date_iso: str | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "customer": customer_code,
        "plan": plan_code,
        "authorization": authorization_code,
    }
    if start_date_iso:
        payload["start_date"] = start_date_iso
    return _data(_request("POST", "/subscription", json_body=payload))


def disable_subscription(*, code: str, email_token: str) -> None:
    _request("POST", "/subscription/disable", json_body={"code": code, "token": email_token})
