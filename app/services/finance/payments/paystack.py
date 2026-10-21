from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.services.finance.payments.base import (
    AccountInfo,
    Credentials,
    PaymentLink,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderTransaction,
    ProviderUnavailable,
    WebhookEvent,
)
from app.services.finance.payments.http import http_client

BASE_URL = "https://api.paystack.co"
PER_PAGE = 100


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


class PaystackProvider:
    name = "paystack"
    key_prefixes: tuple[str, ...] = ("sk_test_", "sk_live_")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def mode_for(self, key: str) -> Literal["test", "live"]:
        return "live" if key.startswith("sk_live_") else "test"

    def _request(
        self,
        creds: Credentials,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._client or http_client()
        try:
            resp = client.request(
                method,
                BASE_URL + path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {creds.secret_key}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Paystack request failed ({type(exc).__name__}).") from None
        finally:
            if self._client is None:
                client.close()
        if resp.status_code in (401, 403):
            raise ProviderAuthError("Paystack rejected the secret key.")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"Paystack returned {resp.status_code}.")
        if resp.status_code >= 400:
            raise ProviderBadResponse(f"Paystack returned {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError:
            raise ProviderBadResponse("Paystack returned a non-JSON body.") from None
        if not isinstance(data, dict):
            raise ProviderBadResponse("Paystack returned an unexpected body.")
        return data

    def verify_key(self, creds: Credentials) -> AccountInfo:
        self._request(creds, "GET", "/balance")
        return AccountInfo(account_name=None)

    def _paged(
        self, creds: Credentials, path: str, base_params: dict[str, Any], max_pages: int
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            body = self._request(creds, "GET", path, params={**base_params, "page": page})
            data = body.get("data")
            if not isinstance(data, list):
                raise ProviderBadResponse("Paystack returned an unexpected list body.")
            rows.extend(r for r in data if isinstance(r, dict))
            meta = body.get("meta")
            page_count = _int(meta.get("pageCount")) if isinstance(meta, dict) else None
            if page_count is None or page >= page_count:
                break
            page += 1
        return rows

    def list_transactions(
        self, creds: Credentials, *, since: datetime | None, max_pages: int
    ) -> list[ProviderTransaction]:
        params: dict[str, Any] = {"status": "success", "perPage": PER_PAGE}
        if since is not None:
            params["from"] = since.astimezone(UTC).isoformat()
        out: list[ProviderTransaction] = []
        for row in self._paged(creds, "/transaction", params, max_pages):
            if row.get("status") != "success":
                continue
            ref = row.get("reference")
            amount = _int(row.get("amount"))
            when = _parse_dt(row.get("paid_at")) or _parse_dt(row.get("created_at"))
            if not isinstance(ref, str) or amount is None or when is None:
                continue
            currency = str(row.get("currency") or "")
            out.append(
                ProviderTransaction(
                    "charge", ref, amount, currency, when, f"Paystack payment {ref}", None
                )
            )
            fee = _int(row.get("fees"))
            if fee:
                out.append(
                    ProviderTransaction(
                        "fee", f"{ref}:fee", fee, currency, when, f"Paystack fee for {ref}", ref
                    )
                )
        rparams: dict[str, Any] = {"perPage": PER_PAGE}
        if since is not None:
            rparams["from"] = since.astimezone(UTC).isoformat()
        for row in self._paged(creds, "/refund", rparams, max_pages):
            if row.get("status") != "processed":
                continue
            rid = row.get("id")
            amount = _int(row.get("amount"))
            when = _parse_dt(row.get("refunded_at")) or _parse_dt(row.get("createdAt"))
            parent = row.get("transaction_reference")
            if rid is None or amount is None or when is None:
                continue
            out.append(
                ProviderTransaction(
                    "refund",
                    str(rid),
                    amount,
                    str(row.get("currency") or ""),
                    when,
                    f"Paystack refund {rid}",
                    parent if isinstance(parent, str) else None,
                )
            )
        out.sort(key=lambda t: t.occurred_at)
        return out

    def create_payment_link(
        self,
        creds: Credentials,
        *,
        reference: str,
        amount_minor: int,
        email: str,
        currency: str,
        description: str,
        redirect_url: str,
    ) -> PaymentLink:
        body = self._request(
            creds,
            "POST",
            "/transaction/initialize",
            json_body={
                "reference": reference,
                "amount": int(amount_minor),
                "email": email,
                "currency": currency,
                "callback_url": redirect_url,
                "metadata": {"description": description},
            },
        )
        data = body.get("data")
        url = data.get("authorization_url") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url:
            raise ProviderBadResponse("Paystack did not return a payment link.")
        ref = data.get("reference") if isinstance(data, dict) else None
        return PaymentLink(url=url, reference=ref if isinstance(ref, str) else reference)

    def verify_webhook(
        self, creds: Credentials, *, raw_body: bytes, headers: Mapping[str, str]
    ) -> bool:
        provided: str | None = None
        for k, v in headers.items():
            if k.lower() == "x-paystack-signature":
                provided = v
                break
        if not provided:
            return False
        try:
            provided_bytes = provided.strip().lower().encode("ascii")
        except UnicodeEncodeError:
            return False
        expected = hmac.new(creds.secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(provided_bytes, expected.encode("ascii"))

    def parse_webhook(self, raw_body: bytes) -> WebhookEvent:
        try:
            payload = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            raise ProviderBadResponse("Webhook body is not valid JSON.") from None
        if not isinstance(payload, dict):
            raise ProviderBadResponse("Webhook body is not an object.")
        event = str(payload.get("event") or "")
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        data_id = data.get("id")
        event_id = f"{event}:{data_id}"
        if event == "charge.success":
            ref = data.get("reference")
            return WebhookEvent(
                event_id,
                "charge",
                ref if isinstance(ref, str) else None,
                _int(data.get("amount")),
                str(data["currency"]) if data.get("currency") else None,
                _int(data.get("fees")),
                _parse_dt(data.get("paid_at")),
            )
        if event == "refund.processed":
            ref = data.get("transaction_reference")
            return WebhookEvent(
                event_id,
                "refund",
                ref if isinstance(ref, str) else None,
                _int(data.get("amount")),
                str(data["currency"]) if data.get("currency") else None,
                None,
                _parse_dt(data.get("refunded_at")) or _parse_dt(data.get("createdAt")),
            )
        return WebhookEvent(event_id, "other", None, None, None, None, None)
