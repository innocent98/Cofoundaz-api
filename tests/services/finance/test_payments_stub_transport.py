from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import settings
from app.services.finance.payments import flutterwave, paystack
from app.services.finance.payments.base import Credentials, ProviderAuthError
from app.services.finance.payments.http import http_client
from app.services.finance.payments.stub_transport import stub_transport


def test_http_client_uses_stub_only_outside_production(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER_STUB", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    with http_client() as c:
        assert isinstance(c._transport, httpx.MockTransport)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with http_client() as c:
        assert not isinstance(c._transport, httpx.MockTransport)


def test_paystack_through_stub():
    p = paystack.PaystackProvider(httpx.Client(transport=stub_transport()))
    creds = Credentials("sk_test_good_goodgood")
    p.verify_key(creds)
    items = p.list_transactions(creds, since=None, max_pages=3)
    assert {i.kind: i.amount_minor for i in items} == {"charge": 500000, "fee": 7500}
    link = p.create_payment_link(
        creds,
        reference="inv_1",
        amount_minor=1000,
        email="a@b.co",
        currency="NGN",
        description="d",
        redirect_url="https://x.test",
    )
    assert link.reference == "inv_1" and link.url.startswith("https://")
    with pytest.raises(ProviderAuthError):
        p.verify_key(Credentials("sk_test_b_adbadbad"))


def test_flutterwave_through_stub():
    f = flutterwave.FlutterwaveProvider(httpx.Client(transport=stub_transport()))
    creds = Credentials("FLWSECK_TEST-good", "h")
    f.verify_key(creds)
    items = f.list_transactions(creds, since=datetime(2026, 1, 1, tzinfo=UTC), max_pages=3)
    assert {i.kind: i.amount_minor for i in items} == {"charge": 500000, "fee": 7550}
    link = f.create_payment_link(
        creds,
        reference="inv_2",
        amount_minor=250000,
        email="a@b.co",
        currency="NGN",
        description="d",
        redirect_url="https://x.test",
    )
    assert link.url.startswith("https://")
    with pytest.raises(ProviderAuthError):
        f.verify_key(Credentials("FLWSECK_TEST-bad", "h"))


def test_flutterwave_verify_through_stub_echoes_the_latest_created_link():
    f = flutterwave.FlutterwaveProvider(httpx.Client(transport=stub_transport()))
    creds = Credentials("FLWSECK_TEST-good", "h")
    f.create_payment_link(
        creds,
        reference="inv_verify",
        amount_minor=250000,
        email="a@b.co",
        currency="NGN",
        description="d",
        redirect_url="https://x.test",
    )
    ev = f.verify_transaction(creds, transaction_id="777")
    assert ev is not None
    assert (ev.reference, ev.amount_minor, ev.currency) == ("inv_verify", 250000, "NGN")
    with pytest.raises(ProviderAuthError):
        f.verify_transaction(Credentials("FLWSECK_TEST-bad", "h"), transaction_id="777")
