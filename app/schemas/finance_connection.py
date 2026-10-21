import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ConnectRequest(BaseModel):
    secret_key: str = Field(min_length=1, max_length=200)
    secret_hash: str | None = Field(default=None, max_length=200)

    @field_validator("secret_key")
    @classmethod
    def _strip_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Enter your secret key.")
        return v

    @field_validator("secret_hash")
    @classmethod
    def _strip_hash(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class ConnectionResponse(BaseModel):
    id: uuid.UUID
    provider: str
    status: str
    mode: str
    key_last4: str
    account_name: str | None
    last_sync_at: datetime | None
    last_error: str | None
    last_sync_summary: dict[str, Any] | None
    webhook_url: str
    created_at: datetime


class ProviderEntry(BaseModel):
    provider: str
    label: str
    key_hint: str
    connection: ConnectionResponse | None


class IntegrationsResponse(BaseModel):
    encryption_configured: bool
    providers: list[ProviderEntry]
