from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import quote

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

BASE_URL = "https://api.flutterwave.com/v3"
_CENT = Decimal("0.01")


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_minor(value: Any) -> int | None:
    """Major-unit amount (naira) to integer kobo via Decimal. None when unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, (int, str)):
        try:
            dec = Decimal(value)
        except InvalidOperation:
            return None
    elif isinstance(value, float):
        dec = Decimal(repr(value))
    else:
        return None
    if not dec.is_finite():
        return None
    return int((dec.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())


def _transaction_id(value: Any) -> str | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    return str(value) or None


def _minor_to_major_str(minor: int) -> str:
    return str((Decimal(int(minor)) / 100).quantize(_CENT))


def _loads(raw: bytes | str) -> Any:
    return json.loads(raw, parse_float=Decimal)


class FlutterwaveProvider:
    name = "flutterwave"
    key_prefixes: tuple[str, ...] = ("FLWSECK_TEST-", "FLWSECK-")

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def mode_for(self, key: str) -> Literal["test", "live"]:
        return "test" if key.startswith("FLWSECK_TEST-") else "live"

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
            raise ProviderUnavailable(
                f"Flutterwave request failed ({type(exc).__name__})."
            ) from None
        finally:
            if self._client is None:
                client.close()
        if resp.status_code in (401, 403):
            raise ProviderAuthError("Flutterwave rejected the secret key.")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"Flutterwave returned {resp.status_code}.")
        if resp.status_code >= 400:
            raise ProviderBadResponse(f"Flutterwave returned {resp.status_code}.")
        try:
            data = _loads(resp.content)
        except (ValueError, UnicodeDecodeError):
            raise ProviderBadResponse("Flutterwave returned a non-JSON body.") from None
        if not isinstance(data, dict):
            raise ProviderBadResponse("Flutterwave returned an unexpected body.")
        return data

    def verify_key(self, creds: Credentials) -> AccountInfo:
        self._request(creds, "GET", "/balances")
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
                raise ProviderBadResponse("Flutterwave returned an unexpected list body.")
            rows.extend(r for r in data if isinstance(r, dict))
            meta = body.get("meta")
            info = meta.get("page_info") if isinstance(meta, dict) else None
            total = info.get("total_pages") if isinstance(info, dict) else None
            if isinstance(total, bool) or not isinstance(total, int) or page >= total:
                break
            page += 1
        return rows

    def list_transactions(
        self, creds: Credentials, *, since: datetime | None, max_pages: int
    ) -> list[ProviderTransaction]:
        params: dict[str, Any] = {"status": "successful"}
        rparams: dict[str, Any] = {}
        if since is not None:
            day = since.astimezone(UTC).date().isoformat()
            params["from"] = day
            rparams["from"] = day
        out: list[ProviderTransaction] = []
        for row in self._paged(creds, "/transactions", params, max_pages):
            if row.get("status") != "successful":
                continue
            ref = row.get("tx_ref")
            amount = to_minor(row.get("amount"))
            when = _parse_dt(row.get("created_at"))
            if not isinstance(ref, str) or not ref or amount is None or when is None:
                continue
            currency = str(row.get("currency") or "")
            out.append(
                ProviderTransaction(
                    "charge", ref, amount, currency, when, f"Flutterwave payment {ref}", None
                )
            )
            fee = to_minor(row.get("app_fee"))
            if fee:
                out.append(
                    ProviderTransaction(
                        "fee",
                        f"{ref}:fee",
                        fee,
                        currency,
                        when,
                        f"Flutterwave fee for {ref}",
                        ref,
                    )
                )
        for row in self._paged(creds, "/refunds", rparams, max_pages):
            if row.get("status") != "completed":
                continue
            rid = row.get("id")
            amount = to_minor(row.get("amount_refunded"))
            when = _parse_dt(row.get("created_at"))
            parent = row.get("tx_ref")
            if rid is None or isinstance(rid, bool) or amount is None or when is None:
                continue
            out.append(
                ProviderTransaction(
                    "refund",
                    str(rid),
                    amount,
                    str(row.get("currency") or ""),
                    when,
                    f"Flutterwave refund {rid}",
                    parent if isinstance(parent, str) and parent else None,
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
            "/payments",
            json_body={
                "tx_ref": reference,
                "amount": _minor_to_major_str(amount_minor),
                "currency": currency,
                "redirect_url": redirect_url,
                "customer": {"email": email},
                "customizations": {"title": description},
            },
        )
        data = body.get("data")
        url = data.get("link") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url:
            raise ProviderBadResponse("Flutterwave did not return a payment link.")
        return PaymentLink(url=url, reference=reference)

    def verify_webhook(
        self, creds: Credentials, *, raw_body: bytes, headers: Mapping[str, str]
    ) -> bool:
        expected = creds.secret_hash
        if not expected:
            return False
        provided: str | None = None
        for k, v in headers.items():
            if k.lower() == "verif-hash":
                provided = v
                break
        if not provided:
            return False
        try:
            return hmac.compare_digest(provided.encode("ascii"), expected.encode("ascii"))
        except UnicodeEncodeError:
            return False

    def parse_webhook(self, raw_body: bytes) -> WebhookEvent:
        try:
            payload = _loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            raise ProviderBadResponse("Webhook body is not valid JSON.") from None
        if not isinstance(payload, dict):
            raise ProviderBadResponse("Webhook body is not an object.")
        event = str(payload.get("event") or "")
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        event_id = f"{event}:{data.get('id')}"
        if event == "charge.completed" and data.get("status") == "successful":
            ref = data.get("tx_ref")
            return WebhookEvent(
                event_id,
                "charge",
                ref if isinstance(ref, str) else None,
                to_minor(data.get("amount")),
                str(data["currency"]) if data.get("currency") else None,
                to_minor(data.get("app_fee")),
                _parse_dt(data.get("created_at")),
                _transaction_id(data.get("id")),
            )
        return WebhookEvent(event_id, "other", None, None, None, None, None)

    def verify_transaction(self, creds: Credentials, *, transaction_id: str) -> WebhookEvent | None:
        """Ask Flutterwave whether this transaction really succeeded.

        `verif-hash` is a static shared secret, not a per-body signature, so a webhook alone
        proves nothing. Returns the API's own view as a charge event (reference, amount,
        currency, fee, time all from the API), or None when the API does not show a successful
        transaction with this id. Raises ProviderAuthError / ProviderUnavailable /
        ProviderBadResponse (a 404 or other 4xx) from the transport.
        """
        body = self._request(creds, "GET", f"/transactions/{quote(transaction_id, safe='')}/verify")
        data = body.get("data")
        if body.get("status") != "success" or not isinstance(data, dict):
            return None
        if data.get("status") != "successful" or _transaction_id(data.get("id")) != transaction_id:
            return None
        ref = data.get("tx_ref")
        amount = to_minor(data.get("amount"))
        if not isinstance(ref, str) or not ref or amount is None or not data.get("currency"):
            return None
        return WebhookEvent(
            f"charge.completed:{transaction_id}",
            "charge",
            ref,
            amount,
            str(data["currency"]),
            to_minor(data.get("app_fee")),
            _parse_dt(data.get("created_at")),
            transaction_id,
        )
