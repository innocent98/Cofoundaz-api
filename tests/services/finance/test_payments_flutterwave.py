from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.finance.payments.base import (
    Credentials,
    ProviderAuthError,
    ProviderBadResponse,
    ProviderUnavailable,
)
from app.services.finance.payments.flutterwave import FlutterwaveProvider, to_minor
from app.services.finance.payments.registry import get_provider

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "payments"
KEY = "FLWSECK_TEST-placeh_olderkey-X"
HASH = "placeholder-secret-hash"
CREDS = Credentials(secret_key=KEY, secret_hash=HASH)


def fx(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text())
    return data


def fx_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def provider(handler: Any) -> FlutterwaveProvider:
    return FlutterwaveProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_modes_and_prefixes() -> None:
    p = FlutterwaveProvider()
    assert p.name == "flutterwave"
    assert p.key_prefixes == ("FLWSECK_TEST-", "FLWSECK-")
    assert p.mode_for("FLWSECK_TEST-x") == "test"
    assert p.mode_for("FLWSECK-x") == "live"


def test_registry() -> None:
    assert get_provider("flutterwave").name == "flutterwave"


def test_to_minor_uses_decimal() -> None:
    from decimal import Decimal

    assert to_minor(Decimal("1234.56")) == 123456
    assert to_minor(5000) == 500000
    assert to_minor(75.5) == 7550
    assert to_minor("0.07") == 7
    assert to_minor(1234.56) == 123456
    assert to_minor(True) is None
    assert to_minor(None) is None
    assert to_minor("abc") is None
    assert to_minor(float("nan")) is None


def test_verify_key_sends_bearer() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["path"] = req.url.path
        return httpx.Response(200, json=fx("flutterwave_balances.json"))

    info = provider(handler).verify_key(CREDS)
    assert seen == {"auth": f"Bearer {KEY}", "path": "/v3/balances"}
    assert info.account_name is None


def test_401_raises_auth_error_without_key() -> None:
    p = provider(lambda r: httpx.Response(401, json=fx("flutterwave_401.json")))
    with pytest.raises(ProviderAuthError) as ei:
        p.verify_key(CREDS)
    assert KEY not in str(ei.value) and "Bearer" not in str(ei.value)


def test_5xx_and_timeout_raise_unavailable() -> None:
    p = provider(lambda r: httpx.Response(500, json=fx("flutterwave_500.json")))
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
        if req.url.path == "/v3/transactions":
            name = (
                "flutterwave_transactions_page1.json"
                if req.url.params.get("page") == "1"
                else "flutterwave_transactions_page2.json"
            )
            return httpx.Response(200, json=fx(name))
        if req.url.path == "/v3/refunds":
            return httpx.Response(200, json=fx("flutterwave_refunds.json"))
        return httpx.Response(404)

    return handler


def test_list_transactions_maps_major_units_to_kobo_ascending() -> None:
    calls: list[httpx.Request] = []
    since = datetime(2026, 9, 1, tzinfo=UTC)
    items = provider(_list_handler(calls)).list_transactions(CREDS, since=since, max_pages=5)
    tx_calls = [c for c in calls if c.url.path == "/v3/transactions"]
    assert [c.url.params["page"] for c in tx_calls] == ["1", "2"]
    assert tx_calls[0].url.params["status"] == "successful"
    assert tx_calls[0].url.params["from"] == "2026-09-01"
    got = [(i.kind, i.reference, i.amount_minor, i.parent_reference) for i in items]
    assert got == [
        ("charge", "tx_alpha", 500000, None),
        ("fee", "tx_alpha:fee", 7550, "tx_alpha"),
        ("charge", "tx_beta", 123456, None),
        ("charge", "tx_usd", 2000, None),
        ("fee", "tx_usd:fee", 100, "tx_usd"),
        ("refund", "9001", 200000, "tx_alpha"),
    ]
    assert all(isinstance(i.amount_minor, int) for i in items)
    assert [i.currency for i in items if i.reference.startswith("tx_usd")] == ["USD", "USD"]
    times = [i.occurred_at for i in items]
    assert times == sorted(times)
    refs = {i.reference for i in items}
    assert "tx_failed" not in refs and "9002" not in refs
    assert "tx_beta:fee" not in refs


def test_list_transactions_respects_max_pages() -> None:
    calls: list[httpx.Request] = []
    provider(_list_handler(calls)).list_transactions(CREDS, since=None, max_pages=1)
    assert len([c for c in calls if c.url.path == "/v3/transactions"]) == 1


def test_create_payment_link() -> None:
    body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body.update(json.loads(req.content))
        assert req.url.path == "/v3/payments"
        return httpx.Response(200, json=fx("flutterwave_payments.json"))

    link = provider(handler).create_payment_link(
        CREDS,
        reference="inv_placeholder_ref",
        amount_minor=150050,
        email="a@example.test",
        currency="NGN",
        description="Invoice 1",
        redirect_url="https://app.example.test/done",
    )
    assert body["amount"] == "1500.50"
    assert body["tx_ref"] == "inv_placeholder_ref"
    assert body["redirect_url"] == "https://app.example.test/done"
    assert body["currency"] == "NGN"
    assert body["customer"] == {"email": "a@example.test"}
    assert link.url == "https://checkout.flutterwave.com/v3/hosted/pay/placeholderlink"
    assert link.reference == "inv_placeholder_ref"


def test_create_payment_link_without_link_is_bad_response() -> None:
    p = provider(lambda r: httpx.Response(200, json={"status": "success", "data": {}}))
    with pytest.raises(ProviderBadResponse):
        p.create_payment_link(
            CREDS,
            reference="r",
            amount_minor=100,
            email="a@example.test",
            currency="NGN",
            description="d",
            redirect_url="https://app.example.test/done",
        )


def test_verify_webhook() -> None:
    p = FlutterwaveProvider()
    raw = fx_bytes("flutterwave_webhook_charge_completed.json")
    assert p.verify_webhook(CREDS, raw_body=raw, headers={"verif-hash": HASH})
    assert p.verify_webhook(CREDS, raw_body=raw, headers={"Verif-Hash": HASH})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={"verif-hash": "other"})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={"verif-hash": ""})
    assert not p.verify_webhook(CREDS, raw_body=raw, headers={"verif-hash": "é中"})
    for empty in (None, ""):
        c = Credentials(secret_key=KEY, secret_hash=empty)
        assert not p.verify_webhook(c, raw_body=raw, headers={"verif-hash": ""})
        assert not p.verify_webhook(c, raw_body=raw, headers={"verif-hash": "x"})
    non_ascii = Credentials(secret_key=KEY, secret_hash="é中")
    assert not p.verify_webhook(non_ascii, raw_body=raw, headers={"verif-hash": "é中"})


def test_parse_webhook() -> None:
    p = FlutterwaveProvider()
    ev = p.parse_webhook(fx_bytes("flutterwave_webhook_charge_completed.json"))
    assert ev.event_id == "charge.completed:2001"
    assert ev.kind == "charge"
    assert (ev.reference, ev.amount_minor, ev.currency, ev.fee_minor) == (
        "tx_alpha",
        500000,
        "NGN",
        7550,
    )
    assert ev.occurred_at == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    failed = p.parse_webhook(fx_bytes("flutterwave_webhook_charge_failed.json"))
    assert failed.kind == "other"
    other = p.parse_webhook(fx_bytes("flutterwave_webhook_other.json"))
    assert other.kind == "other"
    with pytest.raises(ProviderBadResponse):
        p.parse_webhook(b"not json")
    with pytest.raises(ProviderBadResponse):
        p.parse_webhook(b"[1]")
