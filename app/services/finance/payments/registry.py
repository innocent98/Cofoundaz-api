from __future__ import annotations

from app.core.errors import NotFound
from app.services.finance.payments.base import PaymentProvider
from app.services.finance.payments.flutterwave import FlutterwaveProvider
from app.services.finance.payments.paystack import PaystackProvider


def get_provider(name: str) -> PaymentProvider:
    if name == "paystack":
        return PaystackProvider()
    if name == "flutterwave":
        return FlutterwaveProvider()
    raise NotFound()
