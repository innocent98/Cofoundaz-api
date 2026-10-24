import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.db.models.audit import AuditLog
from app.db.models.enums import MembershipRole, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.finance_connection import FinanceConnection
from app.db.models.job import Job
from app.services.finance.payments import mono
from tests.api.test_finance import BASE, _member

URL = f"{BASE}/integrations"
HOOK = "/api/v1/webhooks/mono"
SECRET = "test_sk_fake_platform_key"  # placeholder, not a real Mono key
WEBHOOK_SECRET = "placeholder-webhook-secret"
ACCOUNT_ID = "acct0123456789abcdef0001"
WHEN = "2026-10-01T09:30:00.000Z"

TXNS = [
    {
        "id": "m1",
        "narration": "PAYSTACK TRANSFER SETTLEMENT",
        "amount": 492500,
        "type": "credit",
        "date": WHEN,
    },
    {
        "id": "m2",
        "narration": "NIP transfer from ACME Ltd",
        "amount": 1500000,
        "type": "credit",
        "date": WHEN,
    },
    {"id": "m3", "narration": "POS purchase", "amount": 250000, "type": "debit", "date": WHEN},
    {"id": "bad1", "narration": "no amount", "type": "debit", "date": WHEN},
]


def _handler(request: httpx.Request) -> httpx.Response:
    key = request.headers.get("mono-sec-key", "")
    if "revoked" in key:
        return httpx.Response(401, json={"status": "failed"})
    path = request.url.path
    if path == "/v2/accounts/auth":
        code = json.loads(request.content)["code"]
        if code == "expired":
            return httpx.Response(400, json={"status": "failed"})
        if code == "down":
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"data": {"id": _handler.account_id}})
    if path.endswith("/transactions"):
        if _handler.sync_status != 200:
            return httpx.Response(_handler.sync_status, json={})
        return httpx.Response(200, json={"data": TXNS, "meta": {"next": None}})
    return httpx.Response(
        200,
        json={
            "data": {
                "account": {
                    "id": ACCOUNT_ID,
                    "name": "NovaPay Ltd",
                    "accountNumber": "0123456789",
                    "currency": "NGN",
                    "institution": {"name": "Test Bank"},
                }
            }
        },
    )


@pytest.fixture(autouse=True)
def _mono_env(monkeypatch):
    _handler.account_id = ACCOUNT_ID
    _handler.sync_status = 200
    monkeypatch.setattr(
        mono, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(_handler))
    )
    monkeypatch.setattr(settings, "MONO_SECRET_KEY", SECRET)
    monkeypatch.setattr(settings, "MONO_PUBLIC_KEY", "test_pk_placeholder")
    monkeypatch.setattr(settings, "MONO_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "MONO_ALLOW_LIVE", False)
    monkeypatch.setattr(settings, "FINANCE_SYNC_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


def _connect(client, h, code="good-code"):
    return client.post(f"{URL}/mono/connect", json={"code": code}, headers=h)


def _hook(client, event, *, secret=WEBHOOK_SECRET, account=ACCOUNT_ID, status="AVAILABLE"):
    body = json.dumps(
        {"event": event, "data": {"account": {"id": account}, "meta": {"data_status": status}}}
    )
    headers = {"content-type": "application/json"}
    if secret is not None:
        headers["mono-webhook-secret"] = secret
    return client.post(HOOK, content=body, headers=headers)


def _paystack_row(db, startup):
    from app.services.finance.payments import connections

    row = FinanceConnection(
        startup_id=startup.id,
        public_id="p" * 43,
        provider="paystack",
        status="active",
        key_ciphertext=connections.crypto.encrypt_secret("sk_test_x1234"),
        key_last4="1234",
        mode="test",
    )
    db.add(row)
    db.flush()


def test_connect_stores_account_encrypted_and_never_returns_it(client, db):
    _u, s, h = _member(db)
    resp = _connect(client, h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["provider"] == "mono" and data["mode"] == "test" and data["status"] == "active"
    assert data["account_name"] == "Test Bank · NovaPay Ltd"
    assert data["key_last4"] == "6789"
    assert data["webhook_url"].endswith("/webhooks/mono")
    assert ACCOUNT_ID not in resp.text and SECRET not in resp.text
    row = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    assert row.key_ciphertext.startswith("gAAAA") and ACCOUNT_ID not in row.key_ciphertext
    assert row.provider_account_hash == hashlib.sha256(ACCOUNT_ID.encode()).hexdigest()
    assert db.query(AuditLog).filter_by(action="finance.connection.connected").count() == 1


@pytest.mark.parametrize("role", [MembershipRole.team_member, MembershipRole.accountant])
def test_only_the_founder_can_connect_or_disconnect(client, db, role):
    _u, s, fh = _member(db)
    _u2, _s2, h = _member(db, role=role, startup=s)
    assert _connect(client, h).status_code == 403
    assert _connect(client, fh).status_code == 200
    assert client.post(f"{URL}/mono/disconnect", headers=h).status_code == 403
    # ...but they can read the status and sync
    listed = client.get(URL, headers=h).json()["data"]["providers"]
    assert next(p for p in listed if p["provider"] == "mono")["connection"]["status"] == "active"
    assert client.post(f"{URL}/mono/sync", headers=h).status_code == 200


@pytest.mark.parametrize(
    "role", [MembershipRole.mentor, MembershipRole.investor, MembershipRole.business_consultant]
)
def test_non_finance_roles_get_403(client, db, role):
    _u, s, h = _member(db, role=role)
    assert _connect(client, h).status_code == 403
    assert client.post(f"{URL}/mono/sync", headers=h).status_code == 403


def test_connect_unauthenticated_401(client):
    assert client.post(f"{URL}/mono/connect", json={"code": "x"}).status_code == 401


def test_connect_requires_server_config(client, db, monkeypatch):
    _u, _s, h = _member(db)
    monkeypatch.setattr(settings, "MONO_SECRET_KEY", None)
    assert _connect(client, h).json()["error"]["code"] == "MONO_NOT_CONFIGURED"
    monkeypatch.setattr(settings, "MONO_SECRET_KEY", SECRET)
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", None)
    assert _connect(client, h).status_code == 503


def test_live_keys_are_refused_unless_allowed(client, db, monkeypatch):
    _u, _s, h = _member(db)
    monkeypatch.setattr(settings, "MONO_SECRET_KEY", "live_sk_placeholder")
    resp = _connect(client, h)
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "MONO_LIVE_DISABLED"
    assert db.query(FinanceConnection).count() == 0
    monkeypatch.setattr(settings, "MONO_ALLOW_LIVE", True)
    resp = _connect(client, h)
    assert resp.status_code == 200 and resp.json()["data"]["mode"] == "live"


def test_connect_error_mapping(client, db, monkeypatch):
    _u, _s, h = _member(db)
    assert _connect(client, h, "expired").json()["error"]["code"] == "BANK_LINK_REJECTED"
    assert _connect(client, h, "down").status_code == 502
    monkeypatch.setattr(settings, "MONO_SECRET_KEY", "test_sk_revoked")
    assert _connect(client, h).status_code == 502
    assert db.query(FinanceConnection).count() == 0


def test_one_bank_account_serves_one_workspace(client, db):
    _u, _s, h1 = _member(db)
    _u2, _s2, h2 = _member(db)
    assert _connect(client, h1).status_code == 200
    resp = _connect(client, h2)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BANK_ACCOUNT_ALREADY_LINKED"


def test_reconnect_replaces_and_disconnect_clears_secret(client, db):
    _u, s, h = _member(db)
    first = _connect(client, h).json()["data"]
    again = _connect(client, h).json()["data"]
    assert again["id"] == first["id"]
    assert db.query(AuditLog).filter_by(action="finance.connection.replaced").count() == 1
    out = client.post(f"{URL}/mono/disconnect", headers=h)
    assert out.status_code == 200 and out.json()["data"]["status"] == "disconnected"
    row = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    assert row.key_ciphertext is None and row.provider_account_hash is None
    assert client.post(f"{URL}/mono/sync", headers=h).status_code == 404


def test_generic_connect_route_never_takes_a_pasted_mono_key(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{URL}/mono/connect", json={"secret_key": "test_sk_x"}, headers=h)
    assert resp.status_code == 422  # it wants a code, not a key


def test_list_shows_mono_with_connect_config(client, db):
    _u, _s, h = _member(db)
    mono_entry = next(
        p for p in client.get(URL, headers=h).json()["data"]["providers"] if p["provider"] == "mono"
    )
    assert mono_entry["kind"] == "bank" and mono_entry["connection"] is None
    assert mono_entry["connect_config"] == {
        "configured": True,
        "public_key": "test_pk_placeholder",
        "sandbox": True,
    }
    assert SECRET not in json.dumps(mono_entry)


def test_sync_imports_bank_lines_idempotently(client, db):
    _u, s, h = _member(db)
    _connect(client, h)
    first = client.post(f"{URL}/mono/sync", headers=h)
    assert first.status_code == 200, first.text
    summary = first.json()["data"]["summary"]
    assert summary["credits"] == 2 and summary["debits"] == 1 and summary["paystack_payouts"] == 0
    rows = {t.external_ref: t for t in db.query(Transaction).filter_by(startup_id=s.id)}
    assert set(rows) == {"m1", "m2", "m3"}  # the line with no amount is dropped
    assert rows["m3"].direction == TransactionDirection.outflow
    assert rows["m2"].direction == TransactionDirection.inflow
    assert all(r.source == TransactionSource.bank for r in rows.values())
    assert all(r.category is None for r in rows.values())  # no Paystack: nothing is tagged
    again = client.post(f"{URL}/mono/sync", headers=h).json()["data"]["summary"]
    assert again["credits"] == 0 and again["duplicates"] == 3
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 3


def test_paystack_payout_is_tagged_and_kept_out_of_revenue_and_cash(client, db):
    _u, s, h = _member(db)
    _paystack_row(db, s)
    _connect(client, h)
    summary = client.post(f"{URL}/mono/sync", headers=h).json()["data"]["summary"]
    assert summary["paystack_payouts"] == 1
    rows = {t.external_ref: t for t in db.query(Transaction).filter_by(startup_id=s.id)}
    assert rows["m1"].category == "Paystack payout"
    assert rows["m2"].category is None
    cash = client.get(f"{BASE}/cash-flow", headers=h).json()["data"]
    # 1,500,000 credit - 250,000 debit; the 492,500 payout is the same money as the charges
    assert cash["cash_on_hand"] == 1500000 - 250000
    this_month = [m for m in cash["by_month"] if m["inflow"] or m["outflow"]]
    assert sum(m["inflow"] for m in this_month) == 1500000
    # still visible in the ledger
    ledger = client.get(f"{BASE}/transactions", headers=h).json()["data"]["transactions"]
    assert any(t["category"] == "Paystack payout" for t in ledger)


def test_payout_can_be_recategorised_back_into_the_totals(client, db):
    _u, s, h = _member(db)
    _paystack_row(db, s)
    _connect(client, h)
    client.post(f"{URL}/mono/sync", headers=h)
    tx = db.query(Transaction).filter_by(startup_id=s.id, external_ref="m1").one()
    resp = client.patch(f"{BASE}/transactions/{tx.id}", json={"category": "Revenue"}, headers=h)
    assert resp.status_code == 200, resp.text
    assert client.get(f"{BASE}/cash-flow", headers=h).json()["data"]["cash_on_hand"] == (
        492500 + 1500000 - 250000
    )


def test_sync_when_mono_rejects_the_link_flags_the_connection(client, db):
    _u, s, h = _member(db)
    _connect(client, h)
    _handler.sync_status = 401
    resp = client.post(f"{URL}/mono/sync", headers=h)
    assert resp.status_code == 200
    conn = resp.json()["data"]["connection"]
    assert conn["status"] == "needs_attention"
    assert (
        conn["last_error"]
        == "Your bank link needs attention. Reconnect your bank to resume syncing."
    )


def test_sync_when_mono_is_down_keeps_the_link_and_reports(client, db):
    _u, _s, h = _member(db)
    _connect(client, h)
    _handler.sync_status = 503
    conn = client.post(f"{URL}/mono/sync", headers=h).json()["data"]["connection"]
    assert conn["status"] == "active" and "Mono" in conn["last_error"]


def test_bank_link_coexists_with_a_payment_provider(client, db):
    _u, s, h = _member(db)
    _paystack_row(db, s)
    assert _connect(client, h).status_code == 200
    providers = {
        p["provider"]: p["connection"]
        for p in client.get(URL, headers=h).json()["data"]["providers"]
    }
    assert providers["paystack"]["status"] == "active"
    assert providers["mono"]["status"] == "active"
    assert providers["flutterwave"] is None


@pytest.mark.parametrize("secret", [None, "wrong-secret", ""])
def test_webhook_rejects_missing_or_wrong_secret(client, db, secret):
    _u, _s, h = _member(db)
    _connect(client, h)
    resp = _hook(client, "mono.events.account_updated", secret=secret)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert db.query(Job).count() == 0


def test_webhook_fails_closed_when_no_secret_is_configured(client, db, monkeypatch):
    monkeypatch.setattr(settings, "MONO_WEBHOOK_SECRET", None)
    assert _hook(client, "mono.events.account_updated", secret="anything").status_code == 401
    monkeypatch.setattr(settings, "MONO_WEBHOOK_SECRET", "")
    assert _hook(client, "mono.events.account_updated", secret="").status_code == 401


def test_webhook_account_updated_queues_a_sync(client, db):
    _u, s, h = _member(db)
    conn_id = _connect(client, h).json()["data"]["id"]
    resp = _hook(client, "mono.events.account_updated")
    assert resp.status_code == 200 and resp.json()["data"] == {"received": True}
    job = db.query(Job).one()
    assert job.payload == {"connection_id": conn_id, "startup_id": str(s.id)}
    # data still processing: nothing to pull yet
    _hook(client, "mono.events.account_updated", status="PROCESSING")
    assert db.query(Job).count() == 1


def test_webhook_reauthorisation_flags_the_connection(client, db):
    _u, s, h = _member(db)
    _connect(client, h)
    assert _hook(client, "mono.events.reauthorisation_required").status_code == 200
    row = db.query(FinanceConnection).filter_by(startup_id=s.id).one()
    assert row.status == "needs_attention" and "re-authorise" in row.last_error


def test_webhook_unknown_account_or_event_is_acknowledged_and_ignored(client, db):
    _u, _s, h = _member(db)
    _connect(client, h)
    assert _hook(client, "mono.events.account_updated", account="nobody").status_code == 200
    assert _hook(client, "mono.events.something_else").status_code == 200
    assert db.query(Job).count() == 0


def test_webhook_bad_bodies(client):
    h = {"mono-webhook-secret": WEBHOOK_SECRET}
    assert client.post(HOOK, content=b"not json", headers=h).status_code == 400
    assert client.post(HOOK, content=b"[1]", headers=h).status_code == 400
    big = b"x" * (300 * 1024)
    assert client.post(HOOK, content=big, headers=h).status_code == 413


def test_disconnected_account_ignores_webhooks(client, db):
    _u, _s, h = _member(db)
    _connect(client, h)
    client.post(f"{URL}/mono/disconnect", headers=h)
    _hook(client, "mono.events.account_updated")
    assert db.query(Job).count() == 0


def test_verify_webhook_helper_handles_header_case():
    assert mono.MonoProvider.verify_webhook("s", headers={"Mono-Webhook-Secret": "s"})
    assert not mono.MonoProvider.verify_webhook("s", headers={"x": "s"})


def test_adapter_edge_cases():
    p = mono.MonoProvider()
    assert p._transaction("junk", "NGN") is None
    assert p._transaction({"id": "1", "amount": 1.5, "type": "credit", "date": WHEN}, "NGN") is None
    assert (
        p._transaction({"id": "1", "amount": True, "type": "credit", "date": WHEN}, "NGN") is None
    )
    ok = p._transaction({"id": "1", "amount": 100.0, "type": "debit", "date": "2026-10-01"}, "NGN")
    assert ok is not None and ok.amount_minor == 100 and ok.description == "Bank transaction"
    assert ok.occurred_at.tzinfo is not None and datetime.now(UTC) > ok.occurred_at
