import hashlib
import hmac
import json
import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import find_route_handler
from app.db.models.enums import InvoiceStatus, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.finance_connection import FinanceConnection, ProviderEvent
from app.db.models.invoice import Invoice
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.main import app
from app.services.finance.payments import crypto, flutterwave
from tests.api.test_finance import BASE, _member
from tests.services.finance.payments_helpers import make_connection, make_invoice, make_workspace

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "payments"
PS_KEY = "sk_test_fake_fake_fake1234"
OTHER_KEY = "sk_test_some_otherwor_kspace99"
FLW_KEY = "FLWSECK_TEST-fake_fake_fake5678-X"
FLW_HASH = "placeholder-secret-hash"
HOOK = "/api/v1/webhooks/payments"


@pytest.fixture(autouse=True)
def _enc(monkeypatch):
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


class FlwApi:
    """Mock Flutterwave API for the re-verify call; records every request."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.respond: object = self.fixture("flutterwave_verify_success.json")

    @staticmethod
    def fixture(name: str, status: int = 200) -> httpx.Response:
        return httpx.Response(status, content=(FIX / name).read_bytes())

    def set_fixture(self, name: str, status: int = 200) -> None:
        self.respond = self.fixture(name, status)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if isinstance(self.respond, Exception):
            raise self.respond
        assert isinstance(self.respond, httpx.Response)
        return self.respond


@pytest.fixture(autouse=True)
def flw_api(monkeypatch) -> FlwApi:
    api = FlwApi()
    monkeypatch.setattr(
        flutterwave,
        "http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(api.handler)),
    )
    return api


def ps_sig(body: bytes, key: str = PS_KEY) -> str:
    return hmac.new(key.encode(), body, hashlib.sha512).hexdigest()


def ps_body(*, ref="inv_ref_1", amount=500000, currency="NGN", eid=1001, fees=7500) -> bytes:
    return json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": eid,
                "reference": ref,
                "amount": amount,
                "currency": currency,
                "status": "success",
                "paid_at": "2026-10-01T09:30:00.000Z",
                "fees": fees,
            },
        }
    ).encode()


def post_ps(client, conn, body: bytes, sig: str | None = None, key: str = PS_KEY):
    headers = {"content-type": "application/json"}
    headers["x-paystack-signature"] = ps_sig(body, key) if sig is None else sig
    return client.post(f"{HOOK}/paystack/{conn.public_id}", content=body, headers=headers)


def _flw_conn(db, startup) -> FinanceConnection:
    conn = make_connection(db, startup, provider="flutterwave")
    conn.key_ciphertext = crypto.encrypt_secret(FLW_KEY)
    conn.secret_hash_ciphertext = crypto.encrypt_secret(FLW_HASH)
    db.flush()
    return conn


def flw_body(*, ref="tx_alpha", amount=5000, currency="NGN", eid=2001, fee=75.5) -> bytes:
    return json.dumps(
        {
            "event": "charge.completed",
            "data": {
                "id": eid,
                "tx_ref": ref,
                "amount": amount,
                "currency": currency,
                "app_fee": fee,
                "status": "successful",
                "created_at": "2026-10-01T09:30:00.000Z",
            },
        }
    ).encode()


def post_flw(client, conn, body: bytes, verif: str | bytes | None = FLW_HASH):
    headers: dict[str, str | bytes] = {"content-type": "application/json"}
    if verif is not None:
        headers["verif-hash"] = verif
    return client.post(f"{HOOK}/flutterwave/{conn.public_id}", content=body, headers=headers)


def _inflows(db, startup_id):
    return (
        db.query(Transaction)
        .filter(
            Transaction.startup_id == startup_id,
            Transaction.direction == TransactionDirection.inflow,
        )
        .all()
    )


# --------------------------------------------------------------------------- happy paths


def test_valid_paystack_webhook_marks_invoice_paid_once(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    inv = make_invoice(db, s, reference="inv_ref_1", total=500000)

    resp = post_ps(client, conn, ps_body())

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"received": True}
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid
    assert inv.paid_via == "paystack"
    inflows = _inflows(db, s.id)
    assert len(inflows) == 1
    assert inflows[0].source == TransactionSource.invoice
    assert inflows[0].amount_minor == 500000
    ev = db.query(ProviderEvent).filter_by(connection_id=conn.id).one()
    assert ev.event_id == "charge.success:1001"
    assert ev.outcome == "invoice_paid"


def test_duplicate_event_is_acked_and_moves_no_money(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    make_invoice(db, s, reference="inv_ref_1", total=500000)
    body = ps_body()

    first = post_ps(client, conn, body)
    second = post_ps(client, conn, body)

    assert first.json()["data"] == {"received": True}
    assert second.status_code == 200
    assert second.json()["data"] == {"received": True, "duplicate": True}
    assert len(_inflows(db, s.id)) == 1
    assert db.query(ProviderEvent).filter_by(connection_id=conn.id).count() == 1


def test_duplicate_does_not_poison_session(client, db):
    """A duplicate insert inside the savepoint must leave the session usable (no 500)."""
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    make_invoice(db, s, reference="inv_ref_1")
    body = ps_body()
    post_ps(client, conn, body)
    assert post_ps(client, conn, body).status_code == 200
    assert post_ps(client, conn, body).status_code == 200
    assert db.query(ProviderEvent).count() == 1


def test_amount_mismatch_leaves_invoice_sent_and_records_ignored(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    inv = make_invoice(db, s, reference="inv_ref_1", total=500000)

    resp = post_ps(client, conn, ps_body(amount=100000))

    assert resp.status_code == 200
    assert resp.json()["data"] == {"received": True}
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent
    assert inv.paid_via is None
    assert _inflows(db, s.id) == []
    ev = db.query(ProviderEvent).filter_by(connection_id=conn.id).one()
    assert ev.outcome == "ignored"


def test_currency_mismatch_leaves_invoice_sent(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    inv = make_invoice(db, s, reference="inv_ref_1", total=500000)
    resp = post_ps(client, conn, ps_body(currency="USD"))
    assert resp.status_code == 200
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent
    assert db.query(ProviderEvent).one().outcome == "ignored"


def test_charge_without_invoice_imports_one_inflow_and_fee(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)

    resp = post_ps(client, conn, ps_body(ref="ref_unmatched", amount=250000, fees=3750))

    assert resp.status_code == 200
    rows = db.query(Transaction).filter_by(startup_id=s.id, connection_id=conn.id).all()
    by_ref = {r.external_ref: r for r in rows}
    assert set(by_ref) == {"ref_unmatched", "ref_unmatched:fee"}
    assert by_ref["ref_unmatched"].amount_minor == 250000
    assert by_ref["ref_unmatched"].direction == TransactionDirection.inflow
    assert by_ref["ref_unmatched"].source == TransactionSource.paystack
    assert by_ref["ref_unmatched:fee"].amount_minor == 3750
    assert by_ref["ref_unmatched:fee"].category == "Payment fees"
    assert db.query(ProviderEvent).one().outcome == "imported"


def test_refund_event_is_recorded_by_refund_id(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    body = json.dumps(
        {
            "event": "refund.processed",
            "data": {
                "id": 7001,
                "transaction_reference": "never_imported",
                "amount": 200000,
                "currency": "NGN",
                "status": "processed",
                "refunded_at": "2026-10-03T08:00:00.000Z",
            },
        }
    ).encode()

    assert post_ps(client, conn, body).status_code == 200
    assert post_ps(client, conn, body).json()["data"]["duplicate"] is True

    row = db.query(Transaction).filter_by(startup_id=s.id, external_ref="7001").one()
    assert row.direction == TransactionDirection.outflow
    assert row.category == "Refunds"
    assert row.amount_minor == 200000


def test_unrelated_event_is_acked_and_ignored(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    body = json.dumps({"event": "customeridentification.success", "data": {"id": 5}}).encode()
    resp = post_ps(client, conn, body)
    assert resp.status_code == 200
    assert db.query(ProviderEvent).one().outcome == "ignored"
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 0


def test_events_without_a_data_id_do_not_collide(client, db):
    """Two different id-less charges must not be deduped against each other."""
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    for ref in ("ref_one", "ref_two"):
        body = json.dumps(
            {
                "event": "charge.success",
                "data": {"reference": ref, "amount": 1000, "currency": "NGN"},
            }
        ).encode()
        assert post_ps(client, conn, body).json()["data"] == {"received": True}
    assert db.query(Transaction).filter_by(startup_id=s.id, connection_id=conn.id).count() == 2


def test_manual_mark_paid_after_webhook_is_a_noop_and_mark_unpaid_is_blocked(client, db):
    _u, s, h = _member(db)
    conn = make_connection(db, s)
    inv = make_invoice(db, s, reference="inv_ref_1")
    post_ps(client, conn, ps_body())

    again = client.post(f"{BASE}/invoices/{inv.id}/mark-paid", headers=h)
    assert again.status_code == 200
    assert len(_inflows(db, s.id)) == 1

    unpaid = client.post(f"{BASE}/invoices/{inv.id}/mark-unpaid", headers=h)
    assert unpaid.status_code == 422
    err = unpaid.json()["error"]
    assert err["field_errors"][0]["field"] == "status"
    assert (
        err["field_errors"][0]["message"]
        == "This invoice was paid through Paystack and cannot be marked unpaid."
    )
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid


# --------------------------------------------------------------------------- signature checks


def test_signature_from_another_workspaces_key_is_401(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    make_invoice(db, s, reference="inv_ref_1")
    body = ps_body()

    resp = post_ps(client, conn, body, key=OTHER_KEY)

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert db.query(ProviderEvent).count() == 0
    assert _inflows(db, s.id) == []


@pytest.mark.parametrize("sig", ["", "   ", "deadbeef", "A" * 128])
def test_empty_or_garbage_signature_is_401(client, db, sig):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    assert post_ps(client, conn, ps_body(), sig=sig).status_code == 401


def test_missing_signature_header_is_401(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    resp = client.post(f"{HOOK}/paystack/{conn.public_id}", content=ps_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_SIGNATURE"


def test_non_ascii_signature_header_is_401_not_500(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    resp = client.post(
        f"{HOOK}/paystack/{conn.public_id}",
        content=ps_body(),
        headers={"x-paystack-signature": "caf\xe9".encode("latin-1")},
    )
    assert resp.status_code == 401


def test_tampered_body_is_401(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    make_invoice(db, s, reference="inv_ref_1")
    signed = ps_body(amount=500000)
    tampered = ps_body(amount=1)
    resp = post_ps(client, conn, tampered, sig=ps_sig(signed))
    assert resp.status_code == 401


def test_signature_is_checked_before_json_parsing(client, db):
    """Garbage body + bad signature is 401 (never a 400 that would reveal parsing happened)."""
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    assert post_ps(client, conn, b"{not json", sig="bad").status_code == 401


def test_validly_signed_but_malformed_json_is_400(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    body = b"{not json"
    resp = post_ps(client, conn, body)
    assert resp.status_code == 400
    assert db.query(ProviderEvent).count() == 0


def test_parse_is_not_called_for_unverified_requests(client, db, monkeypatch):
    from app.services.finance.payments import paystack

    def boom(self, raw_body):
        raise AssertionError("parse_webhook called before the signature was verified")

    monkeypatch.setattr(paystack.PaystackProvider, "parse_webhook", boom)
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    assert post_ps(client, conn, ps_body(), sig="nope").status_code == 401


def test_flutterwave_valid_hash_marks_invoice_paid(client, db):
    _u, s, _h = _member(db)
    conn = _flw_conn(db, s)
    inv = make_invoice(db, s, reference="tx_alpha", total=500000)
    inv.pay_provider = "flutterwave"
    db.flush()

    resp = post_flw(client, conn, flw_body())

    assert resp.status_code == 200, resp.text
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid
    assert inv.paid_via == "flutterwave"
    assert len(_inflows(db, s.id)) == 1


@pytest.mark.parametrize("verif", ["wrong-hash", "", None, "caf\xe9".encode("latin-1")])
def test_flutterwave_bad_or_missing_hash_is_401(client, db, verif):
    _u, s, _h = _member(db)
    conn = _flw_conn(db, s)
    make_invoice(db, s, reference="tx_alpha")
    resp = post_flw(client, conn, flw_body(), verif=verif)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert _inflows(db, s.id) == []


def test_flutterwave_connection_without_saved_hash_is_401(client, db):
    _u, s, _h = _member(db)
    conn = _flw_conn(db, s)
    conn.secret_hash_ciphertext = None
    db.flush()
    assert post_flw(client, conn, flw_body(), verif="anything").status_code == 401


def test_flutterwave_amount_is_compared_in_kobo(client, db):
    _u, s, _h = _member(db)
    conn = _flw_conn(db, s)
    inv = make_invoice(db, s, reference="tx_alpha", total=500000)
    # 4999.99 NGN != 5000.00 NGN
    resp = post_flw(client, conn, flw_body(amount=4999.99))
    assert resp.status_code == 200
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent


# --------------------------------------------------------------------------- routing / config


def test_unknown_public_id_is_404(client, db):
    body = ps_body()
    resp = client.post(
        f"{HOOK}/paystack/{uuid.uuid4().hex}",
        content=body,
        headers={"x-paystack-signature": ps_sig(body)},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_provider_is_404(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    resp = client.post(f"{HOOK}/stripe/{conn.public_id}", content=ps_body())
    assert resp.status_code == 404


def test_public_id_under_the_wrong_provider_is_404(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)  # paystack
    body = flw_body()
    resp = client.post(
        f"{HOOK}/flutterwave/{conn.public_id}", content=body, headers={"verif-hash": FLW_HASH}
    )
    assert resp.status_code == 404


def test_disconnected_connection_is_404(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s, status="disconnected")
    assert post_ps(client, conn, ps_body()).status_code == 404


def test_needs_attention_connection_ignores_even_validly_signed_events(client, db):
    # The key was rejected by the provider (maybe revoked or leaked): do not pay invoices on it.
    _u, s, _h = _member(db)
    conn = make_connection(db, s, status="needs_attention")
    inv = make_invoice(db, s, reference="inv_ref_1")
    assert post_ps(client, conn, ps_body()).status_code == 404
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent


def test_unconfigured_encryption_key_is_503(client, db, monkeypatch):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", None)
    resp = post_ps(client, conn, ps_body())
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "FINANCE_CONNECTIONS_NOT_CONFIGURED"


def test_body_over_256kb_is_413(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    big = b"x" * (256 * 1024 + 1)
    resp = post_ps(client, conn, big, sig="irrelevant")
    assert resp.status_code == 413


def test_body_at_the_cap_reaches_signature_check(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    resp = post_ps(client, conn, b"x" * (256 * 1024), sig="irrelevant")
    assert resp.status_code == 401


def test_chunked_body_over_cap_is_413_without_content_length(client, db):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)

    def chunks() -> Iterator[bytes]:
        for _ in range(5):
            yield b"y" * (64 * 1024)

    resp = client.post(
        f"{HOOK}/paystack/{conn.public_id}",
        content=chunks(),
        headers={"x-paystack-signature": "irrelevant"},
    )
    assert resp.status_code == 413


def test_route_is_public_and_not_exempt_from_the_rate_limiter():
    """SlowAPI must resolve this included-router route to a real endpoint (else it is exempt)."""
    path = f"{HOOK}/paystack/abc"
    scope = {"type": "http", "method": "POST", "path": path, "root_path": "", "headers": []}
    handler = find_route_handler(app.routes, scope)
    assert handler is not None
    assert handler.__name__ == "receive_payment_webhook"


def test_nothing_secret_is_logged(client, db, caplog):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    make_invoice(db, s, reference="inv_ref_1")
    good = ps_body()
    bad_sig = "f" * 128
    with caplog.at_level(logging.DEBUG):
        post_ps(client, conn, good)
        post_ps(client, conn, ps_body(eid=2), sig=bad_sig)
        post_ps(client, conn, b"{secret-body-marker", sig=bad_sig)
    text = caplog.text
    assert PS_KEY not in text
    assert ps_sig(good) not in text
    assert bad_sig not in text
    assert "secret-body-marker" not in text
    assert "inv_ref_1" not in text


# --------------------------------------------------------------------------- real sessions


@contextmanager
def _committed_world(engine, monkeypatch) -> Iterator[dict]:
    """Committed rows + per-request real sessions (so locks and rollbacks are real)."""
    setup = Session(bind=engine)
    user, startup = make_workspace(setup)
    conn = make_connection(setup, startup)
    inv = make_invoice(setup, startup, reference="inv_ref_1", total=500000)
    setup.commit()
    world = {"startup_id": startup.id, "conn_id": conn.id, "public_id": conn.public_id}
    world["invoice_id"] = inv.id

    def override() -> Iterator[Session]:
        s = Session(bind=engine)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.state.limiter.enabled = False
    try:
        yield world
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter.enabled = True
        cleanup = Session(bind=engine)
        cleanup.query(Startup).filter_by(id=world["startup_id"]).delete()
        cleanup.query(User).filter_by(id=user.id).delete()
        cleanup.commit()
        cleanup.close()
        setup.close()


def _count_inflows(engine, startup_id) -> int:
    s = Session(bind=engine)
    try:
        return len(_inflows(s, startup_id))
    finally:
        s.close()


def _race(bodies: list[bytes], public_id: str) -> list[int]:
    barrier = threading.Barrier(len(bodies))
    out: list[int] = [0] * len(bodies)

    def go(i: int) -> None:
        c = TestClient(app, raise_server_exceptions=False)
        barrier.wait()
        resp = c.post(
            f"{HOOK}/paystack/{public_id}",
            content=bodies[i],
            headers={"x-paystack-signature": ps_sig(bodies[i])},
        )
        out[i] = resp.status_code

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(bodies))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return out


def test_concurrent_identical_deliveries_yield_one_inflow(engine, monkeypatch):
    with _committed_world(engine, monkeypatch) as w:
        statuses = _race([ps_body()] * 4, w["public_id"])
        assert statuses == [200, 200, 200, 200]
        assert _count_inflows(engine, w["startup_id"]) == 1
        s = Session(bind=engine)
        try:
            assert s.query(ProviderEvent).filter_by(connection_id=w["conn_id"]).count() == 1
            assert s.get(Invoice, w["invoice_id"]).status == InvoiceStatus.paid
        finally:
            s.close()


def test_concurrent_distinct_events_for_one_invoice_yield_one_inflow(engine, monkeypatch):
    """Different event ids, same invoice: the invoice row lock must serialise them."""
    with _committed_world(engine, monkeypatch) as w:
        bodies = [ps_body(eid=9000 + i) for i in range(4)]
        statuses = _race(bodies, w["public_id"])
        assert statuses == [200, 200, 200, 200]
        assert _count_inflows(engine, w["startup_id"]) == 1


def test_crash_after_event_insert_leaves_no_seen_marker_and_retry_succeeds(engine, monkeypatch):
    from app.services.finance import invoices as invoices_svc

    with _committed_world(engine, monkeypatch) as w:
        real = invoices_svc.mark_paid_via_provider

        def boom(*a, **k):
            raise RuntimeError("simulated crash mid-processing")

        monkeypatch.setattr(invoices_svc, "mark_paid_via_provider", boom)
        c = TestClient(app, raise_server_exceptions=False)
        body = ps_body()
        crashed = c.post(
            f"{HOOK}/paystack/{w['public_id']}",
            content=body,
            headers={"x-paystack-signature": ps_sig(body)},
        )
        assert crashed.status_code == 500

        s = Session(bind=engine)
        try:
            assert s.query(ProviderEvent).filter_by(connection_id=w["conn_id"]).count() == 0
        finally:
            s.close()
        assert _count_inflows(engine, w["startup_id"]) == 0

        monkeypatch.setattr(invoices_svc, "mark_paid_via_provider", real)
        retry = c.post(
            f"{HOOK}/paystack/{w['public_id']}",
            content=body,
            headers={"x-paystack-signature": ps_sig(body)},
        )
        assert retry.status_code == 200
        assert retry.json()["data"] == {"received": True}
        assert _count_inflows(engine, w["startup_id"]) == 1


# --------------------------------------------------- Flutterwave: re-verify with the API


def _flw_world(db):
    _u, s, _h = _member(db)
    conn = _flw_conn(db, s)
    inv = make_invoice(db, s, reference="tx_alpha", total=500000)
    inv.pay_provider = "flutterwave"
    db.flush()
    return s, conn, inv


def _events(db, conn):
    return db.query(ProviderEvent).filter_by(connection_id=conn.id).all()


def test_flutterwave_reverify_calls_the_verify_endpoint_with_the_bearer_key(client, db, flw_api):
    s, conn, inv = _flw_world(db)
    assert post_flw(client, conn, flw_body()).status_code == 200
    assert len(flw_api.calls) == 1
    req = flw_api.calls[0]
    assert req.method == "GET" and req.url.path == "/v3/transactions/2001/verify"
    assert req.headers["authorization"] == f"Bearer {FLW_KEY}"
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid
    assert len(_inflows(db, s.id)) == 1


@pytest.mark.parametrize(
    "fixture,status",
    [
        ("flutterwave_verify_failed.json", 200),
        ("flutterwave_verify_amount_mismatch.json", 200),
        ("flutterwave_verify_currency_mismatch.json", 200),
        ("flutterwave_verify_ref_mismatch.json", 200),
        ("flutterwave_verify_404.json", 404),
        ("flutterwave_verify_404.json", 400),
    ],
)
def test_flutterwave_forged_event_the_api_disagrees_with_is_unverified(
    client, db, flw_api, fixture, status
):
    s, conn, inv = _flw_world(db)
    flw_api.set_fixture(fixture, status)
    resp = post_flw(client, conn, flw_body())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"received": True}
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent
    assert _inflows(db, s.id) == []
    assert [e.outcome for e in _events(db, conn)] == ["unverified"]


def test_flutterwave_unverified_event_does_not_block_the_real_delivery(client, db, flw_api):
    s, conn, inv = _flw_world(db)
    flw_api.set_fixture("flutterwave_verify_failed.json")
    assert post_flw(client, conn, flw_body()).status_code == 200  # forged first
    flw_api.set_fixture("flutterwave_verify_success.json")
    resp = post_flw(client, conn, flw_body())  # the real one, same transaction id
    assert resp.json()["data"] == {"received": True}
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid
    assert len(_inflows(db, s.id)) == 1
    assert [e.outcome for e in _events(db, conn)] == ["invoice_paid"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("slow"),
        "500",
    ],
)
def test_flutterwave_provider_down_is_502_with_no_event_row_and_retry_works(
    client, db, flw_api, failure
):
    s, conn, inv = _flw_world(db)
    if isinstance(failure, Exception):
        flw_api.respond = failure
    else:
        flw_api.set_fixture("flutterwave_500.json", 500)
    resp = post_flw(client, conn, flw_body())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"
    assert _events(db, conn) == []
    assert _inflows(db, s.id) == []
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent

    flw_api.set_fixture("flutterwave_verify_success.json")
    retry = post_flw(client, conn, flw_body())
    assert retry.status_code == 200
    assert retry.json()["data"] == {"received": True}
    assert len(_inflows(db, s.id)) == 1
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid


def test_flutterwave_our_key_rejected_by_verify_is_unverified_and_needs_attention(
    client, db, flw_api
):
    s, conn, inv = _flw_world(db)
    flw_api.set_fixture("flutterwave_401.json", 401)
    resp = post_flw(client, conn, flw_body())
    assert resp.status_code == 200
    db.refresh(inv)
    db.refresh(conn)
    assert inv.status == InvoiceStatus.sent
    assert _inflows(db, s.id) == []
    assert [e.outcome for e in _events(db, conn)] == ["unverified"]
    assert conn.status == "needs_attention"


def test_flutterwave_ledger_uses_the_api_values_not_the_webhooks(client, db, flw_api):
    s, conn, inv = _flw_world(db)
    api = json.loads((FIX / "flutterwave_verify_success.json").read_text())
    api["data"]["app_fee"] = 10.25
    api["data"]["created_at"] = "2026-10-02T11:00:00.000Z"
    flw_api.respond = httpx.Response(200, json=api)
    # same amount/currency/ref, but the webhook claims a different fee and date
    body = json.loads(flw_body(fee=99.99))
    body["data"]["created_at"] = "2026-09-01T00:00:00.000Z"
    resp = post_flw(client, conn, json.dumps(body).encode())
    assert resp.status_code == 200
    rows = _inflows(db, s.id)
    assert len(rows) == 1 and rows[0].amount_minor == 500000
    assert rows[0].date.isoformat() == "2026-10-02"
    fees = (
        db.query(Transaction)
        .filter(Transaction.startup_id == s.id, Transaction.direction == TransactionDirection.outflow)
        .all()
    )
    assert [f.amount_minor for f in fees] == [1025]


def test_flutterwave_webhook_amount_differing_from_the_api_is_unverified(client, db, flw_api):
    s, conn, inv = _flw_world(db)
    resp = post_flw(client, conn, flw_body(amount=1000))  # API says 5000
    assert resp.status_code == 200
    db.refresh(inv)
    assert inv.status == InvoiceStatus.sent
    assert _inflows(db, s.id) == []
    assert [e.outcome for e in _events(db, conn)] == ["unverified"]


def test_flutterwave_non_invoice_charge_import_also_needs_api_confirmation(client, db, flw_api):
    _u, s, _h = _member(db)
    conn = _flw_conn(db, s)  # no invoice carries this reference
    flw_api.set_fixture("flutterwave_verify_failed.json")
    assert post_flw(client, conn, flw_body()).status_code == 200
    assert _inflows(db, s.id) == []
    assert [e.outcome for e in _events(db, conn)] == ["unverified"]

    flw_api.set_fixture("flutterwave_verify_success.json")
    assert post_flw(client, conn, flw_body()).status_code == 200
    rows = _inflows(db, s.id)
    assert len(rows) == 1 and rows[0].amount_minor == 500000


def test_flutterwave_duplicate_delivery_makes_no_second_verify_call(client, db, flw_api):
    _s, conn, _inv = _flw_world(db)
    post_flw(client, conn, flw_body())
    dup = post_flw(client, conn, flw_body())
    assert dup.json()["data"] == {"received": True, "duplicate": True}
    assert len(flw_api.calls) == 1


def test_flutterwave_unrelated_event_makes_no_verify_call(client, db, flw_api):
    _s, conn, _inv = _flw_world(db)
    body = json.dumps({"event": "transfer.completed", "data": {"id": 5}}).encode()
    assert post_flw(client, conn, body).status_code == 200
    assert flw_api.calls == []


def test_paystack_path_makes_no_verify_call(client, db, flw_api):
    _u, s, _h = _member(db)
    conn = make_connection(db, s)
    inv = make_invoice(db, s, reference="inv_ref_1")
    assert post_ps(client, conn, ps_body()).status_code == 200
    db.refresh(inv)
    assert inv.status == InvoiceStatus.paid
    assert flw_api.calls == []
