from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


class ProviderAuthError(Exception):
    """The provider rejected the credentials (401/403). Never embeds keys."""


class ProviderUnavailable(Exception):  # noqa: N818
    """Timeout, network failure or provider 5xx. Never embeds keys."""


class ProviderBadResponse(Exception):  # noqa: N818
    """The provider answered with something we cannot interpret."""


@dataclass(frozen=True)
class Credentials:
    secret_key: str
    secret_hash: str | None = None

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"


@dataclass(frozen=True)
class AccountInfo:
    account_name: str | None


@dataclass(frozen=True)
class ProviderTransaction:
    kind: Literal["charge", "fee", "refund"]
    reference: str
    amount_minor: int
    currency: str
    occurred_at: datetime
    description: str
    parent_reference: str | None


@dataclass(frozen=True)
class PaymentLink:
    url: str
    reference: str


@dataclass(frozen=True)
class WebhookEvent:
    event_id: str
    kind: Literal["charge", "refund", "other"]
    reference: str | None
    amount_minor: int | None
    currency: str | None
    fee_minor: int | None
    occurred_at: datetime | None
    transaction_id: str | None = None


class PaymentProvider(Protocol):
    name: str
    key_prefixes: tuple[str, ...]

    def mode_for(self, key: str) -> Literal["test", "live"]: ...

    def verify_key(self, creds: Credentials) -> AccountInfo: ...

    def list_transactions(
        self, creds: Credentials, *, since: datetime | None, max_pages: int
    ) -> list[ProviderTransaction]: ...

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
    ) -> PaymentLink: ...

    def verify_webhook(
        self, creds: Credentials, *, raw_body: bytes, headers: Mapping[str, str]
    ) -> bool: ...

    def parse_webhook(self, raw_body: bytes) -> WebhookEvent: ...
