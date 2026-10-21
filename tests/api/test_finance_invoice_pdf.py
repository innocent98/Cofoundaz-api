import pytest

from app.db.models.enums import MembershipRole
from tests.api.test_finance import BASE, _member
from tests.api.test_invoices import _create, _payload


@pytest.mark.parametrize(
    "role", [MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant]
)
def test_finance_roles_download_pdf(client, db, role):
    _u, _s, h = _member(db, role=role)
    iid = _create(client, h)  # draft is allowed
    resp = client.get(f"{BASE}/invoices/{iid}/pdf", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    number = client.get(f"{BASE}/invoices/{iid}", headers=h).json()["data"]["number"]
    assert resp.headers["content-disposition"] == f'attachment; filename="invoice-{number}.pdf"'
    assert resp.content.startswith(b"%PDF-")
    assert number.encode() in resp.content


@pytest.mark.parametrize("role", [MembershipRole.mentor, MembershipRole.business_consultant])
def test_non_finance_roles_forbidden(client, db, role):
    _owner, startup, h = _member(db)
    iid = _create(client, h)
    _other, _s, oh = _member(db, role=role, startup=startup)
    resp = client.get(f"{BASE}/invoices/{iid}/pdf", headers=oh)
    assert resp.status_code == 403, resp.text


def test_cross_tenant_invoice_is_404(client, db):
    _u1, _s1, h1 = _member(db)
    _u2, _s2, h2 = _member(db)
    iid = _create(client, h1)
    resp = client.get(f"{BASE}/invoices/{iid}/pdf", headers=h2)
    assert resp.status_code == 404, resp.text


def test_non_ngn_create_is_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/invoices", json=_payload(currency="USD"), headers=h)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "NGN" in resp.text


def test_non_ngn_update_is_422(client, db):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    resp = client.patch(f"{BASE}/invoices/{iid}", json={"currency": "EUR"}, headers=h)
    assert resp.status_code == 422, resp.text


def test_response_exposes_reminder_fields(client, db):
    _u, _s, h = _member(db)
    iid = _create(client, h)
    data = client.get(f"{BASE}/invoices/{iid}", headers=h).json()["data"]
    assert data["last_reminded_at"] is None
    assert data["reminder_count"] == 0
    assert data["overdue_notified_at"] is None
