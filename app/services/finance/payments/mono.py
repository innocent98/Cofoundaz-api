"""Mono (open banking) adapter: link a bank account, read its transactions.

Unlike Paystack and Flutterwave there is no founder-pasted key. The Cofoundaz Mono app's secret
key (MONO_SECRET_KEY, environment) authenticates every call; the founder links their bank in
Mono Connect (the FE widget, using the public key) and hands us the one-time `code`, which is
exchanged for the linked account id. That id is the per-founder credential, so it is the value
stored encrypted. Sandbox only unless MONO_ALLOW_LIVE (connections.connect_mono enforces it).

Request/response shapes follow Mono Connect v2 as documented; they are exercised here against
mocked HTTP and the in-process stub only, never against the live sandbox (see the FE guide).
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.services.finance.payments.base import (
    Credentials,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderTransaction,
    ProviderUnavailable,
)
from app.services.finance.payments.http import http_client

BASE_URL = "https://api.withmono.com"
PER_PAGE = 100
SANDBOX_KEY_PREFIX = "test_sk_"
WEBHOOK_SECRET_HEADER = "mono-webhook-secret"  # nosec B105 - a header name, not a secret


class MonoAccount:
    """What the account lookup tells us (never the full account number)."""

    def __init__(
        self, *, name: str | None, institution: str | None, last4: str | None, currency: str
    ) -> None:
        self.name = name
        self.institution = institution
        self.last4 = last4
        self.currency = currency

    @property
    def label(self) -> str | None:
        parts = [p for p in (self.institution, self.name) if p]
        return " · ".join(parts)[:200] if parts else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _minor(value: Any) -> int | None:
    """Mono reports amounts as whole minor units (kobo)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class MonoProvider:
    name = "mono"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    @staticmethod
    def mode_for(key: str) -> Literal["test", "live"]:
        return "test" if key.startswith(SANDBOX_KEY_PREFIX) else "live"

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
                headers={"mono-sec-key": creds.secret_key, "accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Mono request failed ({type(exc).__name__}).") from None
        finally:
            if self._client is None:
                client.close()
        if resp.status_code in (401, 403):
            raise ProviderAuthError("Mono rejected the request credentials.")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"Mono returned {resp.status_code}.")
        if resp.status_code >= 400:
            raise ProviderBadResponse(f"Mono returned {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError:
            raise ProviderBadResponse("Mono returned a non-JSON body.") from None
        if not isinstance(data, dict):
            raise ProviderBadResponse("Mono returned an unexpected body.")
        return data

    def exchange_code(self, creds: Credentials, code: str) -> str:
        """Swap the one-time Mono Connect code for the linked account id."""
        body = self._request(creds, "POST", "/v2/accounts/auth", json_body={"code": code})
        data = body.get("data")
        account_id = _str(data.get("id")) if isinstance(data, dict) else None
        if account_id is None:
            raise ProviderBadResponse("Mono did not return an account id.")
        return account_id

    def account_info(self, creds: Credentials) -> MonoAccount:
        if not creds.account_id:
            raise ProviderBadResponse("No account to look up.")
        body = self._request(creds, "GET", f"/v2/accounts/{creds.account_id}")
        data = body.get("data")
        account = data.get("account") if isinstance(data, dict) else None
        if not isinstance(account, dict):
            raise ProviderBadResponse("Mono returned an unexpected account body.")
        institution = account.get("institution")
        number = _str(account.get("accountNumber"))
        return MonoAccount(
            name=_str(account.get("name")),
            institution=_str(institution.get("name")) if isinstance(institution, dict) else None,
            last4=number[-4:] if number and len(number) >= 4 else None,
            currency=_str(account.get("currency")) or "NGN",
        )

    def list_transactions(
        self, creds: Credentials, *, since: datetime | None, max_pages: int
    ) -> list[ProviderTransaction]:
        currency = self.account_info(creds).currency
        params: dict[str, Any] = {"paginate": "true", "limit": PER_PAGE}
        if since is not None:
            params["start"] = since.astimezone(UTC).strftime("%d-%m-%Y")
        out: list[ProviderTransaction] = []
        page = 1
        while page <= max_pages:
            body = self._request(
                creds,
                "GET",
                f"/v2/accounts/{creds.account_id}/transactions",
                params={**params, "page": page},
            )
            data = body.get("data")
            if not isinstance(data, list):
                raise ProviderBadResponse("Mono returned an unexpected list body.")
            for row in data:
                item = self._transaction(row, currency)
                if item is not None:
                    out.append(item)
            meta = body.get("meta")
            if not data or not (isinstance(meta, dict) and meta.get("next")):
                break
            page += 1
        out.sort(key=lambda t: t.occurred_at)
        return out

    @staticmethod
    def _transaction(row: Any, currency: str) -> ProviderTransaction | None:
        if not isinstance(row, dict):
            return None
        ref = row.get("id")
        amount = _minor(row.get("amount"))
        when = _parse_dt(row.get("date"))
        kind = {"credit": "credit", "debit": "debit"}.get(str(row.get("type")))
        if not isinstance(ref, str) or not ref or amount is None or when is None or kind is None:
            return None
        narration = _str(row.get("narration")) or "Bank transaction"
        return ProviderTransaction(
            kind,  # type: ignore[arg-type]
            ref,
            amount,
            currency,
            when,
            narration,
            None,
        )

    @staticmethod
    def verify_webhook(secret: str | None, *, headers: Mapping[str, str]) -> bool:
        """Mono sends the secret configured on the dashboard in `mono-webhook-secret`.

        Fails closed: no configured secret, or no header, is never valid.
        """
        if not secret:
            return False
        provided: str | None = None
        for k, v in headers.items():
            if k.lower() == WEBHOOK_SECRET_HEADER:
                provided = v
                break
        if not provided:
            return False
        return hmac.compare_digest(provided.strip().encode("utf-8"), secret.encode("utf-8"))
