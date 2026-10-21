import logging
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.db.models.enums import InvoiceStatus, JobStatus, MembershipRole
from app.db.models.invoice import Invoice
from app.db.models.job import Job
from app.platform import email as email_mod
from app.services.finance.payments import crypto, flutterwave, paystack
from app.worker.handlers import invoice_email as handler
from tests.api.test_finance import BASE, _member
from tests.api.test_invoices import _create
from tests.services.finance.payments_helpers import make_connection

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "payments"
PS_KEY = "sk_test_fake_fake_fake1234"


class Provider:
    """Counts provider calls and answers from a scripted status."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.status = 200
        self.url = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"status": False, "message": "nope"})
        if request.url.host == "api.paystack.co":
            body = (FIX / "paystack_initialize.json").read_bytes()
            if self.url:
                body = body.replace(b"https://checkout.paystack.com/fake_access_code", self.url)
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=(FIX / "flutterwave_payments.json").read_bytes())


@pytest.fixture()
def provider(monkeypatch):
    p = Provider()
    factory = lambda: httpx.Client(transport=httpx.MockTransport(p.handler))  # noqa: E731
    monkeypatch.setattr(paystack, "http_client", factory)
    monkeypatch.setattr(flutterwave, "http_client", factory)
    monkeypatch.setattr(
        settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    return p


def _flw_connection(db, startup):
    conn = make_connection(db, startup, provider="flutterwave")
    conn.key_ciphertext = crypto.encrypt_secret("FLWSECK_TEST-fake_fake_fake5678-X")
    conn.secret_hash_ciphertext = crypto.encrypt_secret("placeholder-secret-hash")
    db.flush()
    return conn


def _send(client, h, **kw):
    iid = _create(client, h, **kw)
    return iid, client.post(f"{BASE}/invoices/{iid}/send", headers=h)


def test_send_with_active_connection_stores_pay_link(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)

    iid, resp = _send(client, h)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "sent"
    assert data["pay_url"] == "https://checkout.paystack.com/fake_access_code"
    assert data["pay_provider"] == "paystack"
    assert data["paid_via"] is None
    row = db.query(Invoice).filter_by(startup_id=s.id).one()
    assert row.pay_reference is not None and row.pay_reference.startswith(row.number + "-")
    assert len(row.pay_reference) <= 120
    assert len(provider.calls) == 1
    sent = provider.calls[0]
    assert sent.url.path == "/transaction/initialize"
    assert PS_KEY not in str(sent.url)
    body = sent.read().decode()
    assert row.pay_reference in body and '"amount":' in body.replace(" ", "")


def test_flutterwave_connection_creates_link(client, db, provider):
    _u, s, h = _member(db)
    _flw_connection(db, s)
    _iid, resp = _send(client, h)
    data = resp.json()["data"]
    assert data["pay_url"] == "https://checkout.flutterwave.com/v3/hosted/pay/placeholderlink"
    assert data["pay_provider"] == "flutterwave"


def test_plain_invoice_serialises_new_fields_as_null(client, db, provider):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    data = client.get(f"{BASE}/invoices/{iid}", headers=h).json()["data"]
    assert data["pay_url"] is None
    assert data["pay_provider"] is None
    assert data["paid_via"] is None


def test_send_without_connection_has_no_link_and_no_provider_call(client, db, provider):
    _u, _s, h = _member(db)
    _iid, resp = _send(client, h)
    assert resp.status_code == 200
    assert resp.json()["data"]["pay_url"] is None
    assert provider.calls == []


@pytest.mark.parametrize("status", ["needs_attention", "disconnected"])
def test_inactive_connection_gets_no_link(client, db, provider, status):
    _u, s, h = _member(db)
    make_connection(db, s, status=status)
    _iid, resp = _send(client, h)
    assert resp.json()["data"]["pay_url"] is None
    assert provider.calls == []


def test_non_ngn_invoice_gets_no_link(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)
    _iid, resp = _send(client, h, currency="USD")
    assert resp.status_code == 200
    assert resp.json()["data"]["pay_url"] is None
    assert provider.calls == []


@pytest.mark.parametrize("status", [401, 500])
def test_provider_failure_does_not_fail_send(client, db, provider, caplog, status):
    _u, s, h = _member(db)
    make_connection(db, s)
    provider.status = status
    with caplog.at_level(logging.DEBUG):
        _iid, resp = _send(client, h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "sent"
    assert data["pay_url"] is None
    row = db.query(Invoice).filter_by(startup_id=s.id).one()
    assert row.pay_reference is None and row.pay_provider is None
    assert PS_KEY not in caplog.text
    assert "Bearer" not in caplog.text
    # the email job is still queued
    assert db.query(Job).filter_by(type="email.invoice_sent", startup_id=s.id).count() == 1


def test_unconfigured_encryption_does_not_fail_send(client, db, provider, monkeypatch):
    _u, s, h = _member(db)
    make_connection(db, s)
    monkeypatch.setattr(settings, "FINANCE_CONNECTIONS_ENCRYPTION_KEY", None)
    _iid, resp = _send(client, h)
    assert resp.status_code == 200
    assert resp.json()["data"]["pay_url"] is None


def test_pay_link_retry_creates_link_after_failure(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)
    provider.status = 500
    iid, _ = _send(client, h)
    provider.status = 200

    resp = client.post(f"{BASE}/invoices/{iid}/pay-link", headers=h)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["pay_url"] == "https://checkout.paystack.com/fake_access_code"


def test_pay_link_reuses_existing_link(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)
    iid, _ = _send(client, h)
    before = len(provider.calls)
    resp = client.post(f"{BASE}/invoices/{iid}/pay-link", headers=h)
    assert resp.status_code == 200
    assert resp.json()["data"]["pay_url"] == "https://checkout.paystack.com/fake_access_code"
    assert len(provider.calls) == before


def test_pay_link_without_connection_is_422(client, db, provider):
    _u, _s, h = _member(db)
    iid, _ = _send(client, h)
    resp = client.post(f"{BASE}/invoices/{iid}/pay-link", headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NO_PAYMENT_CONNECTION"


def test_pay_link_provider_failure_is_502_and_changes_nothing(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)
    provider.status = 500
    iid, _ = _send(client, h)
    resp = client.post(f"{BASE}/invoices/{iid}/pay-link", headers=h)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"


def test_pay_link_on_draft_or_paid_invoice_is_422(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)
    draft = _create(client, h)
    assert client.post(f"{BASE}/invoices/{draft}/pay-link", headers=h).status_code == 422
    iid, _ = _send(client, h)
    client.post(f"{BASE}/invoices/{iid}/mark-paid", headers=h)
    assert client.post(f"{BASE}/invoices/{iid}/pay-link", headers=h).status_code == 422


def test_pay_link_403_for_non_finance_role_and_cross_tenant_404(client, db, provider):
    _u, s, h = _member(db)
    make_connection(db, s)
    iid, _ = _send(client, h)
    _m, _ms, hm = _member(db, role=MembershipRole.mentor, startup=s)
    assert client.post(f"{BASE}/invoices/{iid}/pay-link", headers=hm).status_code == 403
    _o, _os, ho = _member(db)
    assert client.post(f"{BASE}/invoices/{iid}/pay-link", headers=ho).status_code == 404


def test_manual_mark_paid_has_no_paid_via(client, db, provider):
    _u, _s, h = _member(db)
    iid, _ = _send(client, h)
    data = client.post(f"{BASE}/invoices/{iid}/mark-paid", headers=h).json()["data"]
    assert data["paid_via"] is None
    assert client.post(f"{BASE}/invoices/{iid}/mark-unpaid", headers=h).status_code == 200


# ------------------------------------------------------------------------------- email


def _emailed(db, monkeypatch, iid):
    sender = email_mod.ConsoleEmailSender()
    monkeypatch.setattr(handler, "get_email_sender", lambda: sender)
    job = db.query(Job).filter_by(type="email.invoice_sent").one()
    job.status = JobStatus.running
    handler.handle_invoice_email(db, job)
    assert job.payload["invoice_id"] == iid
    return sender.sent[0].html


def test_email_contains_escaped_pay_link(client, db, provider, monkeypatch):
    _u, s, h = _member(db)
    make_connection(db, s)
    provider.url = b'https://pay.example.test/c?a=1&b=\\"2\\"'  # JSON-escaped quotes
    iid, resp = _send(client, h)
    assert resp.json()["data"]["pay_url"] is not None
    html_out = _emailed(db, monkeypatch, iid)
    assert 'href="https://pay.example.test/c?a=1&amp;b=&quot;2&quot;"' in html_out
    assert "Pay now" in html_out
    assert 'b="2"' not in html_out


@pytest.mark.parametrize(
    "bad", ["javascript:alert(1)", "data:text/html,x", "//evil.test/x", "ftp://x.test", " http://x"]
)
def test_email_omits_non_http_links(db, bad):
    from tests.factories import create_startup, create_user
    from tests.services.finance.payments_helpers import make_invoice
    from tests.worker.test_invoice_email import _invoice  # noqa: F401

    u = create_user(db)
    s = create_startup(db, owner=u)
    inv = make_invoice(db, s)
    inv.pay_url = bad
    html_out = handler.render_invoice_email(inv, "Initech")
    assert "Pay now" not in html_out
    assert "href" not in html_out


def test_email_without_link_is_unchanged(db):
    from tests.factories import create_startup, create_user
    from tests.services.finance.payments_helpers import make_invoice

    u = create_user(db)
    s = create_startup(db, owner=u)
    inv = make_invoice(db, s)
    assert "href" not in handler.render_invoice_email(inv, "Initech")


def test_email_omits_link_once_invoice_is_paid(db):
    from tests.factories import create_startup, create_user
    from tests.services.finance.payments_helpers import make_invoice

    u = create_user(db)
    s = create_startup(db, owner=u)
    inv = make_invoice(db, s)
    inv.pay_url = "https://pay.example.test/x"
    inv.status = InvoiceStatus.paid
    assert "Pay now" not in handler.render_invoice_email(inv, "Initech")
