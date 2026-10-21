import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.config import settings
from app.db.models.audit import AuditLog
from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.finance import Transaction
from app.db.models.finance_connection import FinanceConnection
from app.services.finance.payments import flutterwave, paystack
from tests.api.test_finance import BASE, _member

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "payments"
URL = f"{BASE}/integrations"
ALL_NON_FINANCE = [
    MembershipRole.mentor,
    MembershipRole.investor,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
]
PS_KEY = "sk_test_fake_fake_fake1234"
FLW_KEY = "FLWSECK_TEST-fake_fake_fake5678-X"
FLW_HASH = "placeholder-hash-value"


def _fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def _mock_handler(request: httpx.Request) -> httpx.Response:
    auth = request.headers.get("authorization", "")
    is_ps = request.url.host == "api.paystack.co"
    if "bad" in auth:
        return httpx.Response(
            401, content=_fx("paystack_401.json" if is_ps else "flutterwave_401.json")
        )
    if "down" in auth:
        return httpx.Response(
            500, content=_fx("paystack_500.json" if is_ps else "flutterwave_500.json")
        )
    return httpx.Response(
        200, content=_fx("paystack_balance.json" if is_ps else "flutterwave_balances.json")
    )


@pytest.fixture(autouse=True)
def _provider_http(monkeypatch):
    def factory():
        return httpx.Client(transport=httpx.MockTransport(_mock_handler))

    monkeypatch.setattr(paystack, "http_client", factory)
    monkeypatch.setattr(flutterwave, "http_client", factory)
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


def _connect_ps(client, h, key=PS_KEY):
    return client.post(f"{URL}/paystack/connect", json={"secret_key": key}, headers=h)


def _connect_flw(client, h, key=FLW_KEY, secret_hash=FLW_HASH):
    return client.post(
        f"{URL}/flutterwave/connect",
        json={"secret_key": key, "secret_hash": secret_hash},
        headers=h,
    )


def test_founder_connects_paystack_without_leaking_secret(client, db, caplog):
    _u, s, h = _member(db)
    with caplog.at_level(logging.DEBUG):
        resp = _connect_ps(client, h)
        listed = client.get(URL, headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["provider"] == "paystack"
    assert data["status"] == "active"
    assert data["mode"] == "test"
    assert data["key_last4"] == PS_KEY[-4:]
    assert "/webhooks/payments/paystack/" in data["webhook_url"]
    assert "secret_key" not in data and "key_ciphertext" not in data
    assert PS_KEY not in resp.text
    assert PS_KEY not in listed.text
    assert PS_KEY not in caplog.text

    row = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    assert row.key_ciphertext.startswith("gAAAA")
    assert PS_KEY not in row.key_ciphertext
    audits = db.query(AuditLog).filter(AuditLog.startup_id == s.id).all()
    assert [a.action for a in audits] == ["finance.connection.connected"]
    dump = db.execute(text("select * from audit_log")).fetchall()
    assert PS_KEY not in repr(dump)
    assert listed.json()["data"]["encryption_configured"] is True


def test_flutterwave_connect_encrypts_hash(client, db):
    _u, s, h = _member(db)
    resp = _connect_flw(client, h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["mode"] == "test"
    assert FLW_HASH not in resp.text and FLW_KEY not in resp.text
    row = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    assert row.secret_hash_ciphertext.startswith("gAAAA")


def test_list_shows_both_providers(client, db):
    _u, _s, h = _member(db)
    _connect_ps(client, h)
    body = client.get(URL, headers=h).json()["data"]
    by = {p["provider"]: p for p in body["providers"]}
    assert set(by) == {"paystack", "flutterwave"}
    assert by["paystack"]["connection"]["key_last4"] == PS_KEY[-4:]
    assert by["flutterwave"]["connection"] is None
    assert by["paystack"]["label"] == "Paystack"


def test_replace_keeps_row_and_audits(client, db):
    _u, s, h = _member(db)
    first = _connect_ps(client, h).json()["data"]
    second = _connect_ps(client, h, key="sk_live_zzzzzzzz_zzzz9999")
    assert second.status_code == 200, second.text
    d = second.json()["data"]
    assert d["id"] == first["id"]
    assert d["mode"] == "live" and d["key_last4"] == "9999"
    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.startup_id == s.id)]
    assert sorted(actions) == ["finance.connection.connected", "finance.connection.replaced"]


def test_disconnect_wipes_secrets_keeps_rows_and_reconnect_reuses_row(client, db):
    _u, s, h = _member(db)
    first = _connect_ps(client, h).json()["data"]
    conn = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    db.add(
        Transaction(
            startup_id=s.id,
            date=date(2026, 10, 1),
            description="Imported",
            amount_minor=1000,
            currency="NGN",
            direction=TransactionDirection.inflow,
            source="paystack",
            connection_id=conn.id,
            external_ref="r1",
        )
    )
    db.flush()

    dc = client.post(f"{URL}/paystack/disconnect", headers=h)
    assert dc.status_code == 200, dc.text
    assert dc.json()["data"]["status"] == "disconnected"
    db.expire_all()
    conn = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    assert conn.key_ciphertext is None and conn.secret_hash_ciphertext is None
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 1
    listed = client.get(URL, headers=h).json()["data"]["providers"]
    assert listed[0]["connection"] is None

    again = _connect_ps(client, h).json()["data"]
    assert again["id"] == first["id"]
    assert again["webhook_url"] != first["webhook_url"]
    assert client.post(f"{URL}/paystack/disconnect", headers=h).status_code == 200
    assert client.post(f"{URL}/paystack/disconnect", headers=h).status_code == 404


def test_other_provider_active_is_409(client, db):
    _u, _s, h = _member(db)
    _connect_ps(client, h)
    resp = _connect_flw(client, h)
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "PAYMENT_PROVIDER_ALREADY_CONNECTED"
    assert err["message"] == "Disconnect Paystack before connecting Flutterwave."


def test_wrong_prefix_rejected_before_network(client, db, monkeypatch):
    def boom():
        raise AssertionError("network used")

    monkeypatch.setattr(paystack, "http_client", boom)
    _u, _s, h = _member(db)
    resp = _connect_ps(client, h, key="pk_test_abcdefghijklmnop")
    assert resp.status_code == 422
    assert resp.json()["error"]["field_errors"][0]["field"] == "secret_key"
    assert _connect_ps(client, h, key=FLW_KEY).status_code == 422


def test_flutterwave_requires_hash(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{URL}/flutterwave/connect", json={"secret_key": FLW_KEY}, headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["field_errors"][0]["field"] == "secret_hash"


def test_bad_key_is_422_and_stores_nothing(client, db):
    _u, s, h = _member(db)
    resp = _connect_ps(client, h, key="sk_test_badbadba_dbad1234")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PAYMENT_KEY_REJECTED"
    assert "sk_test_badbadba_dbad1234" not in resp.text
    assert db.query(FinanceConnection).filter_by(startup_id=s.id).count() == 0


def test_provider_down_is_502(client, db):
    _u, _s, h = _member(db)
    resp = _connect_ps(client, h, key="sk_test_downdown_down1234")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"


def test_unknown_provider_404(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{URL}/stripe/connect", json={"secret_key": "x"}, headers=h)
    assert resp.status_code == 404


def test_encryption_unset_503_on_connect_but_get_works(client, db, monkeypatch):
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", None)
    _u, _s, h = _member(db)
    resp = _connect_ps(client, h)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "FINANCE_CONNECTIONS_NOT_CONFIGURED"
    got = client.get(URL, headers=h)
    assert got.status_code == 200
    assert got.json()["data"]["encryption_configured"] is False


def test_workspaces_are_isolated(client, db):
    _u, _s, h = _member(db)
    _connect_ps(client, h)
    _u2, _s2, h2 = _member(db)
    body = client.get(URL, headers=h2).json()["data"]
    assert all(p["connection"] is None for p in body["providers"])
    assert _connect_flw(client, h2).status_code == 200


@pytest.mark.parametrize("role", [MembershipRole.team_member, MembershipRole.accountant])
def test_finance_roles_read_but_cannot_connect_or_disconnect(client, db, role):
    _f, startup, fh = _member(db)
    _connect_ps(client, fh)
    _u, _s, h = _member(db, role=role, startup=startup)
    got = client.get(URL, headers=h)
    assert got.status_code == 200
    assert PS_KEY not in got.text
    assert _connect_ps(client, h).status_code == 403
    assert client.post(f"{URL}/paystack/disconnect", headers=h).status_code == 403


@pytest.mark.parametrize("role", ALL_NON_FINANCE)
def test_non_finance_roles_forbidden_everywhere(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert client.get(URL, headers=h).status_code == 403
    assert _connect_ps(client, h).status_code == 403
    assert client.post(f"{URL}/paystack/disconnect", headers=h).status_code == 403


def test_unauthenticated_401(client):
    assert client.get(URL).status_code == 401
    assert client.post(f"{URL}/paystack/connect", json={"secret_key": PS_KEY}).status_code == 401
    assert client.post(f"{URL}/paystack/disconnect").status_code == 401
