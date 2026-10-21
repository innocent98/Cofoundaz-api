from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.errors import NotFound
from app.services.finance.payments.base import (
    Credentials,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderUnavailable,
)
from app.services.finance.payments.paystack import PaystackProvider
from app.services.finance.payments.registry import get_provider

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "payments"
KEY = "sk_test_placeh_olderkey"
CREDS = Credentials(secret_key=KEY, secret_hash=None)


def fx(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text())
    return data


def fx_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def provider(handler: Any) -> PaystackProvider:
    return PaystackProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_modes_and_prefixes() -> None:
    p = PaystackProvider()
    assert p.name == "paystack"
    assert p.key_prefixes == ("sk_test_", "sk_live_")
    assert p.mode_for("sk_test_x") == "test"
    assert p.mode_for("sk_live_x") == "live"


def test_registry() -> None:
    assert get_provider("paystack").name == "paystack"
    with pytest.raises(NotFound):
        get_provider("nope")


def test_verify_key_sends_bearer_and_returns_info() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["path"] = req.url.path
        return httpx.Response(200, json=fx("paystack_balance.json"))

    info = provider(handler).verify_key(CREDS)
    assert seen == {"auth": f"Bearer {KEY}", "path": "/balance"}
    assert info.account_name is None


def test_401_raises_auth_error_without_key() -> None:
    p = provider(lambda r: httpx.Response(401, json=fx("paystack_401.json")))
    with pytest.raises(ProviderAuthError) as ei:
        p.verify_key(CREDS)
    assert KEY not in str(ei.value) and "Bearer" not in str(ei.value)


def test_5xx_and_timeout_raise_unavailable() -> None:
    p = provider(lambda r: httpx.Response(500, json=fx("paystack_500.json")))
    with pytest.raises(ProviderUnavailable) as ei:
        p.verify_key(CREDS)
    assert KEY not in str(ei.value)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t", request=req)

    with pytest.raises(ProviderUnavailable) as ei2:
        provider(boom).verify_key(CREDS)
    assert KEY not in str(ei2.value)


def test_bad_json_raises_bad_response() -> None:
    p = provider(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(ProviderBadResponse):
        p.verify_key(CREDS)


def _list_handler(calls: list[httpx.Request]) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/transaction":
            name = (
                "paystack_transactions_page1.json"
                if req.url.params.get("page") == "1"
                else "paystack_transactions_page2.json"
            )
            return httpx.Response(200, json=fx(name))
        if req.url.path == "/refund":
            return httpx.Response(200, json=fx("paystack_refunds.json"))
        return httpx.Response(404)

    return handler


def test_list_transactions_maps_charges_fees_refunds_ascending() -> None:
    calls: list[httpx.Request] = []
    since = datetime(2026, 9, 1, tzinfo=UTC)
    items = provider(_list_handler(calls)).list_transactions(CREDS, since=since, max_pages=5)
    tx_calls = [c for c in calls if c.url.path == "/transaction"]
    assert [c.url.params["page"] for c in tx_calls] == ["1", "2"]
    assert tx_calls[0].url.params["status"] == "success"
    assert tx_calls[0].url.params["perPage"] == "100"
    assert "from" in tx_calls[0].url.params
    got = [(i.kind, i.reference, i.amount_minor, i.parent_reference) for i in items]
    assert got == [
        ("charge", "ref_alpha", 500000, None),
        ("fee", "ref_alpha:fee", 7500, "ref_alpha"),
        ("charge", "ref_beta", 2500000, None),
        ("refund", "7001", 200000, "ref_alpha"),
    ]
    assert all(isinstance(i.amount_minor, int) for i in items)
    assert all(i.currency == "NGN" for i in items)
    times = [i.occurred_at for i in items]
    assert times == sorted(times)
    assert "ref_failed" not in {i.reference for i in items}
    assert "7002" not in {i.reference for i in items}
    assert not any(i.reference == "ref_beta:fee" for i in items)


def test_list_transactions_respects_max_pages() -> None:
    calls: list[httpx.Request] = []
    provider(_list_handler(calls)).list_transactions(CREDS, since=None, max_pages=1)
    assert len([c for c in calls if c.url.path == "/transaction"]) == 1


def test_create_payment_link() -> None:
    body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body.update(json.loads(req.content))
        assert req.url.path == "/transaction/initialize"
        return httpx.Response(200, json=fx("paystack_initialize.json"))

    link = provider(handler).create_payment_link(
        CREDS,
        reference="inv_placeholder_ref",
        amount_minor=150000,
        email="a@example.test",
        currency="NGN",
        description="Invoice 1",
        redirect_url="https://app.example.test/done",
    )
    assert body["amount"] == 150000 and isinstance(body["amount"], int)
    assert body["reference"] == "inv_placeholder_ref"
    assert body["callback_url"] == "https://app.example.test/done"
    assert link.url == "https://checkout.paystack.com/fake_access_code"
    assert link.reference == "inv_placeholder_ref"


def _sig(raw: bytes, key: str = KEY) -> str:
    return hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()


def test_verify_webhook() -> None:
    p = PaystackProvider()
    raw = fx_bytes("paystack_webhook_charge_success.json")
    good = _sig(raw)
    assert p.verify_webhook(CREDS, raw_body=raw, headers={"x-paystack-signature": good})
    assert p.verify_webhook(CREDS, raw_body=raw, headers={"X-Paystack-Signature": good.upper()})
    assert not p.verify_webhook(
        CREDS, raw_body=raw, headers={"x-paystack-signature": _sig(raw, "sk_test_other")}
    )
    assert not p.verify_webhook(CREDS, raw_body=raw + b" ", headers={"x-paystack-signature": good})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={"x-paystack-signature": ""})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={"x-paystack-signature": "é中"})


def test_parse_webhook() -> None:
    p = PaystackProvider()
    ev = p.parse_webhook(fx_bytes("paystack_webhook_charge_success.json"))
    assert ev.event_id == "charge.success:1001"
    assert ev.kind == "charge"
    assert (ev.reference, ev.amount_minor, ev.currency, ev.fee_minor) == (
        "ref_alpha",
        500000,
        "NGN",
        7500,
    )
    assert ev.occurred_at == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    rf = p.parse_webhook(fx_bytes("paystack_webhook_refund_processed.json"))
    assert rf.kind == "refund" and rf.event_id == "refund.processed:7001"
    assert rf.amount_minor == 200000
    other = p.parse_webhook(fx_bytes("paystack_webhook_other.json"))
    assert other.kind == "other"
    with pytest.raises(ProviderBadResponse):
        p.parse_webhook(b"not json")
