from __future__ import annotations

import httpx

from app.core.config import settings

TIMEOUT_SECONDS = 15.0


def http_client() -> httpx.Client:
    if settings.PAYMENT_PROVIDER_STUB and settings.ENVIRONMENT != "production":
        from app.services.finance.payments.stub_transport import stub_transport

        return httpx.Client(transport=stub_transport(), timeout=TIMEOUT_SECONDS)
    return httpx.Client(timeout=TIMEOUT_SECONDS)
