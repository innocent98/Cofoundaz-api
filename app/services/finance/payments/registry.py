from __future__ import annotations

from app.core.errors import NotFound
from app.services.finance.payments.base import PaymentProvider, SyncSource
from app.services.finance.payments.flutterwave import FlutterwaveProvider
from app.services.finance.payments.mono import MonoProvider
from app.services.finance.payments.paystack import PaystackProvider


def get_provider(name: str) -> PaymentProvider:
    if name == "paystack":
        return PaystackProvider()
    if name == "flutterwave":
        return FlutterwaveProvider()
    raise NotFound()


def get_sync_source(name: str) -> SyncSource:
    """Payment providers plus the Mono bank feed: everything the ledger sync can read."""
    if name == "mono":
        return MonoProvider()
    return get_provider(name)
