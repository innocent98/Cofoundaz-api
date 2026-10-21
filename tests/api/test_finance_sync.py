import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.db.models.enums import MembershipRole, TransactionDirection
from app.db.models.finance import Transaction
from app.services.finance.payments import paystack
from tests.api.test_finance import BASE, _member
from tests.api.test_finance_integrations import ALL_NON_FINANCE, FIX, PS_KEY
from tests.services.finance.payments_helpers import make_connection

URL = f"{BASE}/integrations"


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    page = request.url.params.get("page", "1")
    if path == "/transaction":
        name = (
            "paystack_transactions_page1.json"
            if page == "1"
            else "paystack_transactions_page2.json"
        )
    elif path == "/refund":
        name = "paystack_refunds.json"
    else:
        name = "paystack_balance.json"
    return httpx.Response(200, content=(FIX / name).read_bytes())


@pytest.fixture(autouse=True)
def _provider_http(monkeypatch):
    monkeypatch.setattr(
        paystack, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(_handler))
    )
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "FINANCE_SYNC_MIN_INTERVAL_SECONDS", 0)


def _imported(db, startup_id):
    return {
        t.external_ref: t
        for t in db.query(Transaction).filter(
            Transaction.startup_id == startup_id, Transaction.connection_id.isnot(None)
        )
    }


@pytest.mark.parametrize(
    "role", [MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant]
)
def test_finance_roles_can_sync(client, db, role):
    _u, s, h = _member(db, role=role)
    make_connection(db, s)
    resp = client.post(f"{URL}/paystack/sync", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["summary"]["imported"] == 2  # ref_alpha, ref_beta (ref_failed is not a success)
    assert data["summary"]["fees"] == 1 and data["summary"]["refunds"] == 1
    assert data["connection"]["provider"] == "paystack"
    assert data["connection"]["last_sync_at"] is not None
    assert "key_ciphertext" not in resp.text and PS_KEY not in resp.text
    rows = _imported(db, s.id)
    assert rows["ref_alpha"].amount_minor == 500000
    assert rows["ref_alpha:fee"].direction == TransactionDirection.outflow


@pytest.mark.parametrize("role", ALL_NON_FINANCE)
def test_non_finance_roles_get_403(client, db, role):
    _u, s, h = _member(db, role=role)
    make_connection(db, s)
    assert client.post(f"{URL}/paystack/sync", headers=h).status_code == 403


def test_sync_404_without_active_connection(client, db):
    _u, s, h = _member(db)
    assert client.post(f"{URL}/paystack/sync", headers=h).status_code == 404
    make_connection(db, s, provider="flutterwave")
    assert client.post(f"{URL}/paystack/sync", headers=h).status_code == 404
    assert client.post(f"{URL}/nope/sync", headers=h).status_code == 404


def test_sync_unauthenticated_401(client):
    assert client.post(f"{URL}/paystack/sync").status_code == 401


def test_sync_too_soon_429(client, db, monkeypatch):
    monkeypatch.setattr(settings, "FINANCE_SYNC_MIN_INTERVAL_SECONDS", 3600)
    _u, s, h = _member(db)
    make_connection(db, s)
    assert client.post(f"{URL}/paystack/sync", headers=h).status_code == 200
    again = client.post(f"{URL}/paystack/sync", headers=h)
    assert again.status_code == 429
    assert again.json()["error"]["code"] == "SYNC_TOO_SOON"


def test_imported_rows_are_read_only_except_category(client, db):
    _u, s, h = _member(db)
    make_connection(db, s)
    client.post(f"{URL}/paystack/sync", headers=h)
    tid = str(_imported(db, s.id)["ref_alpha"].id)

    ok = client.patch(f"{BASE}/transactions/{tid}", json={"category": "Sales"}, headers=h)
    assert ok.status_code == 200 and ok.json()["data"]["category"] == "Sales"

    bad = client.patch(f"{BASE}/transactions/{tid}", json={"amount_minor": 1}, headers=h)
    assert bad.status_code == 422
    mixed = client.patch(
        f"{BASE}/transactions/{tid}", json={"category": "X", "description": "y"}, headers=h
    )
    assert mixed.status_code == 422
    assert "read-only except the category" in mixed.text

    deleted = client.delete(f"{BASE}/transactions/{tid}", headers=h)
    assert deleted.status_code == 422
    assert str(tid) in {str(t.id) for t in _imported(db, s.id).values()}


def test_manual_rows_still_editable(client, db):
    _u, s, h = _member(db)
    created = client.post(
        f"{BASE}/transactions",
        json={
            "date": "2026-03-10",
            "description": "x",
            "amount_minor": 5,
            "currency": "NGN",
            "direction": "out",
        },
        headers=h,
    )
    tid = created.json()["data"]["id"]
    assert (
        client.patch(f"{BASE}/transactions/{tid}", json={"amount_minor": 9}, headers=h).status_code
        == 200
    )
    assert client.delete(f"{BASE}/transactions/{tid}", headers=h).status_code in (200, 204)
